"""Pokladni kniha akce.

Kniha je pohled na data, ktera uz v aplikaci jsou (uctenky a prijmy), plus
rucni radky pro pohyby bez dokladu. Edituje se primo tady a zapisuje se zpatky
do puvodnich tabulek - proto ta tri ruzna zahlavi nazvu poli ve formulari.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.cas import dnes
from app.chyby_poli import StavFormulare, stav_s_chybami
from app.formaty import koruny
from app.formulare import (
    CastkaKnihyForm,
    RadekKnihyForm,
    VratkaForm,
    formular_s_chybami,
    over_delky,
)
from app.models import SMER_PRIJEM, SMER_VYDEJ, Akce, PokladniRadek, Prijem, Sken, Ucastnik
from app.routes.spolecne import (
    Databaze,
    hlaska_necitelnych,
    kam_dal,
    na_datum,
    nacti_akci,
    nacti_formular,
    nacti_otevrenou_akci,
    over_otevrenou,
    prazdne_na_none,
    prubezne_ulozeni,
    radky_z_formulare,
    uz_smazano_kam,
)
from app.sablony import oznam, strana, strana_s_chybou, zpet
from app.services import pokladni_kniha
from app.services.ciselniky import dalsi_poradi

router = APIRouter()


# Ktery z dvou formularu (radek, vratka) dostal chybu: oba maji pole `datum` a
# `castka`, takze by se chyba z jednoho ukazala i u druheho.
FORMULAR_RADEK = "radek"
FORMULAR_VRATKA = "vratka"


def _strana_knihy(
    request: Request,
    sezeni: Session,
    akce: Akce,
    stav: StavFormulare | None = None,
    formular: str = FORMULAR_RADEK,
) -> HTMLResponse:
    """Pokladni kniha; se `stav` je to znovu vykresleny formular „Pridat radek“ nebo
    „Vratka“ (podle `formular`) s chybou u pole."""
    radky = pokladni_kniha.sestav(akce)
    prijmy, vydaje, zustatek = pokladni_kniha.souhrn(radky)
    bez_data, mimo_slozku = pokladni_kniha.problemy(radky)
    kontext = {
        "akce": akce,
        "radky": radky,
        "soucet_prijmu": prijmy,
        "soucet_vydaju": vydaje,
        "konecny_zustatek": zustatek,
        "pocet_bez_data": bez_data,
        "pocet_mimo_slozku": mimo_slozku,
        "srovnana_rucne": pokladni_kniha.je_srovnana_rucne(akce),
        "dnes": dnes().isoformat(),
    }
    # Kazdy z obou formularu dostane svuj stav, druhy prazdny.
    kontext["stav_radku"] = stav if formular == FORMULAR_RADEK and stav else StavFormulare()
    kontext["stav_vratky"] = stav if formular == FORMULAR_VRATKA and stav else StavFormulare()
    if stav is None:
        return strana(request, "pokladni_kniha.html", **kontext)
    return strana_s_chybou(request, "pokladni_kniha.html", stav, **kontext)


@router.get("/akce/{akce_id}/pokladni-kniha", response_class=HTMLResponse)
async def prehled(request: Request, akce_id: int, sezeni: Databaze):
    return _strana_knihy(request, sezeni, nacti_akci(sezeni, akce_id))


@router.post("/akce/{akce_id}/pokladni-kniha/presunout")
async def presunout(
    request: Request,
    akce_id: int,
    sezeni: Databaze,
    presun: str = Form(...),
):
    """Sipka nahoru/dolu u radku.

    Radek chodi v jedinem poli jako "sken:12:nahoru". Tlacitko umi poslat jen
    svoji jednu hodnotu a mit u kazdeho radku dva dalsi formulare by znamenalo
    stovky formularu na strance - takhle staci jeden spolecny.
    """
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    zdroj, _, zbytek = presun.partition(":")
    cislo, _, kudy = zbytek.partition(":")

    # `isascii()` pred `isdigit()`: cislice jako "²" projdou `isdigit()`, ale `int()` na nich
    # spadne (chyba 500). Obri cislo nevadi - radek s takovym ID prostě neexistuje.
    if (
        cislo.isascii()
        and cislo.isdigit()
        and pokladni_kniha.presun(akce, zdroj, int(cislo), dolu=(kudy == "dolu"))
    ):
        sezeni.commit()
    return zpet(f"/akce/{akce_id}/pokladni-kniha")


@router.post("/akce/{akce_id}/pokladni-kniha/srovnat")
async def srovnat(request: Request, akce_id: int, sezeni: Databaze):
    """Zahodi rucni poradi a vrati knihu k razeni podle data."""
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    pokladni_kniha.srovnej_podle_data(akce)
    sezeni.commit()
    oznam(request, "Kniha je zase seřazená podle data.")
    return zpet(f"/akce/{akce_id}/pokladni-kniha")


@router.post("/akce/{akce_id}/pokladni-kniha/pridat")
async def pridat(
    request: Request,
    akce_id: int,
    data: Annotated[RadekKnihyForm | StavFormulare, Depends(formular_s_chybami(RadekKnihyForm))],
    sezeni: Databaze,
):
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    # Chyba formulare: stranka znovu, s chybou u pole a tim, co clovek napsal.
    if isinstance(data, StavFormulare):
        return _strana_knihy(request, sezeni, akce, data, FORMULAR_RADEK)
    poradi = dalsi_poradi(r.poradi for r in akce.pokladni_radky)
    sezeni.add(
        PokladniRadek(
            akce=akce,
            datum=data.datum,
            ucel=data.ucel or "Pohyb v pokladně",
            smer=SMER_PRIJEM if data.smer == SMER_PRIJEM else SMER_VYDEJ,
            castka=data.castka,
            poradi=poradi,
        )
    )
    sezeni.commit()
    oznam(request, "Řádek přidán.")
    return zpet(f"/akce/{akce_id}/pokladni-kniha")


@router.post("/akce/{akce_id}/pokladni-kniha/vratka")
async def pridat_vratku(
    request: Request,
    akce_id: int,
    data: Annotated[VratkaForm | StavFormulare, Depends(formular_s_chybami(VratkaForm))],
    sezeni: Databaze,
):
    """Vratka castecne zaplaceneho ucastnikovi - vzdy vydaj.

    Zapisuje se jen radek knihy; `Ucastnik.vratka` je jejich soucet, takze
    jeden ucastnik muze dostat vic vratek a nic se nerozejde. Vratka
    vetsi nez to, co ucastnik zaplatil, se ulozi, ale ohlasi se.
    """
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    if isinstance(data, StavFormulare):
        return _strana_knihy(request, sezeni, akce, data, FORMULAR_VRATKA)
    ucastnik = sezeni.get(Ucastnik, data.ucastnik_id) if data.ucastnik_id else None
    if ucastnik is None or ucastnik.akce_id != akce.id:
        return _strana_knihy(
            request,
            sezeni,
            akce,
            stav_s_chybami(request, ucastnik_id="Účastníka jsem nenašel. Vyber ho ze seznamu."),
            FORMULAR_VRATKA,
        )

    jmeno = " ".join(cast for cast in (ucastnik.jmeno, ucastnik.prijmeni) if cast)
    poradi = dalsi_poradi(r.poradi for r in akce.pokladni_radky)
    sezeni.add(
        PokladniRadek(
            akce=akce,
            ucastnik=ucastnik,
            datum=data.datum,
            ucel=f"Vratka – {jmeno}".strip(" –"),
            smer=SMER_VYDEJ,
            castka=data.castka,
            poradi=poradi,
        )
    )
    sezeni.commit()
    oznam(request, "Vratka přidána.")
    if ucastnik.zaplaceno and (ucastnik.vratka or 0) > ucastnik.zaplaceno:
        oznam(
            request,
            # Bez jmena: hlaska lezi v podepsane, ale nesifrovane cookie.
            f"Pozor: účastník dostává zpátky víc, než zaplatil "
            f"({koruny(ucastnik.vratka)} z {koruny(ucastnik.zaplaceno)}). Zkontroluj částku.",
            "chyba",
        )
    return zpet(f"/akce/{akce_id}/pokladni-kniha")


# Pole kazdeho zdroje radku: `<zdroj>_<pole>_<id>`, zdroj je sken, prijem nebo radek.
ZDROJE_KNIHY = ("sken", "prijem", "radek")
POLE_KNIHY = ("ucel", "datum", "castka", "smer")


def _uloz_radek_knihy(
    radky: dict[int, dict[str, str]], zaznam, zahlavi: str, pole_ucelu: str
) -> int:
    """Zapise pole jednoho radku knihy do `zaznam` (sken, prijem nebo rucni radek).
    Vraci pocet nectitelnych poli."""
    pole = radky.get(zaznam.id, {})
    # Co na strance nebylo, na to se nesaha. Priloha v knize radek nema,
    # takze bez teto pojistky by ji ukladani vymazalo datum i castku.
    # Prubezne ukladani navic posila jen zmenena pole, proto se
    # kazde pole bere samo, ne podle toho, jestli prislo datum.
    ucel = prazdne_na_none(pole.get(f"{zahlavi}_ucel"))
    if ucel:
        setattr(zaznam, pole_ucelu, ucel)
    if f"{zahlavi}_datum" in pole:
        zaznam.datum = na_datum(pole[f"{zahlavi}_datum"])
    necitelnych = 0
    if f"{zahlavi}_castka" in pole:
        zadano = CastkaKnihyForm.model_validate({"castka": pole[f"{zahlavi}_castka"]})
        necitelnych = zadano.necitelnych()
        if zadano.zmeny().get("castka") is not None:
            zaznam.castka = zadano.castka
    return necitelnych


@router.post("/akce/{akce_id}/pokladni-kniha/ulozit")
async def ulozit(request: Request, akce_id: int, sezeni: Databaze):
    """Zapise upravy zpatky do uctenek, prijmu a rucnich radku.

    Castka se prepisuje jen kdyz se ji podarilo precist - jinak by preklep
    v poli castku smazal. Kniha na rozdil od jinych tabulek prazdnou castku
    nemaze: sloupec nesmi byt prazdny. Nectitelne pole se zapocita do hlasky.
    """
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    formular = await nacti_formular(request)
    over_delky(
        formular,
        {
            "sken_ucel": Sken.nazev,
            "prijem_ucel": Prijem.popis,
            "radek_ucel": PokladniRadek.ucel,
        },
    )
    radky = radky_z_formulare(
        formular, [f"{zdroj}_{pole}" for zdroj in ZDROJE_KNIHY for pole in POLE_KNIHY]
    )
    necitelnych = 0

    # Uctenky: ucel platby je zaroven nazev skenu v ZIPu i v Excelu pro
    # hospodare - je to jedna hodnota na jednom miste, ne dve ruzne.
    for sken in akce.skeny:
        necitelnych += _uloz_radek_knihy(radky, sken, "sken", "nazev")

    for prijem in akce.prijmy:
        necitelnych += _uloz_radek_knihy(radky, prijem, "prijem", "popis")

    for radek in akce.pokladni_radky:
        # Cestakovy radek se v knize needituje - jeho castka je soucet jizd
        # a ucel jeho ucel cesty. Menit se smi jen na obrazovce cestaku, jinak
        # by se dve cisla teze veci rozesla. Sablona pole schovava, ale rozhodne
        # to server: schovane policko je pohodli, ne ochrana.
        if radek.je_z_cestaku:
            continue
        necitelnych += _uloz_radek_knihy(radky, radek, "radek", "ucel")
        smer = radky.get(radek.id, {}).get("radek_smer")
        if smer in (SMER_PRIJEM, SMER_VYDEJ):
            radek.smer = smer

    sezeni.commit()
    if (hotovo := prubezne_ulozeni(request, necitelnych)) is not None:
        return hotovo
    oznam(request, "Uloženo.")
    if necitelnych:
        oznam(request, hlaska_necitelnych(necitelnych), "chyba")
    return zpet(f"/akce/{akce_id}/pokladni-kniha")


@router.post("/pokladni-radek/{radek_id}/smazat")
async def smazat(request: Request, radek_id: int, sezeni: Databaze):
    radek = sezeni.get(PokladniRadek, radek_id)
    if radek is None:
        return await uz_smazano_kam(request)
    over_otevrenou(radek.akce)
    akce_id = radek.akce_id
    # Cestakovy radek z knihy nemizi sam - odebira se na obrazovce cestaku,
    # aby cestak nezustal "ve vydajich" bez radku, ktery by to dokladal.
    if radek.je_z_cestaku:
        oznam(
            request,
            "Tenhle řádek patří cestovnímu příkazu — odeber ho z výdajů přímo na cesťáku.",
            "chyba",
        )
        return zpet(await kam_dal(request, f"/akce/{akce_id}/pokladni-kniha"))
    ucastnik = radek.ucastnik
    sezeni.delete(radek)
    # Cislo dokladu vratky zmizi, az kdyz zadna vratka nezbyde - jinak by
    # smazani jednoho radku vymazalo doklad druhemu.
    if ucastnik is not None and not any(r is not radek for r in ucastnik.pokladni_radky):
        ucastnik.vratka_doklad = None
    sezeni.commit()
    oznam(request, "Řádek smazán.")
    return zpet(await kam_dal(request, f"/akce/{akce_id}/pokladni-kniha"))
