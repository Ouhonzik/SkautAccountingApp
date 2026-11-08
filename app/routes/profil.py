"""Vlastni profil - jmeno, e-mail, zmena hesla.

Router dostava jen `auth.nacti_uzivatele`, ne `auth.prihlaseny` - kdyby
vyzadoval i to, presmerovani "prihlasil ses docasnym heslem, zmen si ho"
by mirilo prave sem a stranka by se nikdy nenacetla. Ze stejneho duvodu sem
smi i revizor: zmena vlastniho hesla neni "zapis do dat" v tom smyslu, ktery
`zapis_jen_pro_editory` hlida.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app import auth
from app.chyby_poli import StavFormulare, stav_s_chybami
from app.formulare import ProfilForm, ZmenaHeslaForm, formular_s_chybami
from app.models import STAV_EMAILU_LOKALNE, UCEL_ZMENA_EMAILU, OdeslanyEmail, Uzivatel
from app.routes.spolecne import Databaze
from app.sablony import oznam, strana, strana_s_chybou, zpet
from app.services import posta, tarif, tokeny
from app.services.omezeni_prihlaseni import Pravidlo, omezovac
from app.services.uzivatele import (
    chyba_hesla,
    email_je_volny,
    email_zmenen,
    nastav_heslo,
    over_email,
    posli_odkaz,
)

router = APIRouter()

zurnal = logging.getLogger(__name__)

# Chyby z prohlizece: kolik jich smi jeden ucet poslat za minutu.
LIMIT_CHYB_JS = 10
OKNO_CHYB_JS_S = 60
DELKA_HLASENI_JS = 300

_MOC_POKUSU = "Příliš mnoho pokusů. Zkus to znovu za chvíli."


def _chyba_hesla_v_profilu(uzivatel: Uzivatel, heslo: str, spatne: str) -> str | None:
    """Overi aktualni heslo prihlaseneho pro citlivou zmenu (e-mail, nove heslo).
    Vrati chybovou hlasku, nebo None kdyz heslo sedi.

    Spatne heslo se pocita do omezovace pod klicem uctu - zmena e-mailu i zmena
    hesla sdili jedno pocitadlo, jinak by se dalo „stare heslo“ hadat bez limitu
    z ukradene relace. Prazdne heslo se nepocita, neni to hadani."""
    klic = f"profil:{uzivatel.id}"
    if omezovac.zablokovano(klic):
        return _MOC_POKUSU
    if not heslo:
        return spatne
    if not auth.over_heslo(uzivatel, heslo):
        omezovac.zaznamenej_neuspech(klic)
        return spatne
    omezovac.vycisti(klic)
    return None


def _oznam_potvrzeni(request: Request, odkaz: OdeslanyEmail, uspech: str, neuspech: str) -> None:
    """Hlaska o odeslani potvrzovaciho e-mailu podle skutecneho stavu zaznamu
