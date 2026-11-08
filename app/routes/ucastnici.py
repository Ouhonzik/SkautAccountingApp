"""Ucastnici akce - import z exportu h.skauting.cz.

Data obsahuji adresy a data narozeni deti. Zpracovavaji se lokalne a nikdy
neodchazi do Gemini.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse

from app.config import nastaveni
from app.formulare import UcastnikRadekForm, over_delky
from app.models import Akce, Ucastnik, dnes
from app.routes.spolecne import (
    MAX_POLI_UCASTNICI,
    Databaze,
    datum_narozeni_z_formulare,
    hlaska_dat_narozeni,
    hlaska_necitelnych,
    je_prubezne,
    nacti_akci,
    nacti_formular,
    nacti_otevrenou_akci,
    over_otevrenou,
    prazdne_na_none,
    prubezne_ulozeni,
    radky_z_formulare,
    uz_smazano_kam,
)
from app.sablony import oznam, strana, zpet
from app.services.import_ucastniku import ChybaImportu, nacti_ucastniky
from app.services.soubory import ChybaObsahu, precti_s_limitem

router = APIRouter()


@router.get("/akce/{akce_id}/ucastnici", response_class=HTMLResponse)
async def prehled(request: Request, akce_id: int, sezeni: Databaze):
    akce = nacti_akci(sezeni, akce_id)
    return strana(request, "ucastnici.html", akce=akce)


def _nejpozdeji_narozeni(akce: Akce) -> dt.date:
    """Nejpozdejsi pripustne datum narozeni: prvni den akce, bez terminu dnesek."""
    return akce.datum_od or dnes()


@router.post("/akce/{akce_id}/ucastnici/import")
async def importovat(
    request: Request,
    akce_id: int,
    sezeni: Databaze,
    soubor: UploadFile = File(...),
):
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    try:
        obsah = await precti_s_limitem(soubor, nastaveni.max_velikost_xlsx_mb)
        necitelne: Counter[str] = Counter()
        # Narozeni po zacatku akce nedava smysl; bez terminu dnesek.
        ucastnici = nacti_ucastniky(obsah, necitelne, _nejpozdeji_narozeni(akce))
    except ChybaObsahu as chyba:
        oznam(request, f"Soubor {chyba}.", "chyba")
        return zpet(f"/akce/{akce_id}/ucastnici")
    except ChybaImportu as chyba:
        oznam(request, str(chyba), "chyba")
        return zpet(f"/akce/{akce_id}/ucastnici")

    # Import nahrazuje predchozi seznam - jinak by se pri opakovanem nahrani
    # deti zdvojily. Prirazeni celeho seznamu stara jmena smaze samo, protoze
    # vazba `akce.ucastnici` ma `delete-orphan`.
    akce.ucastnici = ucastnici
    sezeni.commit()

    oznam(request, f"Naimportováno {len(ucastnici)} účastníků.")
    # Jen pocty - hodnoty z bunek jsou udaje deti.
    if pocet := necitelne["hotove"]:
        ucastniku = "účastníka" if pocet == 1 else "účastníků"
        oznam(
            request,
            f"U {pocet} {ucastniku} jsem nepřečetl zaplacenou částku — doplň ji v tabulce.",
            "chyba",
        )
    if pocet := necitelne["datum_narozeni"]:
        ucastniku = "účastníka" if pocet == 1 else "účastníků"
        oznam(
            request,
            f"U {pocet} {ucastniku} jsem nepřečetl datum narození (nebo vyšlo po začátku "
            "akce) — doplň ho v tabulce.",
            "chyba",
        )
    if pocet := necitelne["text"]:
        bunek = "buňku" if pocet == 1 else "buněk"
        oznam(
            request,
            f"Vynechal jsem {pocet} {bunek} s příliš dlouhým textem — doplň je v tabulce.",
            "chyba",
        )
    return zpet(f"/akce/{akce_id}/ucastnici")


# Pole, ktera jdou u ucastnika prepsat tuztickou. Klic ve formulari je
# "<pole>_<id ucastnika>".
TEXTOVA_POLE = (
    "jmeno",
    "prijmeni",
    "prezdivka",
    "ulice",
    "mesto",
    "psc",
    "jednotka",
    "hotove_doklad",
    "prevodem_doklad",
    "vouchery_doklad",
    "vratka_doklad",
)
# Prazdne cislo znamena "spocitej z dochazky", proto se uklada None, ne nula.
CISELNA_POLE = ("osobodny", "detodny")
PENEZNI_POLE = ("hotove", "prevodem", "vouchery")


def _dochazka(formular, akce, ucastnik, prubezne: bool) -> None:
    """Prepise dny nepritomnosti podle zaskrtavatek v mrizce.

    Obycejne odeslani: neodeslane zaskrtavatko znamena "chybel" - prohlizec
    posila jen zaskrtnuta. Prubezne ukladani posila jen zmenena zaskrtavatka
