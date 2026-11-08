"""Export pokladni knihy do Excelu.

Rozvrzeni je opsane z vzorove tabulky hospodare (nejsou soucasti repozitare), list Pokladni_kniha:

    radek 1   nazev tiskopisu, stredisko, nazev a cislo akce
    radek 2   Dne | Dokl. | Ucel platby | Pokladna (slouceno pres tri sloupce)
    radek 3   Prijem | Vydej | Zustatek
    radek 4+  data, sloupec A je poradove cislo radku
    niz       CELKEM a "Cislo stranky ___/___"

Hospodar pracuje v celych korunach, takze se kazdy radek zaokrouhli a zustatek
se pocita **az ze zaokrouhlenych hodnot** - jinak by sloupec nesedel, kdyby ho
nekdo secetl na kalkulacce. Stejny princip jako v excel.soucty_v_celych_korunach.

Vzhled (barvy, pisma, mrizka, nastaveni tisku) je v `excel_styl`; rozvrzeni
podle vzoru se nemeni.
"""

from __future__ import annotations

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from app.models import Akce
from app.services import excel_styl, pokladni_kniha
from app.services.excel_styl import na_cele_koruny
from app.services.soubory import bezpecny_nazev

# Sloupec "Dokl." nese cislo dokladu bez prefixu ("VPD 01") - prefix stoji
# jednou v hlavicce jako "Cislo akce". I tak je sloupec sirsi nez datum vedle
# nej, aby se "VPD 01" veslo na jeden radek.
SIRKY_SLOUPCU = {"A": 3.5, "B": 10.5, "C": 17.0, "D": 35.5, "E": 12.5, "F": 12.5, "G": 12.5}

PRVNI_DATOVY_RADEK = 4

# Sloupce tabulky: A je okraj, B..G viz hlavicka v `_zahlavi`.
SL_CISLO_RADKU = 1
SL_DATUM = 2
SL_DOKLAD = 3
SL_UCEL = 4
SL_PRIJEM = 5
SL_VYDEJ = 6
SL_ZUSTATEK = 7
POSLEDNI_SLOUPEC = SL_ZUSTATEK
PRVNI_SLOUPEC_CASTEK = SL_PRIJEM

# Cislo radku, datum a doklad na stred, ucel doleva; od sloupce Prijem dal jsou
# penize, ty patri doprava (viz `PRVNI_SLOUPEC_CASTEK`).
ZAROVNANI = {
    SL_CISLO_RADKU: excel_styl.NA_STRED,
    SL_DATUM: excel_styl.NA_STRED,
    SL_DOKLAD: excel_styl.NA_STRED,
}


def _zahlavi(list_: Worksheet, akce: Akce) -> None:
    excel_styl.nadpis_stranky(
        list_,
        akce,
        "Pokladní kniha",
        sloupec_nadpisu=2,
        sloupec_strediska=4,
        sloupec_akce=5,
        sloupec_cisla=8,
    )
    list_.merge_cells("B1:C1")
    list_.merge_cells("F1:G1")

    list_["B2"] = "Dne"
    list_["C2"] = "Dokl."
    list_["D2"] = "Účel platby"
    list_["E2"] = "Pokladna"
    list_["E3"] = "Příjem"
    list_["F3"] = "Výdej"
    list_["G3"] = "Zůstatek"

    for oblast in ("B2:B3", "C2:C3", "D2:D3", "E2:G2"):
        list_.merge_cells(oblast)

    excel_styl.zahlavi_pruh(list_, 2, 3, POSLEDNI_SLOUPEC)


