"""Nacteni ucastniku z exportu h.skauting.cz.

Soubor obsahuje adresy a data narozeni deti. Zpracovava se vyhradne tady
a lokalne - nikdy se neposila do Gemini a nikdy nekonci v gitu.

Sloupce se hledaji podle nazvu v hlavicce, ne podle poradi, aby import prezil
drobnou zmenu exportu.
"""

from __future__ import annotations

import io
import logging
import zipfile
from collections import Counter
from datetime import date, datetime
from decimal import Decimal

from openpyxl import load_workbook

from app.config import nastaveni

# Pravidla cteni castky jsou jedna pro formulare i import. Sluzba tu
# bere z vrstvy formularu - rozdeleni na samostatne moduly ceka v K-068.
from app.formulare import castka_z_bunky, mez_delky
from app.models import Ucastnik, dnes, pripustne_narozeni
from app.services import soubory

log = logging.getLogger(__name__)

# nazev v hlavicce (male pismeno, bez mezer navic) -> pole v databazi
SLOUPCE = {
    "jméno": "jmeno",
    "příjmení": "prijmeni",
    "přezdívka": "prezdivka",
    "ulice": "ulice",
    "město": "mesto",
    "psč": "psc",
    "datum narození": "datum_narozeni",
    "jednotka": "jednotka",
    "oddíl": "jednotka",
    "zaplaceno": "hotove",
}

# Osobodny a detodny se zamerne neimportuji, i kdyz je export obsahuje:
# pocitaji se z dochazky a rucne vyplnene cislo by ten vypocet natrvalo
# prebilo. Prepsat je jde primo v tabulce ucastniku.

DATOVA_POLE = {"datum_narozeni"}
PENEZNI_POLE = {"hotove"}

# Do kolikateho radku se jeste hleda hlavicka. Export ji ma hned na prvnim
# radku, rucne upravene soubory mivaji nad ni par radku s nazvem akce.
MAX_RADKU_HLAVICKY = 20

# Nejvic ucastniku v jednom souboru. Tabor ma realne desitky az par
# stovek deti; soubor s milionem radku (strop Excelu) by zahltil pamet i
# databazi. Radky bez jmena se do poctu neberou, ale prochazi se jen
# `MAX_RADKU_NA_UCASTNIKA`-nasobek limitu, aby soubor s milionem prazdnych
# radku neprochazel cely.
MAX_UCASTNIKU = 1000
MAX_RADKU_NA_UCASTNIKA = 10


class ChybaImportu(Exception):
    """Srozumitelna chyba, kterou lze ukazat uzivateli."""


def _prilis_mnoho() -> ChybaImportu:
    return ChybaImportu(
        f"Soubor má víc než {MAX_UCASTNIKU} účastníků — je to opravdu seznam z h.skauting.cz?"
    )


def _nazev(hodnota) -> str:
    """Text bunky hlavicky pro porovnani - mala pismena, jedna mezera mezi slovy."""
    return " ".join(str(hodnota).split()).lower() if hodnota is not None else ""


def _text(hodnota) -> str:
    """Excel vraci cisla jako float a data jako datetime.

    Bez prevodu by z PSC 60200 vzniklo '60200.0' a z data narozeni
    '2006-05-22 00:00:00'.
    """
    if isinstance(hodnota, date):
        return hodnota.strftime("%d.%m.%Y")
    if isinstance(hodnota, float) and hodnota.is_integer():
        return str(int(hodnota))
    return str(hodnota).strip()


# Tvary, ve kterych datum narozeni z exportu chodi. Bunka byva bud skutecne
# datum, nebo text - a lidi ho pisou ruzne.
TVARY_DATA = ("%d.%m.%Y", "%d. %m. %Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%y")