: zaskrtnuto prijde jako "on", odskrtnuto jako prazdna hodnota a den,
    ktery neprisel, zustava, jak je.
    Bez terminu akce mrizka na strance neni, takze by prazdny formular smazal
    uz zapsanou dochazku; proto se v tom pripade nesaha na nic.
    """
    if not akce.ma_termin:
        return
    chybi = ucastnik.chybejici_dny & set(akce.dny)
    for den in akce.dny:
        klic = f"den_{ucastnik.id}_{den.isoformat()}"
        if klic in formular:
            pritomen = bool(formular[klic])
        elif prubezne:
            continue
        else:
            pritomen = False
        if pritomen:
            chybi.discard(den)
        else:
            chybi.add(den)
    ucastnik.zapis_chybejici(chybi)


@router.post("/akce/{akce_id}/ucastnici/ulozit")
async def ulozit(request: Request, akce_id: int, sezeni: Databaze):
    """Ulozi rucni opravy udaju a dochazku - to, co Honza prepsal v tabulce.

    Prazdne cislo se smaze (osobodny pak vypocte dochazka), nectitelne nechá
    puvodni hodnotu a zapocita se do hlasky.
    """
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    formular = await nacti_formular(request, MAX_POLI_UCASTNICI)
    over_delky(formular, {jmeno: getattr(Ucastnik, jmeno) for jmeno in TEXTOVA_POLE})
    radky = radky_z_formulare(
        formular, (*TEXTOVA_POLE, *CISELNA_POLE, *PENEZNI_POLE, "datum_narozeni")
    )
    necitelnych = 0
    nepripustnych_dat = 0
    prubezne = je_prubezne(request)

    for ucastnik in akce.ucastnici:
        # Co ve formulari neni, to se nemeni.
        pole = radky.get(ucastnik.id, {})
        for jmeno in TEXTOVA_POLE:
            if jmeno in pole:
                setattr(ucastnik, jmeno, prazdne_na_none(pole[jmeno]))
        zadano = UcastnikRadekForm.model_validate(pole)
        necitelnych += zadano.necitelnych()
        for jmeno, hodnota in zadano.zmeny().items():
            setattr(ucastnik, jmeno, hodnota)
        if "datum_narozeni" in pole:
            ucastnik.datum_narozeni, ok = datum_narozeni_z_formulare(
                pole["datum_narozeni"], ucastnik.datum_narozeni, _nejpozdeji_narozeni(akce)
            )
            nepripustnych_dat += not ok
        _dochazka(formular, akce, ucastnik, prubezne)

    sezeni.commit()
    if (hotovo := prubezne_ulozeni(request, necitelnych, nepripustnych_dat)) is not None:
        return hotovo
    oznam(request, "Uloženo.")
    if necitelnych:
        oznam(request, hlaska_necitelnych(necitelnych), "chyba")
    if nepripustnych_dat:
        oznam(request, hlaska_dat_narozeni(nepripustnych_dat), "chyba")
    return zpet(f"/akce/{akce_id}/ucastnici")


@router.post("/akce/{akce_id}/ucastnici/smazat")
async def smazat_vse(request: Request, akce_id: int, sezeni: Databaze):
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    pocet = len(akce.ucastnici)
    for ucastnik in list(akce.ucastnici):
        sezeni.delete(ucastnik)
    sezeni.commit()
    oznam(request, f"Seznam účastníků smazán ({pocet}).")
    return zpet(f"/akce/{akce_id}/ucastnici")


@router.post("/ucastnik/{ucastnik_id}/smazat")
async def smazat(request: Request, ucastnik_id: int, sezeni: Databaze):
    ucastnik = sezeni.get(Ucastnik, ucastnik_id)
    if ucastnik is None:
        return await uz_smazano_kam(request)
    over_otevrenou(ucastnik.akce)
    akce_id = ucastnik.akce_id
    sezeni.delete(ucastnik)
    sezeni.commit()
    # Hlaska, ze radek opravdu zmizel. Bez jmena: hlaska lezi v cookie
    # session, ktera je jen podepsana, ne sifrovana (viz `oznam`, B-047).
    oznam(request, "Účastník smazán.")
    return zpet(f"/akce/{akce_id}/ucastnici")
