"""Sprava uzivatelskych uctu. Cely router je jen pro spravce - viz main.py."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData

from app import auth
from app.chyby_poli import StavFormulare, stav_s_chybami
from app.db import zamkni_zapis
from app.formulare import UzivatelForm, formular_s_chybami, over_delky
from app.models import (
    NAZVY_ROLI,
    ROLE,
    STAV_EMAILU_LOKALNE,
    UCEL_RESET_HESLA,
    Uzivatel,
)
from app.routes.spolecne import (
    MAX_ID,
    Databaze,
    nacti_formular,
    prazdne_na_none,
    text_z_formulare,
    uz_smazano,
)
from app.sablony import oznam, strana, strana_s_chybou, zpet
from app.services import cteni_ai, posta, tarif, tokeny
from app.services.uzivatele import (
    ChybaPole,
    docasne_heslo,
    email_zmenen,
    nastav_heslo,
    over_email,
    posledni_pozvanky,
    posli_odkaz,
    posli_pozvanku,
    smaz_ucet,
    zaloz_ucet,
    zkontroluj_smazani,
    zkontroluj_zmenu,
)

router = APIRouter()

# Posuvniky limitu cteni AI: (pole, popisek, jednotka). Meze jsou v cteni_ai.
LIMITY_AI = (
    ("limit_cteni_den", "Čtení za den", "×"),
    ("limit_kc_den", "Útrata za den", "Kč"),
    ("limit_kc_rok", "Útrata za rok", "Kč"),
)


def _limit_z_formulare(text: str | None, nejmene: int, nejvice: int) -> int | None:
    """Cislo z posuvniku oriznute do mezi, nebo None, kdyz to cislo neni
    (nic se pak nemeni). Server mezim veri vic nez HTML: pole jde poslat i rucne."""
    try:
        hodnota = int((text or "").strip())
    except ValueError:
        return None
    return max(nejmene, min(nejvice, hodnota))


def _strana_prehledu(
    request: Request, sezeni: Session, stav: StavFormulare | None = None
) -> HTMLResponse:
    """Přehled uživatelů; se `stav` je to znovu vykreslený formulář „Přidat“ s chybou u pole."""
    uzivatele = list(sezeni.scalars(select(Uzivatel).order_by(Uzivatel.jmeno)))
    ids = [u.id for u in uzivatele]
    neaktivni = [u.id for u in uzivatele if not u.aktivni]
    kontext = {
        "uzivatele": uzivatele,
        "role_nazvy": NAZVY_ROLI,
        "role_volby": ROLE,
        "cteni_ai": tarif.cteni_ai_uzivatelu(sezeni, ids),
        "vyuziti": cteni_ai.vyuziti_vice(sezeni, ids),
        "limity_ai": [
            (pole, popisek, jednotka, *cteni_ai.MEZE_LIMITU_AI[pole])
            for pole, popisek, jednotka in LIMITY_AI
        ],
        "posledni_pozvanky": posledni_pozvanky(sezeni, neaktivni),
    }
    if stav is not None:
        return strana_s_chybou(request, "uzivatele.html", stav, **kontext)
    return strana(request, "uzivatele.html", **kontext)


@router.get("/uzivatele", response_class=HTMLResponse)
async def prehled(request: Request, sezeni: Databaze):
    return _strana_prehledu(request, sezeni)


@router.post("/uzivatele/pridat")
def pridat(
    request: Request,
    data: Annotated[UzivatelForm | StavFormulare, Depends(formular_s_chybami(UzivatelForm))],
    sezeni: Databaze,
    aktualni: Uzivatel = Depends(auth.vyzaduj_spravce),
):
    # Chyba formulare: stranka znovu, s chybou u pole a tim, co clovek napsal.
    if isinstance(data, StavFormulare):
        return _strana_prehledu(request, sezeni, data)
    try:
        novy, heslo, zaznam = zaloz_ucet(
            sezeni,
            login=data.login,
            jmeno=data.jmeno,
            email=data.email,
            role=data.role,
            zpusob="pozvanka" if data.poslat_pozvanku else "docasne_heslo",
            pozval=aktualni,
        )
    except ChybaPole as chyba:
        sezeni.rollback()
        return _strana_prehledu(
            request, sezeni, stav_s_chybami(request, **{chyba.pole: str(chyba)})
        )
    except ValueError as chyba:
        sezeni.rollback()
        oznam(request, str(chyba), "chyba")
        return zpet("/uzivatele")

    sezeni.commit()
    if zaznam is not None:
        if posta.neodeslano(zaznam):
            oznam(
                request,
                f"Účet založen, ale pozvánka se neodeslala. {posta.hlaska_neodeslano(zaznam)}",
                "chyba",
            )
        elif zaznam.stav == STAV_EMAILU_LOKALNE:
            oznam(
                request,
                f"Účet založen, ale pozvánka se neodeslala. {posta.HLASKA_LOKALNE}",
                "pozor",
            )
        else:
            oznam(request, "Pozvánka odeslána.")
        return zpet("/uzivatele")

    assert heslo is not None  # zpusob="docasne_heslo" ho vzdy vraci
    # Docasne heslo se ukazuje jen tady, jednou - nikdy nechodi do session
    # (flash hlasky), aby nezustalo v cookie ani v historii prohlizece.
    return strana(request, "uzivatel_heslo.html", ucet=novy, heslo=heslo)


@router.post("/uzivatele/ulozit")
async def ulozit(
    request: Request,
    sezeni: Databaze,
    aktualni: Uzivatel = Depends(auth.vyzaduj_spravce),
):
    """Hromadne ulozeni jmena, e-mailu a role. Vlastni radek roli nenabizi -
    sablona pro nej misto <select> vypisuje jen text - takze zmena role
    sam sobe se sem vubec nedostane. Server si to i tak overuje."""
    formular = await nacti_formular(request)
    # Zbytek posila e-maily (SMTP trva sekundy), proto bezi ve vlakne - v `async`
    # route by na ten cas stal cely server.
    return await run_in_threadpool(_uloz_uzivatele, request, sezeni, aktualni, formular)


def _rozdel_emaily(
    vsichni: list[Uzivatel], formular: FormData
) -> tuple[dict[int, str | None], list[str]]:
    """Z formulare vybere e-mail pro kazdy ucet a vrati ho spolu s chybami.

    E-maily se kontroluji napred, pres cely formular najednou - je to jeden
    <table> formular, takze prijde i nezmenena hodnota u kazdeho radku.
    Bez tyhle kontroly by kolize s unikatnim indexem shodila cely POST
    neosetrenou vyjimkou uprostred hromadneho ukladani. Zamerne se tu
    nepouziva `uzivatele.email_je_volny` - kdyby se volala uvnitr
    smycky po kazde zmene `uzivatel.email`, autoflush by rozpracovanou
    zmenu z predchoziho radku zapsal driv, nez by se stihla zkontrolovat
    dalsi kolize, a shodil by presne ten pad, ktere ma tenhle slovnik
    predejit. Vadny nebo zdvojeny e-mail se nahradi stavajicim."""
    chyby: list[str] = []
    # Prohlizec pri prubeznem ukladani posila jen zmenena pole, takze chybejici
    # `email_{id}` znamena „beze zmeny“, ne „smazat“. Prazdne pole
    # naopak e-mail maze.
    zadane_emaily: dict[int, str | None] = {
        u.id: (
            prazdne_na_none(text_z_formulare(formular, f"email_{u.id}"))
            if f"email_{u.id}" in formular
            else u.email
        )
        for u in vsichni
    }
    # Ucty bez pole si sve adresy drzi: zapsat je napred, aby je zadne jine pole
    # nemohlo zabrat driv (unikatni index by jinak shodil ulozeni).
    pouzite: dict[str, int] = {
        u.email.lower(): u.id for u in vsichni if u.email and f"email_{u.id}" not in formular
    }
    for uzivatel in vsichni:
        if f"email_{uzivatel.id}" not in formular:
            continue
        email = zadane_emaily[uzivatel.id]
        # Tvar se kontroluje jen u zmeneneho e-mailu - starsi hodnota, ktera by
        # dnes neprosla, nema blokovat kazde ulozeni tabulky.
        if email is not None and email != uzivatel.email:
            chyba_tvaru = over_email(email)
            if chyba_tvaru:
                chyby.append(f"„{uzivatel.jmeno}“: {chyba_tvaru}")
                email = zadane_emaily[uzivatel.id] = uzivatel.email
        if email is None:
            continue
        klic = email.lower()
        if klic in pouzite:
            chyby.append(
                f"Tentýž e-mail je zadaný u víc účtů najednou — u „{uzivatel.jmeno}“ nezměněn."
            )
            zadane_emaily[uzivatel.id] = uzivatel.email
        else:
            pouzite[klic] = uzivatel.id
    return zadane_emaily, chyby


def _uloz_limity_ai(formular: FormData, uzivatel: Uzivatel) -> None:
    """Limity cteni AI: platne hned, citaji se z uctu pri kazdem cteni.
    Pole se jmenuji jako sloupce (`limit_cteni_den`); chybejici nebo necitelne se nemeni."""
    for pole, (nejmene, nejvice, _krok) in cteni_ai.MEZE_LIMITU_AI.items():
        limit = _limit_z_formulare(text_z_formulare(formular, pole), nejmene, nejvice)
        if limit is not None:
            setattr(uzivatel, pole, limit)


def _zmen_roli(
    sezeni: Session, aktualni: Uzivatel, formular: FormData, uzivatel: Uzivatel
) -> str | None:
    """Prenastavi roli podle formulare; vrati chybu z `zkontroluj_zmenu`, nebo None."""
    nova_role = text_z_formulare(formular, f"role_{uzivatel.id}")
    if not (nova_role and nova_role in ROLE and nova_role != uzivatel.role):
        return None
    chyba = zkontroluj_zmenu(sezeni, uzivatel, aktualni, nova_role=nova_role)
    if not chyba:
        uzivatel.role = nova_role
    return chyba


def _uloz_uzivatele(request: Request, sezeni: Session, aktualni: Uzivatel, formular: FormData):
    over_delky(formular, {"jmeno": Uzivatel.jmeno, "email": Uzivatel.email})
    vsichni = list(sezeni.scalars(select(Uzivatel)))
    zadane_emaily, chyby = _rozdel_emaily(vsichni, formular)

    zmeny_emailu: list[tuple[Uzivatel, str | None]] = []
    for uzivatel in vsichni:
        nove_jmeno = prazdne_na_none(text_z_formulare(formular, f"jmeno_{uzivatel.id}"))
        if nove_jmeno:
            uzivatel.jmeno = nove_jmeno
        novy_email = zadane_emaily[uzivatel.id]
        if novy_email != uzivatel.email:
            zmeny_emailu.append((uzivatel, uzivatel.email))
            uzivatel.email = novy_email
            # Spravce e-mail nepotvrzuje z nove schranky, tak stitek „overen“
            # o nem nesmi tvrdit, ze ho potvrdil sam clovek.
            uzivatel.email_overen = False
        chyba = _zmen_roli(sezeni, aktualni, formular, uzivatel)
        if chyba:
            chyby.append(chyba)

    # Az po vsech zmenach: `email_zmenen` dela dotaz a ten by zapsal (autoflush)
    # rozpracovane zmeny driv, nez se dokonci kontrola vyse.
    for uzivatel, stary_email in zmeny_emailu:
        zaznam = email_zmenen(sezeni, uzivatel, stary_email, kdo=f"správce {aktualni.jmeno}")
        if zaznam is not None and posta.neodeslano(zaznam):
            chyby.append(
                f"Upozornění na původní adresu u „{uzivatel.jmeno}“ se neodeslalo. "
                f"{posta.hlaska_neodeslano(zaznam)}"
            )

    sezeni.commit()
    for chyba in chyby:
        oznam(request, chyba, "chyba")
    if not chyby:
        oznam(request, "Uloženo.")
    return zpet("/uzivatele")


@router.post("/uzivatele/{uzivatel_id}/limity")
async def ulozit_limity(request: Request, uzivatel_id: int, sezeni: Databaze):
    """Limity čtení AI jednoho účtu z okna Limity. Hromadné uložení tabulky je nečte,
    aby se při úpravě jména či role omylem nepřepsaly (a okno je ukládá samo)."""
    # ID větší než sloupec INTEGER by databáze odmítla výjimkou, ne prázdným výsledkem.
    cil = sezeni.get(Uzivatel, uzivatel_id) if 0 < uzivatel_id <= MAX_ID else None
    if cil is None:
        return uz_smazano(request)
    _uloz_limity_ai(await nacti_formular(request), cil)
    sezeni.commit()
    oznam(request, "Limity čtení AI uloženy.")
    return zpet("/uzivatele")


@router.post("/uzivatele/{uzivatel_id}/aktivni")
async def prepnout_aktivni(
    request: Request,
    uzivatel_id: int,
    sezeni: Databaze,
    aktualni: Uzivatel = Depends(auth.vyzaduj_spravce),
):
    cil = sezeni.get(Uzivatel, uzivatel_id)
    if cil is None:
        return zpet("/uzivatele")

    if cil.aktivni:
        chyba = zkontroluj_zmenu(sezeni, cil, aktualni, novy_stav_aktivni=False)
        if chyba:
            oznam(request, chyba, "chyba")
            return zpet("/uzivatele")
        cil.aktivni = False
        # Pozvanka pak uz ucet neoziví, jen toto tlacitko.
        cil.nekdy_aktivni = True
        # Stara cookie zustava kryptograficky platna a `nacti_uzivatele` ji
        # odmita jen kvuli `aktivni` - bez zvyseni verze by ji znovuaktivace
        # oziví (ztraceny telefon, deaktivace, o tyden pozdeji aktivace; B-016).
        cil.verze_hesla += 1
        # Nepouzity odkaz (pozvanka, obnova hesla, zmena e-mailu) vydany pred
        # deaktivaci nesmi po ni dal platit.
        tokeny.zneplatni_vse(sezeni, cil)
        oznam(request, "Účet deaktivován.")
    else:
        cil.aktivni = True
        oznam(request, "Účet je znovu aktivní.")
    sezeni.commit()
    return zpet("/uzivatele")


@router.post("/uzivatele/{uzivatel_id}/smazat")
def smazat(
    request: Request,
    uzivatel_id: int,
    sezeni: Databaze,
    aktualni: Uzivatel = Depends(auth.vyzaduj_spravce),
):
    """Úplné smazání deaktivovaného účtu i jeho záznamů (log, využití AI, tokeny, e-maily)."""
    # Kontrola a smazání jsou jedna transakce: dva správci naráz nesmí oba projít kontrolou
    # „zbývá jiný správce“ a pak smazat každý jednoho.
    zamkni_zapis(sezeni)
    cil = sezeni.get(Uzivatel, uzivatel_id) if 0 < uzivatel_id <= MAX_ID else None
    if cil is None:
        return uz_smazano(request)
    chyba = zkontroluj_smazani(sezeni, cil, aktualni)
    if chyba:
        sezeni.rollback()
        oznam(request, chyba, "chyba")
        return zpet("/uzivatele")
    smaz_ucet(sezeni, cil, aktualni)
    sezeni.commit()
    oznam(request, "Účet byl smazán i s jeho záznamy.")
    return zpet("/uzivatele")


@router.post("/uzivatele/{uzivatel_id}/nove-heslo")
def nove_heslo(
    request: Request,
    uzivatel_id: int,
    sezeni: Databaze,
):
    cil = sezeni.get(Uzivatel, uzivatel_id)
    if cil is None:
        return zpet("/uzivatele")

    if cil.aktivni and cil.email:
        # Ucet s e-mailem dostane misto hesla jednorazovy odkaz - heslo se
        # nikomu nediktuje a nikde nevznika slabe docasne heslo. Stavajici
        # heslo plati, dokud ho clovek pres odkaz sam nezmeni.
        zaznam = posli_odkaz(sezeni, cil, UCEL_RESET_HESLA)
        sezeni.commit()
        if posta.neodeslano(zaznam):
            oznam(
                request,
                f"Odkaz na nové heslo se neodeslal. {posta.hlaska_neodeslano(zaznam)}",
                "chyba",
            )
        elif zaznam.stav == STAV_EMAILU_LOKALNE:
            oznam(request, f"Odkaz na nové heslo se neodeslal. {posta.HLASKA_LOKALNE}", "pozor")
        else:
            oznam(request, "Odkaz na nastavení nového hesla je odeslaný na e-mail účtu.")
        return zpet("/uzivatele")

    heslo = docasne_heslo()
    # `nastav_heslo` zvysi verzi hesla - to odhlasi kazdou session, kterou uz
    # mel cil otevrenou.
    nastav_heslo(cil, heslo, docasne=True)
    sezeni.commit()
    return strana(request, "uzivatel_heslo.html", ucet=cil, heslo=heslo)


@router.post("/uzivatele/{uzivatel_id}/pozvat-znovu")
def pozvat_znovu(
    request: Request,
    uzivatel_id: int,
    sezeni: Databaze,
    aktualni: Uzivatel = Depends(auth.vyzaduj_spravce),
):
    cil = sezeni.get(Uzivatel, uzivatel_id)
    if cil is None or cil.aktivni:
        return zpet("/uzivatele")

    try:
        zaznam = posli_pozvanku(sezeni, cil, aktualni)
    except ValueError as chyba:
        sezeni.rollback()
        oznam(request, str(chyba), "chyba")
        return zpet("/uzivatele")
    sezeni.commit()
    if posta.neodeslano(zaznam):
        oznam(request, f"Pozvánka se neodeslala. {posta.hlaska_neodeslano(zaznam)}", "chyba")
    elif zaznam.stav == STAV_EMAILU_LOKALNE:
        oznam(request, f"Pozvánka se neodeslala. {posta.HLASKA_LOKALNE}", "pozor")
    else:
        oznam(request, "Pozvánka odeslána znovu.")
    return zpet("/uzivatele")