: „poslali jsme“ jen tehdy, kdyz zprava opravdu odesla."""
    if posta.neodeslano(odkaz):
        oznam(request, f"{neuspech} {posta.hlaska_neodeslano(odkaz)}", "chyba")
    elif odkaz.stav == STAV_EMAILU_LOKALNE:
        oznam(request, f"{neuspech} {posta.HLASKA_LOKALNE}", "pozor")
    else:
        oznam(request, uspech)


def _strana_profilu(
    request: Request, sezeni: Session, uzivatel: Uzivatel, stav: StavFormulare | None = None
) -> HTMLResponse:
    """Profil; se `stav` je to znovu vykreslený formulář s chybou u pole.

    Nic se pred tim neulozilo, ale `uzivatel` mohl zustat rozepsany - vrati se na
    to, co je v databazi, aby hlavicka stranky nenesla nepotvrzene jmeno."""
    if stav is not None:
        sezeni.rollback()
    kontext: dict[str, Any] = {"cteni_ai": tarif.cteni_ai_uzivatele(sezeni, uzivatel.id)}
    if stav is not None:
        return strana_s_chybou(request, "profil.html", stav, **kontext)
    return strana(request, "profil.html", **kontext)


@router.get("/profil", response_class=HTMLResponse)
async def formular(
    request: Request,
    sezeni: Databaze,
    uzivatel: Uzivatel = Depends(auth.nacti_uzivatele),
):
    return _strana_profilu(request, sezeni, uzivatel)


@router.post("/profil/ulozit")
def ulozit(
    request: Request,
    sezeni: Databaze,
    data: Annotated[
        ProfilForm | StavFormulare, Depends(formular_s_chybami(ProfilForm, tajna=("heslo",)))
    ],
    uzivatel: Uzivatel = Depends(auth.nacti_uzivatele),
):
    # Chyba formulare nebo chyba, kterou zjistime az tady: stranka znovu, s chybou u pole
    # a tim, co clovek napsal. Kdyz je spatny jen e-mail, jmeno se ulozi (jako driv).
    if isinstance(data, StavFormulare):
        return _strana_profilu(request, sezeni, uzivatel, data)
    jmeno = data.jmeno
    if not jmeno:
        stav = stav_s_chybami(request, jmeno="Jméno nesmí být prázdné.")
        return _strana_profilu(request, sezeni, uzivatel, stav)

    novy_email = data.email
    if novy_email is not None and novy_email != uzivatel.email:
        # Zmena e-mailu pripravi prevzeti uctu (reset hesla jde na novou
        # adresu), proto chce aktualni heslo; tvar se kontroluje
        # driv nez heslo, aby preklep nespotreboval pokus.
        chyby: dict[str, str] = {}
        if chyba := over_email(novy_email):
            chyby["email"] = chyba
        elif chyba := _chyba_hesla_v_profilu(
            uzivatel, data.heslo, "Ke změně e-mailu zadej své aktuální heslo."
        ):
            chyby["heslo"] = chyba
        elif not email_je_volny(sezeni, novy_email, krome_id=uzivatel.id):
            chyby["email"] = "Tenhle e-mail už používá jiný účet."
        if chyby:
            uzivatel.jmeno = jmeno
            sezeni.commit()
            stav = stav_s_chybami(
                request, souhrn="Jméno je uložené, e-mail ne — oprav označené pole.", **chyby
            )
            return _strana_profilu(request, sezeni, uzivatel, stav)

    uzivatel.jmeno = jmeno
    if novy_email != uzivatel.email:
        if novy_email is None:
            # Smazani e-mailu nepotrebuje potvrzeni - jen se prestanou
            # posilat vyzvy k podpisu, nic to neodemyka. Odkazy uctu se ale
            # zneplatni a puvodni adresa se dozvi, ze byla odebrana.
            stary_email = uzivatel.email
            uzivatel.email = None
            uzivatel.email_overen = False
            zaznam = email_zmenen(sezeni, uzivatel, stary_email, kdo="ty sám(a) ve svém profilu")
            if zaznam is not None and posta.neodeslano(zaznam):
                oznam(
                    request,
                    f"Upozornění na původní adresu se neodeslalo. {posta.hlaska_neodeslano(zaznam)}",
                    "chyba",
                )
        else:
            odkaz = posli_odkaz(
                sezeni, uzivatel, UCEL_ZMENA_EMAILU, komu=novy_email, nova_hodnota=novy_email
            )
            sezeni.commit()
            _oznam_potvrzeni(
                request,
                odkaz,
                "Uloženo. Na novou adresu jsme poslali potvrzovací odkaz.",
                "Jméno je uloženo, ale potvrzovací e-mail se neodeslal.",
            )
            return zpet("/profil")

    sezeni.commit()
    oznam(request, "Uloženo.")
    return zpet("/profil")


@router.post("/profil/email/overit")
def overit_email(
    request: Request,
    sezeni: Databaze,
    uzivatel: Uzivatel = Depends(auth.nacti_uzivatele),
):
    """Posle znovu potvrzeni na stavajici (neoverenou) adresu - napr. kdyz
    prvni odkaz vyprsel nebo se ztratil."""
    if not uzivatel.email:
        return zpet("/profil")
    odkaz = posli_odkaz(
        sezeni, uzivatel, UCEL_ZMENA_EMAILU, komu=uzivatel.email, nova_hodnota=uzivatel.email
    )
    sezeni.commit()
    _oznam_potvrzeni(
        request,
        odkaz,
        "Potvrzovací odkaz jsme poslali na tvůj e-mail.",
        "Potvrzovací e-mail se neodeslal.",
    )
    return zpet("/profil")


@router.get("/profil/email/potvrdit/{token}")
def potvrdit_email(
    request: Request,
    token: str,
    sezeni: Databaze,
    uzivatel: Uzivatel = Depends(auth.nacti_uzivatele),
):
    zaznam = tokeny.najdi(sezeni, UCEL_ZMENA_EMAILU, token)
    if zaznam is None or zaznam.uzivatel_id != uzivatel.id:
        oznam(request, "Odkaz na potvrzení e-mailu už neplatí.", "chyba")
        return zpet("/profil")

    novy_email = zaznam.nova_hodnota
    assert novy_email is not None  # token UCEL_ZMENA_EMAILU ji vzdy nese
    if not email_je_volny(sezeni, novy_email, krome_id=uzivatel.id):
        oznam(request, "Tenhle e-mail už mezitím používá jiný účet.", "chyba")
        return zpet("/profil")

    stary_email = uzivatel.email
    uzivatel.email = novy_email
    uzivatel.email_overen = True
    tokeny.spotrebuj(sezeni, zaznam)
    # Zneplatni i ostatni odkazy (napr. obnovu hesla vyzadanou na starou adresu)
    # a oznami zmenu na puvodni adresu.
    oznameni = email_zmenen(sezeni, uzivatel, stary_email, kdo="ty sám(a) ve svém profilu")
    sezeni.commit()
    oznam(request, "E-mail potvrzen.")
    if oznameni is not None and posta.neodeslano(oznameni):
        oznam(
            request,
            f"Upozornění na původní adresu se neodeslalo. {posta.hlaska_neodeslano(oznameni)}",
            "chyba",
        )
    return zpet("/profil")


@router.post("/profil/heslo")
def zmenit_heslo(
    request: Request,
    sezeni: Databaze,
    data: Annotated[
        ZmenaHeslaForm | StavFormulare,
        Depends(
            formular_s_chybami(ZmenaHeslaForm, tajna=("stare_heslo", "nove_heslo", "znovu_heslo"))
        ),
    ],
    uzivatel: Uzivatel = Depends(auth.nacti_uzivatele),
):
    if isinstance(data, StavFormulare):
        return _strana_profilu(request, sezeni, uzivatel, data)
    chyba = _chyba_hesla_v_profilu(uzivatel, data.stare_heslo, "Staré heslo nesouhlasí.")
    if chyba:
        stav = stav_s_chybami(request, stare_heslo=chyba)
        return _strana_profilu(request, sezeni, uzivatel, stav)
    chyba = chyba_hesla(data.nove_heslo, data.znovu_heslo)
    if chyba:
        # „Hesla se neshoduji“ patri k poli pro opakovani, ostatni k novemu heslu.
        pole = "znovu_heslo" if data.nove_heslo != data.znovu_heslo else "nove_heslo"
        stav = stav_s_chybami(request, **{pole: chyba})
        return _strana_profilu(request, sezeni, uzivatel, stav)

    nastav_heslo(uzivatel, data.nove_heslo)
    sezeni.commit()
    # Vlastni session musi prezit zmenu hesla - jinak by cloveka zmena hesla
    # rovnou odhlasila.
    request.session[auth.KLIC_VERZE_HESLA] = uzivatel.verze_hesla
    oznam(request, "Heslo změněno.")
    return zpet("/profil")


def _do_logu(hodnota: str) -> str:
    """Text z prohlizece do jednoho radku logu: bez ridicich znaku (zalozeni
    falesnych radku), zkraceny."""
    return re.sub(r"[\x00-\x1f\x7f]", " ", hodnota)[:DELKA_HLASENI_JS]


@router.post("/profil/chyba-js")
def chyba_javascriptu(
    uzivatel: Annotated[Uzivatel, Depends(auth.nacti_uzivatele)],
    zprava: str = Form(""),
    soubor: str = Form(""),
    radek: str = Form(""),
    strana: str = Form(""),
):
    """Chyba v JavaScriptu v prohlizeci -> radek v logu serveru (jen tam, nikam
    dal). Sem smi i revizor: nic nezapisuje do dat. Zprava, soubor a stranka se
    zkracuji a ocisti; adresa souboru bez query stringu, cesta stranky s
    maskovanym tokenem. Nad limit se zahazuje mlcky."""
    if not omezovac.zkus_pouzit(Pravidlo(f"chyba-js:{uzivatel.id}", LIMIT_CHYB_JS, OKNO_CHYB_JS_S)):
        return Response(status_code=204)
    zurnal.warning(
        "Chyba JavaScriptu: %s (%s:%s, stránka %s, uživatel %s)",
        _do_logu(zprava),
        _do_logu(soubor.split("?")[0].split("#")[0]),
        _do_logu(radek)[:10],
        auth.cesta_pro_log(_do_logu(strana.split("?")[0])),
        uzivatel.id,
    )
    return Response(status_code=204)