def _datum(hodnota) -> date | None:
    """Datum narozeni jako skutecne datum. None, kdyz se precist neda.

    Musi to byt datum, ne text - z data narozeni se pocita vek a z nej detoden.
    Necitelnou hodnotu je lepsi zahodit nez z ni pocitat nesmyslny vek; import
    ji spocita a ohlasi, aby se dala doplnit rucne.
    """
    if isinstance(hodnota, datetime):
        return hodnota.date()
    if isinstance(hodnota, date):
        return hodnota
    text = " ".join(str(hodnota).split())
    for tvar in TVARY_DATA:
        try:
            # Jen datum narozeni, bez casu - casova zona by tu nic neznamenala.
            datum = datetime.strptime(text, tvar).date()  # noqa: DTZ007
        except ValueError:
            continue
        # Dvouciferny rok 00-68 Python cte jako 2000-2068: dospely zapsany
        # "1.1.65" by dostal rok 2065 a z vedouciho by byl "bud dite".
        # Narozeni v budoucnosti nedava smysl nikdy, tak se posune o 100 let zpet.
        if tvar.endswith("%y") and datum > dnes():
            datum = datum.replace(year=datum.year - 100)
        return datum
    return None


def _penize(hodnota) -> Decimal | None:
    """Castka z bunky; prazdna bunka je None, `ValueError`, kdyz se precist neda
    (text, NaN, zaporne)."""
    return castka_z_bunky(hodnota)


def _hlavicka(radek: tuple) -> dict[int, str] | None:
    """Z radku hodnot udela mapu poradi sloupce -> pole. None, kdyz to hlavicka neni."""
    mapa = {
        index: SLOUPCE[nazev]
        for index, hodnota in enumerate(radek)
        if (nazev := _nazev(hodnota)) in SLOUPCE
    }
    # Bez jmena a prijmeni to hlavicka seznamu ucastniku neni.
    return mapa if {"jmeno", "prijmeni"} <= set(mapa.values()) else None


def _hodnoty(
    radek: tuple, mapa: dict[int, str], necitelne: Counter[str], nejpozdeji: date
) -> dict[str, object]:
    """Hodnoty jednoho radku. Nectitelnou castku nebo datum narozeni vynecha
    a zapocita do `necitelne` (jen pocet podle pole, nikdy hodnotu - jde o udaje
    deti). Textova bunka delsi nez sloupec se zapocita pod klic `text`. Datum narozeni po `nejpozdeji` nebo pred rokem 1900 je taky nectitelne."""
    hodnoty: dict[str, object] = {}
    for index, pole in mapa.items():
        hodnota = radek[index] if index < len(radek) else None
        if hodnota in (None, ""):
            continue
        if pole in DATOVA_POLE:
            datum = _datum(hodnota)
            if datum is None or not pripustne_narozeni(datum, nejpozdeji):
                necitelne[pole] += 1
            else:
                hodnoty[pole] = datum
        elif pole in PENEZNI_POLE:
            try:
                hodnoty[pole] = _penize(hodnota)
            except ValueError:
                necitelne[pole] += 1
        else:
            text = _text(hodnota)
            # Delku SQLite nehlida, formular ucastniku ji hlida, tak ji hlida
            # i import. Dlouhy text se neorezava (usekla by se treba ulice
            # tise): bunka zustane prazdna a pocet jde do hlasky jako u necitelnych cisel.
            if len(text) > mez_delky(getattr(Ucastnik, pole)):
                necitelne["text"] += 1
            else:
                hodnoty[pole] = text
    return hodnoty


