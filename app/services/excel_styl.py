"""Jednotny vzhled excelovych exportu.

Rozvrzeni tabulek je dane vzory hospodare (viz docstringy jednotlivych exportu)
a nesahá se na nej. Tenhle modul resi jen to, jak vytistena tabulka **vypada** -
barvy, pisma, mrizku a nastaveni stranky.

Barvy jsou tytez, jakymi svitit aplikace v prohlizeci (`app/static/app.css`,
brandmanual pristavu Kormorani). openpyxl chce barvu jako sest znaku RRGGBB
bez mrizky.

Pravidla, ktera plati stejne jako v aplikaci:

* Modra je nosna barva - zahlavi, nadpisy, souctove radky.
* Zelena znamena "v poradku", cervena "neco je spatne". Barvi se jimi **jen
  vysledek** (zustatek, balance sheet), ne kazde cislo - jinak barva ztrati
  vyznam.
* Vyplne jsou svetle. Tiskne se to na cernobile tiskarne, kde se z tmave
  vyplne stane sedy flek, pres ktery neni videt.

Jsou tu i pomocniky, ktere stejne cini vsechny exporty: zahlavi stranky,
datovy radek, souctovy pruh, zaokrouhleni na koruny a ulozeni do bajtu.
Rozvrzeni (co stoji ve kterem sloupci, co se slucuje) zustava v modulech
jednotlivych exportu - tam je rozdil skutecny, ne nahodny.
"""

from __future__ import annotations

import io
from decimal import ROUND_HALF_UP, Decimal
from typing import cast

from openpyxl import Workbook
from openpyxl.cell import Cell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.properties import PageSetupProperties
from openpyxl.worksheet.worksheet import Worksheet

from app.config import nastaveni
from app.models import Akce

# --- barvy -----------------------------------------------------------------

MODRA = "2A4983"  # --kormorani-blue
MODRA_TMAVA = "1F386A"  # --kormorani-blue-tmava
BILA = "FFFFFF"
PODKLAD_ZAHLAVI = MODRA
PODKLAD_SOUCTU = "E3EBF7"  # svetla modra, o odstin sytejsi nez pruh
PODKLAD_PRUHU = "F4F7FC"  # --blue-050, strida se po slozkach
LEM = "C3CCD8"  # --grey-200, svetlejsi nez cerna mrizka Excelu
SEDA_TEXT = "5D6B7A"  # --grey-600
ZELENA_TEXT = "005E26"  # --zelena-text
CERVENA_TEXT = "9E1C1C"  # --cervena-text

# --- pisma -----------------------------------------------------------------

NADPIS = Font(bold=True, size=16, color=MODRA)
PODNADPIS = Font(bold=True, size=11, color=MODRA)
NAZEV_AKCE = Font(bold=True, size=12, color=MODRA_TMAVA)
DROBNE = Font(size=9, color=SEDA_TEXT)
ZAHLAVI = Font(bold=True, size=10, color=BILA)
SOUCET = Font(bold=True, size=11, color=MODRA_TMAVA)

# --- vyplne, lemy, zarovnani ----------------------------------------------

VYPLN_ZAHLAVI = PatternFill("solid", fgColor=PODKLAD_ZAHLAVI)
VYPLN_SOUCTU = PatternFill("solid", fgColor=PODKLAD_SOUCTU)
VYPLN_PRUHU = PatternFill("solid", fgColor=PODKLAD_PRUHU)

_TENKA = Side(style="thin", color=LEM)
_SILNA = Side(style="medium", color=MODRA)

MRIZKA = Border(left=_TENKA, right=_TENKA, top=_TENKA, bottom=_TENKA)
MRIZKA_SOUCTU = Border(left=_TENKA, right=_TENKA, top=_SILNA, bottom=_TENKA)

NA_STRED = Alignment(horizontal="center", vertical="center", wrap_text=True)
DOPRAVA = Alignment(horizontal="right", vertical="center")
DOLEVA = Alignment(horizontal="left", vertical="center")

CISLO = "#,##0"
FORMAT_DATA = "dd.mm.yyyy"