def _zapis_radky(list_: Worksheet, akce: Akce) -> tuple[int, int, int, int]:
    """Zapise data. Vraci cislo radku za daty a soucty v celych korunach."""
    radek = PRVNI_DATOVY_RADEK
    prijmy = vydaje = zustatek = 0

    for poradi, polozka in enumerate(pokladni_kniha.sestav(akce)):
        prijem = na_cele_koruny(polozka.prijem) if polozka.prijem is not None else None
        vydej = na_cele_koruny(polozka.vydej) if polozka.vydej is not None else None
        prijmy += prijem or 0
        vydaje += vydej or 0
        zustatek += (prijem or 0) - (vydej or 0)

        excel_styl.zapis(list_, radek, SL_CISLO_RADKU, polozka.cislo)
        if polozka.datum:
            bunka = excel_styl.zapis(list_, radek, SL_DATUM, polozka.datum)
            bunka.number_format = excel_styl.FORMAT_DATA
        excel_styl.zapis(list_, radek, SL_DOKLAD, polozka.doklad)
        excel_styl.zapis(list_, radek, SL_UCEL, polozka.ucel)
        if prijem is not None:
            excel_styl.zapis(list_, radek, SL_PRIJEM, prijem)
        if vydej is not None:
            excel_styl.zapis(list_, radek, SL_VYDEJ, vydej)
        excel_styl.zapis(list_, radek, SL_ZUSTATEK, zustatek)

        # Kazdy druhy radek svetlejsi, at oko nesjede o radek vedle -
        # kniha je siroka pres celou stranku.
        excel_styl.oblec_radek(
            list_,
            radek,
            za=POSLEDNI_SLOUPEC + 1,
            zarovnani=ZAROVNANI,
            podbarvit=bool(poradi % 2),
            od_castek=PRVNI_SLOUPEC_CASTEK,
        )

        # Cerveny zustatek je varovani, ne ozdoba: v knize se nikdy nema stat,
        # ze je v pokladne min nez nula. Kladny zustatek zustava cerny, aby si
        # zelena drzela vyznam pro konecny vysledek dole.
        if zustatek < 0:
            excel_styl.obarvi_vysledek(list_.cell(row=radek, column=SL_ZUSTATEK), zustatek)
        radek += 1

    return radek, prijmy, vydaje, zustatek


def _zapis_celkem(list_: Worksheet, radek: int, *, prijmy: int, vydaje: int, zustatek: int) -> None:
    excel_styl.zapis(list_, radek, SL_DATUM, "CELKEM")
    list_.merge_cells(start_row=radek, start_column=SL_DATUM, end_row=radek, end_column=SL_UCEL)
    for sloupec, hodnota in ((SL_PRIJEM, prijmy), (SL_VYDEJ, vydaje), (SL_ZUSTATEK, zustatek)):
        list_.cell(row=radek, column=sloupec, value=hodnota)

    excel_styl.souctovy_pruh(
        list_,
        radek,
        za=POSLEDNI_SLOUPEC + 1,
        popisek_do=PRVNI_SLOUPEC_CASTEK,
        zarovnani_popisku=excel_styl.DOLEVA,
        zarovnani_hodnot=excel_styl.DOPRAVA,
    )
    excel_styl.obarvi_vysledek(list_.cell(row=radek, column=SL_ZUSTATEK), zustatek)

    excel_styl.zapis(list_, radek + 1, SL_DATUM, "Číslo stránky ___/___")
    list_.cell(row=radek + 1, column=SL_DATUM).font = excel_styl.DROBNE


def sestav_sesit(akce: Akce) -> Workbook:
    sesit, list_ = excel_styl.novy_sesit("Pokladní kniha")

    for sloupec, sirka in SIRKY_SLOUPCU.items():
        list_.column_dimensions[sloupec].width = sirka

    _zahlavi(list_, akce)
    radek_za_daty, prijmy, vydaje, zustatek = _zapis_radky(list_, akce)
    _zapis_celkem(list_, radek_za_daty, prijmy=prijmy, vydaje=vydaje, zustatek=zustatek)

    for radek in list_.iter_rows(min_row=PRVNI_DATOVY_RADEK, min_col=5, max_col=7):
        for bunka in radek:
            if isinstance(bunka.value, int):
                bunka.number_format = excel_styl.CISLO

    excel_styl.nastav_tisk(list_, opakovat="2:3")
    return sesit


def do_bajtu(akce: Akce) -> bytes:
    """Hotovy soubor XLSX pripraveny ke stazeni."""
    return excel_styl.do_bajtu(sestav_sesit(akce))


def nazev_souboru(akce: Akce) -> str:
    return f"{bezpecny_nazev(akce.nazev)}_pokladni kniha.xlsx"