def _precti_radky(
    data: bytes, necitelne: Counter[str], nejpozdeji: date
) -> tuple[dict[int, str] | None, list[Ucastnik]]:
    """Otevre sesit a precte z nej hlavicku a ucastniky. Muze vyhodit cokoli,
    co openpyxl a XML parser pri poskozenem souboru vyhodi - zachytava to
    volajici. V rezimu `read_only` se XML listu cte az pri `iter_rows`, takze
    poskozeny list spadne az v cyklu, ne pri otevreni."""
    sesit = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    mapa: dict[int, str] | None = None
    ucastnici: list[Ucastnik] = []

    # Jeden pruchod: dokud nemame hlavicku, hledame ji; potom uz cteme ucastniky.
    # Cteme hodnoty (`values_only`), ne bunky - v rezimu read_only jsou nevyplnene
    # bunky objekty EmptyCell, ktere nemaji ani cislo sloupce.
    list_ = sesit.active
    if list_ is None:
        raise ValueError("sešit nemá žádný list")
    for cislo_radku, radek in enumerate(list_.iter_rows(values_only=True), start=1):
        if mapa is None:
            if cislo_radku > MAX_RADKU_HLAVICKY:
                break
            mapa = _hlavicka(radek)
            continue

        if cislo_radku > MAX_RADKU_HLAVICKY + MAX_RADKU_NA_UCASTNIKA * MAX_UCASTNIKU:
            raise _prilis_mnoho()
        # Spocitat se musi hned, aby se zbytek obrovskeho souboru vubec nectl.
        hodnoty = _hodnoty(radek, mapa, necitelne, nejpozdeji)
        if not hodnoty.get("jmeno") and not hodnoty.get("prijmeni"):
            continue  # prazdny radek predtistene sablony
        if len(ucastnici) >= MAX_UCASTNIKU:
            raise _prilis_mnoho()
        ucastnici.append(Ucastnik(poradi=len(ucastnici), **hodnoty))
    return mapa, ucastnici


def nacti_ucastniky(
    data: bytes,
    necitelne: Counter[str] | None = None,
    nejpozdeji: date | None = None,
) -> list[Ucastnik]:
    """Precte soubor a vrati ucastniky. Neuklada je a nenavazuje na akci.

    Kdyz volajici preda `necitelne`, doplni se do nej, u kolika bunek podle pole
    (`hotove`, `datum_narozeni`) se hodnotu precist nepodarilo - bunka zustane
    prazdna a volajici to ma ohlasit, jinak by prazdna platba vypadala jako
    "nezaplatil". `nejpozdeji` je nejpozdejsi pripustne datum narozeni
    (zacatek akce; bez nej dnesek) - pozdejsi se bere jako necitelne.
    Vic nez `MAX_UCASTNIKU` ucastniku je `ChybaImportu`.

    Navazani na akci dela az routa - kdyby se ucastnici pripojili uz tady,
    ocitli by se v `akce.ucastnici` drive, nez se stary seznam smaze.
    """
    # xlsx je taky ZIP archiv - sharedStrings.xml v nem muze byt samo male,
    # ale po rozbaleni zabrat stovky MB pameti ("zip bomba", B-037c). Spatny
    # ZIP se resi az nize pri `load_workbook`, tady se jen hlida velikost.
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archiv:
            rozbaleno = soubory.rozbalena_velikost_zipu(archiv)
    except zipfile.BadZipFile:
        rozbaleno = 0
    if rozbaleno > nastaveni.max_rozbalena_velikost_mb * 1024 * 1024:
        raise ChybaImportu(
            f"Soubor po rozbalení zabírá víc než {nastaveni.max_rozbalena_velikost_mb} MB "
            "— je poškozený, nebo to není platný export z h.skauting.cz."
        )

    try:
        mapa, ucastnici = _precti_radky(
            data,
            necitelne if necitelne is not None else Counter(),
            nejpozdeji or dnes(),
        )
    except ChybaImportu:
        raise
    except Exception as chyba:  # noqa: BLE001 - poskozeny soubor nesmi shodit stranku
        # Jen typ vyjimky: jeji text z openpyxl muze obsahovat hodnotu bunky,
        # tedy udaj ditete, a uzivateli patri obecna hlaska.
        log.warning("Import účastníků: soubor se nepodařilo přečíst (%s)", type(chyba).__name__)
        raise ChybaImportu(
            "Soubor se nepodařilo přečíst. Ulož ho znovu z h.skauting.cz jako .xlsx "
            "a zkus to znovu."
        ) from None

    if mapa is None:
        raise ChybaImportu(
            "V souboru jsem nenašel hlavičku se sloupci Jméno a Příjmení. "
            "Je to opravdu export účastníků z h.skauting.cz?"
        )
    if not ucastnici:
        raise ChybaImportu("V souboru nejsou žádní účastníci.")
    return ucastnici
