"""Verejne dostupne stranky kolem uctu - prihlaseni a odhlaseni, prijeti
pozvanky a zapomenute heslo. Router NEDOSTAVA zadnou z auth zavislosti - cesty
musi byt zapsane v `auth.VEREJNE_CESTY`, jinak by je middleware odmitl driv,
nez by se vubec dostaly sem.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from app import auth
from app.chyby_poli import StavFormulare, stav_s_chybami
from app.formulare import (
    ObnovitHesloForm,
    PozvankaForm,
    PrihlaseniForm,
    ZapomenuteHesloForm,
    formular_s_chybami,
)
from app.models import (
    CINNOST_ODHLASENI,
    CINNOST_POZVANKA,
    CINNOST_PRIHLASENI,
    CINNOST_PRIHLASENI_NEUSPESNE,
    CINNOST_REGISTRACE,
    CINNOST_RESET_HESLA,
    CINNOST_RESET_HESLA_ZADOST,
    UCEL_POZVANKA,
    UCEL_RESET_HESLA,
    TokenUzivatele,
    Uzivatel,
)
from app.routes.spolecne import Databaze
from app.sablony import oznam, strana, strana_s_chybou, zpet
from app.services import log_cinnosti, tokeny
from app.services.omezeni_prihlaseni import OKNO_S, Pravidlo, omezovac
from app.services.uzivatele import (
    aktivuj_ucet,
    chyba_hesla_u_pole,
    chyby_registrace,
    najdi_ucet,
    nastav_heslo,
    posli_obnovu_hesla,
)

router = APIRouter()

# Limity prihlaseni (kolik pokusu, za kolik sekund). Dvojice ucet + IP ma
# vychozi limit omezovace (5 za 15 minut), viz komentar v `prihlaseni_odeslani`.
PRIHLASENI_ZA_IP = (30, OKNO_S)
PRIHLASENI_ZA_UCET = (20, 3600)


@router.get("/prihlaseni", response_class=HTMLResponse)
async def prihlaseni_formular(request: Request, sezeni: Databaze):
    if auth.je_prihlasen(request):
        return zpet("/")
    return _strana_prihlaseni(request, sezeni)


def _strana_prihlaseni(request: Request, sezeni: Session, stav: StavFormulare | None = None):
    """Prihlasovaci stranka; se `stav` je to znovu vykresleny formular s chybou u pole."""
    kontext = {"existuje_ucet": auth.existuje_ucet(sezeni)}
    if stav is not None:
        return strana_s_chybou(request, "prihlaseni.html", stav, **kontext)
    return strana(request, "prihlaseni.html", **kontext)


def _log_neuspechu(sezeni: Session, pokus: Uzivatel | None, ip: str | None, popis: str) -> None:
    """Zapise neuspesne prihlaseni (nebo zablokovani) do logu cinnosti.

    Login existujiciho uctu se v logu smi objevit - je to jen jmeno.
    Neexistujici login muze byt preklep, ktery omylem obsahuje heslo
    napsane do spatneho policka, tak se misto nej zapise "neznáme jméno"."""
    log_cinnosti.zapis(
        sezeni,
        CINNOST_PRIHLASENI_NEUSPESNE,
        uzivatel_id=pokus.id if pokus else None,
        uzivatel_jmeno=pokus.login if pokus else "neznámé jméno",
        popis=popis,
        ip=ip,
    )


def _zapocti_neuspech(pokus: Uzivatel | None, ucet_klic: str) -> str:
    """Zapocita neuspesny pokus na ucet a vrati popis pro log cinnosti.

    Dvojice ucet + IP a samotna IP se zapsaly uz pred overenim hesla
    (`rezervuj_pokus`), tady zbyva jen pocitadlo uctu pro spravce."""
    popis = "neúspěšné přihlášení"
    if pokus is not None:
        # Samotny ucet se nezamyka (to by dovolilo cizimu zamknout majitele),
        # jen se nechá stopa pro spravce - zapise se pri dosazeni limitu.
        omezovac.zaznamenej_neuspech(ucet_klic)
        if omezovac.pocet(ucet_klic, PRIHLASENI_ZA_UCET[1]) == PRIHLASENI_ZA_UCET[0]:
            popis += f" — už {PRIHLASENI_ZA_UCET[0]} neúspěšných pokusů za hodinu na tenhle účet"
    return popis


def _odpoved_na_blokaci(request: Request, sezeni: Session, pokus: Uzivatel | None, ip: str | None):
    """Odpoved na pokus o prihlaseni po prekroceni limitu.

    Heslo se v tomhle pripade ani neoveruje - po prekroceni limitu
    nema smysl dal zatezovat bcrypt (a tim padem ani prozrazovat
    casovanim odpovedi, jestli je heslo blizko spravnemu).
    Do logu cinnosti jen prvni zablokovani za okno z dane IP, jinak by utocnik
    log zaplavil. Bez tohohle by spravce videl pet pokusu a pak nic."""
    if omezovac.ohlas_blokaci(f"blokace-prihlaseni:{ip or '?'}"):
        _log_neuspechu(sezeni, pokus, ip, "přihlášení zablokováno (příliš mnoho pokusů)")
        sezeni.commit()
    oznam(request, "Příliš mnoho pokusů o přihlášení. Zkus to znovu za chvíli.", "chyba")
    return zpet("/prihlaseni")


def _dokonci_prihlaseni(request: Request, sezeni: Session, ucet: Uzivatel, ip: str | None):
    auth.prihlas(request, ucet)
    # Naivni UTC - stejna konvence jako u logu cinnosti, aby se casy
    # v databazi daly porovnavat bez ohledu na casove pasmo.
    ucet.posledni_prihlaseni = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    log_cinnosti.zapis(
        sezeni,
        CINNOST_PRIHLASENI,
        uzivatel_id=ucet.id,
        uzivatel_jmeno=ucet.jmeno,
        popis="přihlášení",
        ip=ip,
    )
    sezeni.commit()
    return zpet("/moje-doklady" if ucet.je_podepisujici else "/")


def _chyby_prazdnych(data: PrihlaseniForm) -> dict[str, str]:
    chyby = {}
    if not data.uzivatel:
        chyby["uzivatel"] = "Napiš jméno nebo e-mail."
    if not data.heslo:
        chyby["heslo"] = "Napiš heslo."
    return chyby


@router.post("/prihlaseni")
def prihlaseni_odeslani(
    request: Request,
    sezeni: Databaze,
    data: Annotated[
        PrihlaseniForm | StavFormulare,
        Depends(formular_s_chybami(PrihlaseniForm, tajna=("heslo",))),
    ],
):
    # Chyba je tu jen v tom, co clovek nevyplnil; zda ucet
    # existuje a zda sedi heslo, se tady neprozradi (to je jedna hlaska nize). Prazdne
    # pole se nepocita do limitu ani nespousti bcrypt - nic by neprislo.
    if isinstance(data, StavFormulare):
        return _strana_prihlaseni(request, sezeni, data)
    if not data.uzivatel or not data.heslo:
        return _strana_prihlaseni(
            request, sezeni, stav_s_chybami(request, **_chyby_prazdnych(data))
        )
    heslo = data.heslo
    login = data.uzivatel.lower()
    ip = request.client.host if request.client else None
    pokus = najdi_ucet(sezeni, login)
    # Pocitadla drzi ucet (login a e-mail jsou dve jmena jednoho uctu, B-018),
    # ne napsany text. Neznamy text se zkrati - pole formulare muze mit 1 MB
    # a klic by zabral tolik pameti.
    ucet_klic = f"ucet:{pokus.id}" if pokus else f"jmeno:{login[:100]}"
    klic_ucet_ip = f"login-ip:{ucet_klic}:{ip or '?'}"
    klic_ip = f"ip:{ip}" if ip else None
    # Blokuje dvojice ucet + IP, ne samotny ucet: kdo zna cizi login, zamkne jen
    # sebe, majitel se z jine site prihlasi. Samotna IP ma limit vyssi,
    # aby ji nevycerpala klubovna s par prepsanimi.
    #
    # Pokus se zapise PRED pomalym overenim hesla (bcrypt), jednou operaci spolu
    # s kontrolou limitu. Routa je `def` a pokusy bezi ve vlaknech souberzne;
    # kdyby se neuspech zapsal az po bcryptu, prosla by limitem vsechna vlakna,
    # dokud by nikdo nezapsal. Pri uspechu se pokus vrati.
    rezervace = omezovac.rezervuj_pokus(
        Pravidlo(klic_ucet_ip), Pravidlo(klic_ip, *PRIHLASENI_ZA_IP) if klic_ip else None
    )
    if rezervace is None:
        return _odpoved_na_blokaci(request, sezeni, pokus, ip)
    ucet = auth.overit(sezeni, login, heslo)
    if ucet is None:
        popis = _zapocti_neuspech(pokus, ucet_klic)
        _log_neuspechu(sezeni, pokus, ip, popis)
        sezeni.commit()
        oznam(request, "Nesprávné jméno nebo heslo.", "chyba")
        return zpet("/prihlaseni")
    omezovac.vrat_pokus(rezervace)
    omezovac.vycisti(klic_ucet_ip)
    return _dokonci_prihlaseni(request, sezeni, ucet, ip)


@router.get("/odhlaseni")
async def odhlaseni_get():
    """GET nic nemeni (cizi stranka by uzivatele odkazem odhlasila) - odhlasuje
    jen POST. Tohle zustalo pro stare zalozky."""
    return zpet("/")


@router.post("/odhlaseni")
async def odhlaseni(request: Request, sezeni: Databaze):
    uzivatel_id = request.session.get(auth.KLIC_UZIVATEL)
    ucet = sezeni.get(Uzivatel, uzivatel_id) if uzivatel_id else None
    if ucet is not None and auth.cookie_odpovida_uctu(request, ucet):
        # Do logu jen odhlaseni opravdu prihlaseneho, ne opsana stara cookie.
        log_cinnosti.zapis(
            sezeni,
            CINNOST_ODHLASENI,
            uzivatel_id=ucet.id,
            uzivatel_jmeno=ucet.jmeno,
            popis="odhlášení",
        )
        auth.odhlas(request, ucet)
        sezeni.commit()
    else:
        auth.odhlas(request)
    oznam(request, "Odhlášeno.")
    return zpet("/prihlaseni")


def _pozvany_ucet(sezeni: Session, token: str) -> tuple[TokenUzivatele, Uzivatel] | None:
    """Platny token pozvanky a jeho ucet, nebo None.

    Pozvanka oziví jen ucet, ktery jeste nikdy nebyl aktivni. U deaktivovaneho
    uctu by stary nepouzity odkaz vratil pristup i se starou roli, obesel by
    tlacitko Aktivovat."""
    zaznam = tokeny.najdi(sezeni, UCEL_POZVANKA, token)
    if zaznam is None:
        return None
    ucet = sezeni.get(Uzivatel, zaznam.uzivatel_id)
    if ucet is None or ucet.nekdy_aktivni:
        return None
    return zaznam, ucet


@router.get("/pozvanka/{token}", response_class=HTMLResponse)
async def pozvanka_formular(request: Request, token: str, sezeni: Databaze):
    pozvany = _pozvany_ucet(sezeni, token)
    if pozvany is None:
        return strana(request, "pozvanka.html", platna=False)
    return strana(request, "pozvanka.html", platna=True, ucet=pozvany[1])


def _odpoved_na_blokaci_pozvanky(request: Request, sezeni: Session, token: str, ip: str | None):
    if omezovac.ohlas_blokaci(f"blokace-pozvanky:{ip or '?'}"):
        log_cinnosti.zapis(
            sezeni,
            CINNOST_POZVANKA,
            popis="pozvánka zablokována (příliš mnoho pokusů)",
            ip=ip,
        )
        sezeni.commit()
    oznam(request, "Příliš mnoho pokusů. Zkus to znovu za chvíli.", "chyba")
    return zpet(f"/pozvanka/{token}")


def _strana_pozvanky(request: Request, ucet: Uzivatel, stav: StavFormulare | None = None):
    """Formular pozvanky; se `stav` je to znovu vykresleny formular s chybou u pole."""
    if stav is not None:
        return strana_s_chybou(request, "pozvanka.html", stav, platna=True, ucet=ucet)
    return strana(request, "pozvanka.html", platna=True, ucet=ucet)


@router.post("/pozvanka/{token}")
def pozvanka_odeslani(
    request: Request,
    token: str,
    sezeni: Databaze,
    data: Annotated[
        PozvankaForm | StavFormulare,
        Depends(formular_s_chybami(PozvankaForm, tajna=("heslo", "znovu_heslo"))),
    ],
):
    ip = request.client.host if request.client else None
    klic_pokusu = f"pozvanka:{ip}" if ip else None
    if omezovac.zablokovano(klic_pokusu):
        return _odpoved_na_blokaci_pozvanky(request, sezeni, token, ip)

    # Platnost odkazu se overuje drive nez chyby formulare: neplatny odkaz nesmi
    # nic prozradit ani ukazat formular.
    pozvany = _pozvany_ucet(sezeni, token)
    if pozvany is None:
        return strana(request, "pozvanka.html", platna=False)
    zaznam, ucet = pozvany

    if isinstance(data, StavFormulare):
        omezovac.zaznamenej_neuspech(klic_pokusu)
        return _strana_pozvanky(request, ucet, data)

    login = data.login.lower()
    jmeno = data.jmeno

    chyby = chyby_registrace(sezeni, ucet, login, jmeno, data.heslo, data.znovu_heslo)
    if chyby:
        omezovac.zaznamenej_neuspech(klic_pokusu)
        return _strana_pozvanky(request, ucet, stav_s_chybami(request, **chyby))

    omezovac.vycisti(klic_pokusu)
    aktivuj_ucet(ucet, login, jmeno, data.heslo)
    tokeny.spotrebuj(sezeni, zaznam)

    auth.prihlas(request, ucet)
    log_cinnosti.zapis(
        sezeni,
        CINNOST_REGISTRACE,
        uzivatel_id=ucet.id,
        uzivatel_jmeno=ucet.jmeno,
        popis="přijetí pozvánky a založení účtu",
        ip=ip,
    )
    sezeni.commit()
    oznam(request, f"Vítej, {ucet.jmeno}!")
    return zpet("/moje-doklady" if ucet.je_podepisujici else "/")


@router.get("/zapomenute-heslo", response_class=HTMLResponse)
async def zapomenute_heslo_formular(request: Request):
    return strana(request, "zapomenute_heslo.html")


def _posli_obnovu_na_pozadi(vazba: Engine | Connection, prihlaseni: str) -> None:
    """Bezi az PO odpovedi (FastAPI `BackgroundTasks`) - kdyby appka hledala
    ucet a posilala mail jeste behem pozadavku, doba SMTP spojeni by
    prozradila, jestli prihlasovaci jmeno/e-mail v databazi je.
    Vlastni databazova session na stejnem spojeni jako pozadavek - `sezeni`
    pozadavku uz v tuhle chvili muze byt zavrene.
    """
    with Session(bind=vazba) as vlastni_sezeni:
        posli_obnovu_hesla(vlastni_sezeni, prihlaseni)
        vlastni_sezeni.commit()


@router.post("/zapomenute-heslo")
def zapomenute_heslo_odeslani(
    request: Request,
    background_tasks: BackgroundTasks,
    sezeni: Databaze,
    data: Annotated[
        ZapomenuteHesloForm | StavFormulare, Depends(formular_s_chybami(ZapomenuteHesloForm))
    ],
):
    # Nevyplnena (nebo moc dlouha) zadost se nepocita do limitu ani nic nehleda -
    # nic by neprislo. Zda ucet existuje, se tady neprozradi.
    if isinstance(data, StavFormulare):
        return strana_s_chybou(request, "zapomenute_heslo.html", data)
    prihlaseni = data.prihlaseni
    if not prihlaseni:
        stav = stav_s_chybami(request, prihlaseni="Napiš přihlašovací jméno nebo e-mail.")
        return strana_s_chybou(request, "zapomenute_heslo.html", stav)
    ip = request.client.host if request.client else None
    # Stejna odpoved at ucet existuje, nebo ne - jinak by appka prozradila,
    # ktere prihlasovaci jmeno/e-mail v databazi jsou.
    oznam(
        request,
        "Pokud účet existuje a má e-mail, poslali jsme na něj odkaz na obnovení hesla.",
    )
    log_cinnosti.zapis(
        sezeni,
        CINNOST_RESET_HESLA_ZADOST,
        popis="žádost o obnovení hesla",
        ip=ip,
    )
    sezeni.commit()

    # Pocita se kazda zadost z adresy, i ta na existujici ucet - jinak by jedna
    # adresa mohla zahltit schranky mnoha lidi. Limit podle uctu je
    # v `posli_obnovu_hesla`.
    klic_ip = f"reset:{ip}" if ip else None
    if omezovac.zablokovano(klic_ip):
        if omezovac.ohlas_blokaci(f"blokace-resetu:{ip or '?'}"):
            log_cinnosti.zapis(
                sezeni,
                CINNOST_RESET_HESLA_ZADOST,
                popis="obnovení hesla zablokováno (příliš mnoho žádostí)",
                ip=ip,
            )
            sezeni.commit()
    else:
        omezovac.zaznamenej_neuspech(klic_ip)
        # Hledani uctu i odeslani mailu az po odpovedi - viz
        # `_posli_obnovu_na_pozadi`. `sezeni.get_bind()` je porad totez
        # spojeni, na kterem beha testovaci databaze v pameti.
        background_tasks.add_task(_posli_obnovu_na_pozadi, sezeni.get_bind(), prihlaseni)
    return zpet("/zapomenute-heslo")


def _odkaz_k_obnove(sezeni: Session, token: str) -> tuple[TokenUzivatele, Uzivatel] | None:
    """Platny odkaz na obnovu hesla a aktivni ucet, kteremu patri, jinak None.
    Deaktivovanemu uctu heslo nastavit nejde - odkaz vydany pred deaktivaci by
    jinak po znovuaktivaci pustil dovnitr toho, kdo ho drzi."""
    zaznam = tokeny.najdi(sezeni, UCEL_RESET_HESLA, token)
    if zaznam is None:
        return None
    ucet = sezeni.get(Uzivatel, zaznam.uzivatel_id)
    if ucet is None or not ucet.aktivni:
        return None
    return zaznam, ucet


@router.get("/heslo/obnovit/{token}", response_class=HTMLResponse)
async def obnovit_heslo_formular(request: Request, token: str, sezeni: Databaze):
    return strana(request, "obnovit_heslo.html", platny=_odkaz_k_obnove(sezeni, token) is not None)


def _strana_obnovy_hesla(request: Request, stav: StavFormulare | None = None):
    """Formular noveho hesla; se `stav` je to znovu vykresleny formular s chybou u pole."""
    if stav is not None:
        return strana_s_chybou(request, "obnovit_heslo.html", stav, platny=True)
    return strana(request, "obnovit_heslo.html", platny=True)


@router.post("/heslo/obnovit/{token}")
def obnovit_heslo_odeslani(
    request: Request,
    token: str,
    sezeni: Databaze,
    data: Annotated[
        ObnovitHesloForm | StavFormulare,
        Depends(formular_s_chybami(ObnovitHesloForm, tajna=("heslo", "znovu_heslo"))),
    ],
):
    ip = request.client.host if request.client else None
    # Platnost odkazu se overuje drive nez chyby formulare: neplatny odkaz nesmi
    # nic prozradit ani ukazat formular.
    odkaz = _odkaz_k_obnove(sezeni, token)
    if odkaz is None:
        return strana(request, "obnovit_heslo.html", platny=False)
    zaznam, ucet = odkaz

    if isinstance(data, StavFormulare):
        return _strana_obnovy_hesla(request, data)
    chyby = chyba_hesla_u_pole(data.heslo, data.znovu_heslo)
    if chyby:
        return _strana_obnovy_hesla(request, stav_s_chybami(request, **chyby))

    # `nastav_heslo` zvysi verzi hesla - to odhlasi kazdou session, kterou uz
    # mel ucet otevrenou; kdyby heslo obnovoval nekdo jiny, puvodni majitel
    # uctu se hned odhlasi.
    nastav_heslo(ucet, data.heslo)
    tokeny.spotrebuj(sezeni, zaznam)
    log_cinnosti.zapis(
        sezeni,
        CINNOST_RESET_HESLA,
        uzivatel_id=ucet.id,
        uzivatel_jmeno=ucet.jmeno,
        popis="obnovení hesla",
        ip=ip,
    )
    sezeni.commit()
    oznam(request, "Heslo změněno, přihlas se novým heslem.")
    return zpet("/prihlaseni")
