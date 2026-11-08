"""Export seznamu obdarovanych do Excelu.

Rozvrzeni je opsane z vzorove tabulky hospodare (nejsou soucasti repozitare), list
Seznam_obdarovanych:

    radek 1   nazev tiskopisu, stredisko, nazev a cislo akce
    radek 2   C. | Jmeno | Prijmeni | Prezdivka | Ulice | Mesto | PSC |
              Datum narozeni | Vec
    radek 3+  jeden radek na dar

**Odchylka od vzoru, vedoma:** vzor ma zahlavi slouceny pres radky 2 az 4,
takze je to na papire silny pruh. Tady je zahlavi jednoradkove - pod nadpisy
nejsou zadne podnadpisy (na rozdil od seznamu ucastniku, kde se pod
"Poukazane prostredky" jeste vetvi Kc a Doklad), takze by to byly jen dva
prazdne radky navic.

**Druha odchylka od vzoru, taky vedoma (Honza, 1. 9. 2026):** vzor ma na
kazdy dar vlastni radek, takze kdo dostal tri veci, je v nem trikrat. Tady je
radek jeden clovek a jeho veci jdou do sloupce Vec za sebou ("mic, cepice,
odznak"). Duvod: veci se rozdavaji pretahovanim po obrazovce, kde je clovek
taky jednou, a seznam je takhle kratsi a citelnejsi.
"""

from __future__ import annotations

from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.models import Akce
from app.services import excel_styl
from app.services.soubory import bezpecny_nazev

# Sloupce, jak jdou za sebou, spolu se sirkou v jednotkach Excelu.
# Sirky jsou ze vzoru - Vec je zamerne siroka, aby se do ni veslo
# "pohar za etapovku vcetne stuhy" bez zalamovani.
SLOUPCE = (
    ("Č.", 6),
    ("Jméno", 17),
    ("Příjmení", 18),
    ("Přezdívka", 12),
    ("Ulice", 18),
    ("Město", 24),
    ("PSČ", 9),
    ("Datum narození", 17),
    ("Věc", 50),
)

SLOUPEC_DATA_NAROZENI = 8
RADEK_ZAHLAVI = 2
PRVNI_DATOVY_RADEK = 3

# Cislo a datum narozeni na stred, texty doleva - stejne jako v Seznamu ucastniku.
ZAROVNANI = {1: excel_styl.NA_STRED, SLOUPEC_DATA_NAROZENI: excel_styl.NA_STRED}


def _zahlavi(list_: Worksheet, akce: Akce) -> None:
    excel_styl.nadpis_stranky(
        list_,
        akce,
        "Seznam obdarovaných",
        sloupec_nadpisu=1,
        sloupec_strediska=4,
        sloupec_akce=8,
        sloupec_cisla=10,
    )
    list_.merge_cells("A1:C1")

    for sloupec, (nadpis, sirka) in enumerate(SLOUPCE, start=1):
        excel_styl.bunka(list_, RADEK_ZAHLAVI, sloupec, nadpis)
        list_.column_dimensions[get_column_letter(sloupec)].width = sirka
    excel_styl.zahlavi_pruh(list_, RADEK_ZAHLAVI, RADEK_ZAHLAVI, len(SLOUPCE))


def _zapis_radky(list_: Worksheet, akce: Akce) -> None:
    for poradi, obdarovany in enumerate(akce.obdarovani, start=1):
        radek = PRVNI_DATOVY_RADEK + poradi - 1
        udaje = (
            poradi,
            obdarovany.jmeno,
            obdarovany.prijmeni,
            obdarovany.prezdivka,
            obdarovany.ulice,
            obdarovany.mesto,
            obdarovany.psc,
            obdarovany.datum_narozeni,
            obdarovany.veci,
        )
        for sloupec, hodnota in enumerate(udaje, start=1):
            bunka = excel_styl.bunka(list_, radek, sloupec, hodnota)
            if sloupec == SLOUPEC_DATA_NAROZENI and hodnota is not None:
                bunka.number_format = excel_styl.FORMAT_DATA
        excel_styl.oblec_radek(
            list_,
            radek,
            za=len(SLOUPCE) + 1,
            zarovnani=ZAROVNANI,
            podbarvit=poradi % 2 == 0,
        )


def sestav_sesit(akce: Akce) -> Workbook:
    sesit, list_ = excel_styl.novy_sesit("Seznam obdarovaných")

    _zahlavi(list_, akce)
    _zapis_radky(list_, akce)
    excel_styl.nastav_tisk(list_, na_sirku=True, opakovat=f"{RADEK_ZAHLAVI}:{RADEK_ZAHLAVI}")

    return sesit


def do_bajtu(akce: Akce) -> bytes:
    """Hotovy soubor XLSX pripraveny ke stazeni."""
    return excel_styl.do_bajtu(sestav_sesit(akce))


def nazev_souboru(akce: Akce) -> str:
    return f"{bezpecny_nazev(akce.nazev)}_seznam obdarovanych.xlsx"