def na_cele_koruny(castka: Decimal | None) -> int:
    """Hospodar pracuje v celych korunach.

    V databazi drzime presnou castku vcetne haleru; zaokrouhluje se az tady.
    """
    if castka is None:
        return 0
    return int(Decimal(castka).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def zapis(list_: Worksheet, radek: int, sloupec: int, hodnota=None) -> Cell:
    """Zapise hodnotu do bunky. **Kazdy text se uklada jako text.**

    openpyxl totiz bere kazdy retezec zacinajici „=“ jako vzorec a Excel by ho
    po otevreni spocital - `=HYPERLINK(...)` nebo `=WEBSERVICE(...)` v nazvu
    uctenky, jmene ucastnika nebo popisu prijmu by z listu posilalo udaje deti
    ven. Nastaveni `data_type = "s"` ulozi retezec jako obycejny text a Excel
    ukaze presne to, co uzivatel napsal. (Apostrof pred „=“ by nestacil -
    openpyxl ho ulozi jako soucast textu a Excel ho zobrazi.) Znaky „+“, „-“
    a „@“ na zacatku nevadi: jako vzorec je Excel bere jen pri rucnim psani,
    ne u ulozeneho textu. Cisla, data a `Decimal` zustavaji, jak jsou, aby
    fungovaly soucty.

    Pouzivej ji u kazde bunky, do ktere jde text z databaze, z formularu,
    z importu nebo od AI.
    """
    zapsana = cast(Cell, list_.cell(row=radek, column=sloupec, value=hodnota))
    if isinstance(hodnota, str):
        zapsana.data_type = "s"
    return zapsana


def bunka(list_: Worksheet, radek: int, sloupec: int, hodnota=None) -> Cell:
    """Bunka s rameckem. Ramecek dostava kazda bunka tabulky, at je videt mrizka
    i tam, kde nic neni - prazdna kolonka se pak da vyplnit rucne na papire."""
    zapsana = zapis(list_, radek, sloupec, hodnota)
    zapsana.border = MRIZKA
    return zapsana


def do_bajtu(sesit: Workbook) -> bytes:
    """Hotovy soubor XLSX pripraveny ke stazeni."""
    vystup = io.BytesIO()
    sesit.save(vystup)
    return vystup.getvalue()


def novy_sesit(nazev_listu: str) -> tuple[Workbook, Worksheet]:
    """Prazdny sesit s jednim listem. `Workbook().active` je pro typy nepovinne,
    ale novy sesit prvni list vzdy ma."""
    sesit = Workbook()
    list_ = cast(Worksheet, sesit.active)
    list_.title = nazev_listu
    return sesit, list_


def cislo_akce(list_: Worksheet, radek: int, sloupec: int, prefix: str | None) -> None:
    """Cislo akce do prvniho radku, hned za nazev akce.

    Prefix, kterym se cisluji doklady ("VPD TAB2026 01"), je zaroven cislo,
    pod kterym akci vedeme v tabulkach ve Scoutu. Na vytistenem papire je
    podle nej poznat, ke ktere akci vyuctovani patri, takze patri do hlavicky
    kazdeho exportu.

    Popisek i hodnota jsou v jedne bunce zamerne - vpravo uz je konec tabulky
    a kazdy dalsi zabrany sloupec by zmensil meritko tisku. Kdyz prefix
    vyplneny neni, bunka se vubec nezalozi; prazdne "Číslo akce:" nikomu
    nepomuze (stejne pravidlo jako u cisel dokladu, kde prazdny prefix vypadne
    i s mezerou).
    """
    prefix = (prefix or "").strip()
    if not prefix:
        return
    bunka = zapis(list_, radek, sloupec, f"Číslo akce: {prefix}")
    bunka.font = PODNADPIS
    bunka.alignment = DOLEVA


def nadpis_stranky(
    list_: Worksheet,
    akce: Akce,
    nadpis: str,
    *,
    sloupec_nadpisu: int,
    sloupec_strediska: int,
    sloupec_akce: int,
    sloupec_cisla: int,
) -> None:
    """Prvni radek: nazev tiskopisu, stredisko a hned za nim nazev a cislo akce.

    Bez nazvu akce je vytisteny papir k nerozeznani od jineho vyuctovani. Kam
    ktera cast pada, urcuje kazdy export sam (lisi se sirkou stranky); co se
    v nich slucuje, taky.
    """
    zapis(list_, 1, sloupec_nadpisu, nadpis).font = NADPIS
    zapis(list_, 1, sloupec_strediska, nastaveni.stredisko_nazev).font = DROBNE
    zapis(list_, 1, sloupec_akce, "Název akce:").font = PODNADPIS
    zapis(list_, 1, sloupec_akce + 1, akce.nazev).font = NAZEV_AKCE
    cislo_akce(list_, 1, sloupec_cisla, akce.prefix_dokladu)
    list_.row_dimensions[1].height = 26


def oznac_soucet(bunka: Cell) -> None:
    """Tucne modre pismo na svetle modre vyplni - bunka se souctem."""
    bunka.font = SOUCET
    bunka.fill = VYPLN_SOUCTU


def oblec_radek(
    list_: Worksheet,
    radek: int,
    *,
    za: int,
    zarovnani: dict[int, Alignment],
    podbarvit: bool = False,
    od_sloupce: int = 1,
    od_castek: int | None = None,
    format_cisel: bool = False,
) -> None:
    """Mrizka, zarovnani a podbarveni jednoho datoveho radku.

    `za` je cislo sloupce za poslednim (stejne jako u `range`), `zarovnani`
    mapuje sloupec na zarovnani (ostatni jdou doleva). `od_castek` je prvni
    sloupec, od ktereho uz jde o penize; ty patri doprava. Podbarveny radek je
    svetlejsi, at oko nesjede o radek vedle - tabulka je siroka pres celou
    stranku. Ktery radek se podbarvi (po radcich, po slozkach), rozhoduje
    volajici.
    """
    for sloupec in range(od_sloupce, za):
        zapsana = cast(Cell, list_.cell(row=radek, column=sloupec))
        zapsana.border = MRIZKA
        if od_castek is not None and sloupec >= od_castek:
            zapsana.alignment = DOPRAVA
        else:
            zapsana.alignment = zarovnani.get(sloupec, DOLEVA)
        if format_cisel and isinstance(zapsana.value, int):
            zapsana.number_format = CISLO
        if podbarvit:
            zapsana.fill = VYPLN_PRUHU


def souctovy_pruh(
    list_: Worksheet,
    radek: int,
    *,
    za: int,
    popisek_do: int,
    zarovnani_popisku: Alignment = DOPRAVA,
    zarovnani_hodnot: Alignment = NA_STRED,
) -> None:
    """Svetle modry pruh se silnou linkou nahore - konec tabulky.

    Sloupce pred `popisek_do` nesou popisek („SOUCET:“), od nej dal hodnoty.
    """
    for sloupec in range(1, za):
        zapsana = cast(Cell, list_.cell(row=radek, column=sloupec))
        oznac_soucet(zapsana)
        zapsana.border = MRIZKA_SOUCTU
        zapsana.alignment = zarovnani_popisku if sloupec < popisek_do else zarovnani_hodnot
    list_.row_dimensions[radek].height = 20


def obarvi_vysledek(bunka, hodnota: int) -> None:
    """Zeleny plus, cerveny minus. Jen na konecnych cislech, ne na kazdem radku."""
    barva = ZELENA_TEXT if hodnota >= 0 else CERVENA_TEXT
    bunka.font = Font(bold=True, size=11, color=barva)


def zahlavi_pruh(list_: Worksheet, prvni_radek: int, posledni_radek: int, sloupcu: int) -> None:
    """Modry pruh se sedlym bilym pismem pres cele zahlavi tabulky."""
    for radek in list_.iter_rows(
        min_row=prvni_radek, max_row=posledni_radek, min_col=1, max_col=sloupcu
    ):
        for bunka in radek:
            bunka.font = ZAHLAVI
            bunka.fill = VYPLN_ZAHLAVI
            bunka.alignment = NA_STRED
            bunka.border = MRIZKA
    for radek in range(prvni_radek, posledni_radek + 1):
        list_.row_dimensions[radek].height = 22


def nastav_tisk(list_: Worksheet, *, na_sirku: bool = False, opakovat: str = "") -> None:
    """A4, tabulka roztazena presne na sirku stranky, cislo stranky v pate.

    `fitToWidth=1` + `fitToHeight=0` znamena "zmensi to tak, aby se to veslo do
    sirky, a na vysku si to nataz pres kolik stranek chces". Bez
    `PageSetupProperties(fitToPage=True)` si toho Excel nevsimne.

    `opakovat` je rozsah radku zahlavi (treba "2:3"); zopakuje se na kazde
    dalsi vytistene strance, aby clovek vedel, ktery sloupec je ktery.
    """
    list_.page_setup.orientation = "landscape" if na_sirku else "portrait"
    list_.page_setup.paperSize = list_.PAPERSIZE_A4
    list_.page_setup.fitToWidth = 1
    list_.page_setup.fitToHeight = 0
    list_.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    list_.page_margins = PageMargins(left=0.4, right=0.4, top=0.6, bottom=0.5)
    list_.print_options.horizontalCentered = True
    if opakovat:
        list_.print_title_rows = opakovat
    paticka = list_.oddFooter
    if paticka is not None and paticka.right is not None:  # pro typy nepovinne, ve skutecnosti vzdy
        paticka.right.text = "Strana &P z &N"
        paticka.right.size = 8
        paticka.right.color = SEDA_TEXT
