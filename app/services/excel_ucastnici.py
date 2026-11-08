"""Export seznamu ucastniku do Excelu.

Sesit ma dva listy:

    Seznam ucastniku   osobni udaje a poukazane prostredky
    Dochazka           mrizka dnu akce, jeden radek na cloveka

Rozvrzeni prvniho listu je opsane z vzorove tabulky hospodare (nejsou soucasti repozitare), list
Seznam_ucastniku_akce:

    radek 1   nazev tiskopisu, stredisko, nazev a cislo akce
    radek 2   C. | Jmeno | ... | Poukazane financni prostredky | Spec.
    radek 3   Hotove | Prevodem | Dotace/vouchery
    radek 4   Kc | Doklad (u kazde dvojice)
    radek 5+  jeden radek na ucastnika
    niz       SOUCET

**Odchylky od vzoru, vsechny vedome:**

1. Denni ucast je ve vzoru prilepena vpravo za osobni udaje a Cislo, Jmeno
   a Prijmeni si vpravo znovu opakuje pres vzorce (`=A5`). Tady ma vlastni
   list: tabulka osobnich udaju je uz tak sirsi nez stranka a mrizka dnu ji
   protahovala do nekonecna. Osobodny a detodny zustavaji na obou listech,
   takze se cislo da zkontrolovat, aniz clovek prepina.
2. Pritomnost se znaci lomitkem (`/`), ne jednickou - je to skrtnuti tuzkou
   na papire, ne cislo do soucotve tabulky.

Plati tu stejne pravidlo jako u ostatnich exportu: hospodar pracuje v celych
korunach, takze se kazda castka zaokrouhli uz pri zapisu. Vzhled (barvy,
pisma, mrizka, nastaveni tisku) je v `excel_styl`.
"""

from __future__ import annotations

from typing import NamedTuple

from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.models import Akce, Ucastnik
from app.services import excel_styl
from app.services.excel_styl import na_cele_koruny
from app.services.soubory import bezpecny_nazev

# Sloupce leve casti, jak jdou za sebou, spolu se sirkou v jednotkach Excelu.
OSOBNI_UDAJE = (
    ("Č.", 4),
    ("Jméno", 14),
    ("Příjmení", 16),
    ("Přezdívka", 14),
    ("Ulice", 22),
    ("Město", 16),
    ("PSČ", 8),
    ("Datum narození", 14),
    ("Oddíl", 12),
    ("Osobodny", 10),
    ("Děťodny", 10),
)

SLOUPEC_DATA_NAROZENI = 8
SLOUPEC_OSOBODNY = 10
SLOUPEC_DETODNY = 11


class Sloupce(NamedTuple):
    """Kde na listu 1 zacinaji bloky, jejichz poloha zavisi na poctu zpusobu platby."""

    platby: int  # prvni sloupec s platbami (Kc)
    spec: int  # sloupec "Spec."
    konec: int  # prvni sloupec ZA tabulkou (tabulka konci o jeden driv)


# Dvojice Kc + Doklad pod spolecnym nadpisem: pole v databazi -> popisek ve vzoru.
ZPUSOBY_PLATBY = (
    ("hotove", "Hotově"),
    ("prevodem", "Převodem"),
    ("vouchery", "Dotace/vouchery"),
    ("vratka", "Vratka"),
)

PRVNI_DATOVY_RADEK = 5

# Dochazka: uvodni sloupce pred mrizkou dnu a znacka pritomnosti.
DOCHAZKA_SLOUPCE = (("Č.", 4), ("Jméno", 16), ("Příjmení", 18), ("Přezdívka", 14))
DOCHAZKA_RADEK_ZAHLAVI = 2
DOCHAZKA_PRVNI_DATOVY_RADEK = 3
BYL_TU = "/"

# Zarovnani leve casti: cislo, datum narozeni a oba dnove sloupce na stred,
# texty doleva. Castky se zarovnavaji doprava zvlast, viz `_oblec_radek`.
ZAROVNANI_UDAJU = {
    1: excel_styl.NA_STRED,
    SLOUPEC_DATA_NAROZENI: excel_styl.NA_STRED,
    SLOUPEC_OSOBODNY: excel_styl.NA_STRED,
    SLOUPEC_DETODNY: excel_styl.NA_STRED,
}


def _nadpis_stranky(list_: Worksheet, akce: Akce, nadpis: str, *, sloupec_akce: int) -> None:
    """Prvni radek: nazev tiskopisu vlevo, stredisko uprostred, akce vpravo.

    Za nazvem akce jeste cislo akce, at se da vytisteny list priradit
    k radku v tabulkach ve Scoutu."""
    excel_styl.nadpis_stranky(
        list_,
        akce,
        nadpis,
        sloupec_nadpisu=1,
        sloupec_strediska=4,
        sloupec_akce=sloupec_akce,
        sloupec_cisla=sloupec_akce + 2,
    )
    list_.merge_cells("A1:C1")


# --- list 1: seznam ucastniku ---------------------------------------------


def _zahlavi(list_: Worksheet, akce: Akce) -> Sloupce:
    """Napise hlavicku a vrati, ve kterem sloupci ktery blok zacina."""
    _nadpis_stranky(list_, akce, "Přehled účastníků", sloupec_akce=len(OSOBNI_UDAJE) - 1)

    sloupec = 1
    for nadpis, sirka in OSOBNI_UDAJE:
        excel_styl.bunka(list_, 2, sloupec, nadpis)
        list_.column_dimensions[get_column_letter(sloupec)].width = sirka
        list_.merge_cells(start_row=2, start_column=sloupec, end_row=4, end_column=sloupec)
        sloupec += 1

    # Poukazane prostredky: spolecny nadpis pres vsechny dvojice, pod nim nazev
    # zpusobu platby a uplne dole "Kc" a "Doklad".
    prvni_platba = sloupec
    for _, popisek in ZPUSOBY_PLATBY:
        excel_styl.bunka(list_, 3, sloupec, popisek)
        list_.merge_cells(start_row=3, start_column=sloupec, end_row=3, end_column=sloupec + 1)
        excel_styl.bunka(list_, 4, sloupec, "Kč")
        excel_styl.bunka(list_, 4, sloupec + 1, "Doklad")
        list_.column_dimensions[get_column_letter(sloupec)].width = 10
        list_.column_dimensions[get_column_letter(sloupec + 1)].width = 12
        sloupec += 2
    excel_styl.bunka(list_, 2, prvni_platba, "Poukázané finanční prostředky")
    list_.merge_cells(start_row=2, start_column=prvni_platba, end_row=2, end_column=sloupec - 1)

    sloupec_spec = sloupec
    excel_styl.bunka(list_, 2, sloupec_spec, "Spec.")
    list_.merge_cells(start_row=2, start_column=sloupec_spec, end_row=4, end_column=sloupec_spec)
    list_.column_dimensions[get_column_letter(sloupec_spec)].width = 10
    sloupec += 1

    excel_styl.zahlavi_pruh(list_, 2, 4, sloupec - 1)
    return Sloupce(platby=prvni_platba, spec=sloupec_spec, konec=sloupec)


def _zapis_ucastniky(list_: Worksheet, akce: Akce, kde: Sloupce) -> int:
    """Zapise ucastniky. Vraci cislo radku za poslednim zapsanym."""
    radek = PRVNI_DATOVY_RADEK

    for poradi, ucastnik in enumerate(akce.ucastnici, start=1):
        udaje = (
            poradi,
            ucastnik.jmeno,
            ucastnik.prijmeni,
            ucastnik.prezdivka,
            ucastnik.ulice,
            ucastnik.mesto,
            ucastnik.psc,
            ucastnik.datum_narozeni,
            ucastnik.jednotka,
            ucastnik.osobodny_vysledne,
            ucastnik.detodny_vysledne,
        )
        for sloupec, hodnota in enumerate(udaje, start=1):
            bunka = excel_styl.bunka(list_, radek, sloupec, hodnota)
            if sloupec == SLOUPEC_DATA_NAROZENI and hodnota is not None:
                bunka.number_format = excel_styl.FORMAT_DATA

        sloupec = kde.platby
        for pole, _ in ZPUSOBY_PLATBY:
            castka = getattr(ucastnik, pole)
            excel_styl.bunka(list_, radek, sloupec, na_cele_koruny(castka) if castka else None)
            excel_styl.bunka(list_, radek, sloupec + 1, getattr(ucastnik, f"{pole}_doklad"))
            sloupec += 2

        excel_styl.bunka(list_, radek, kde.spec)  # Spec. se vyplnuje rucne

        excel_styl.oblec_radek(
            list_,
            radek,
            za=kde.konec,
            zarovnani=ZAROVNANI_UDAJU,
            podbarvit=poradi % 2 == 0,
            od_castek=kde.platby,
            format_cisel=True,
        )
        radek += 1

    return radek


def _zapis_soucet(list_: Worksheet, akce: Akce, kde: Sloupce, radek: int) -> None:
    """Radek SOUCET pod tabulkou - osobodny, detodny a penize."""
    soucty = {
        SLOUPEC_OSOBODNY: sum(u.osobodny_vysledne for u in akce.ucastnici),
        SLOUPEC_DETODNY: sum(u.detodny_vysledne for u in akce.ucastnici),
    }
    sloupec = kde.platby
    for pole, _ in ZPUSOBY_PLATBY:
        soucty[sloupec] = sum(na_cele_koruny(getattr(u, pole)) for u in akce.ucastnici)
        sloupec += 2

    # Osobni udaje se nescitaji, takze pod nimi je jedna siroka bunka s popiskem.
    excel_styl.bunka(list_, radek, 1, "SOUČET:")
    list_.merge_cells(
        start_row=radek, start_column=1, end_row=radek, end_column=SLOUPEC_OSOBODNY - 1
    )
    for sloupec in range(SLOUPEC_OSOBODNY, kde.konec):
        bunka = excel_styl.bunka(list_, radek, sloupec, soucty.get(sloupec))
        bunka.number_format = excel_styl.CISLO

    excel_styl.souctovy_pruh(list_, radek, za=kde.konec, popisek_do=SLOUPEC_OSOBODNY)


# --- list 2: dochazka ------------------------------------------------------


def _dochazka_zahlavi(list_: Worksheet, akce: Akce, *, prvni_den: int, posledni: int) -> None:
    """Zahlavi mrizky: pevne sloupce, jeden sloupec na den a dva soucty."""
    for sloupec, (nadpis, sirka) in enumerate(DOCHAZKA_SLOUPCE, start=1):
        excel_styl.bunka(list_, DOCHAZKA_RADEK_ZAHLAVI, sloupec, nadpis)
        list_.column_dimensions[get_column_letter(sloupec)].width = sirka

    sloupec = prvni_den
    for den in akce.dny:
        bunka = excel_styl.bunka(list_, DOCHAZKA_RADEK_ZAHLAVI, sloupec, den)
        bunka.number_format = "d.m."
        list_.column_dimensions[get_column_letter(sloupec)].width = 5.5
        sloupec += 1

    for nadpis in ("Osobodny celkem", "Děťodny celkem"):
        excel_styl.bunka(list_, DOCHAZKA_RADEK_ZAHLAVI, sloupec, nadpis)
        list_.column_dimensions[get_column_letter(sloupec)].width = 12
        sloupec += 1

    excel_styl.zahlavi_pruh(list_, DOCHAZKA_RADEK_ZAHLAVI, DOCHAZKA_RADEK_ZAHLAVI, posledni)


def _dochazka_radek(
    list_: Worksheet,
    radek: int,
    ucastnik: Ucastnik,
    poradi: int,
    akce: Akce,
    *,
    prvni_den: int,
    posledni: int,
) -> None:
    """Jeden radek mrizky: udaje ucastnika, tecky po dnech a jeho soucty."""
    udaje = (poradi, ucastnik.jmeno, ucastnik.prijmeni, ucastnik.prezdivka)
    for sloupec, hodnota in enumerate(udaje, start=1):
        excel_styl.bunka(list_, radek, sloupec, hodnota)

    sloupec = prvni_den
    for den in akce.dny:
        # Lomitko = byl tu, prazdno = nebyl. Zamerne to neni jednicka:
        # dnovou mrizku nikdo nescita, scitaji se az osobodny vedle.
        excel_styl.bunka(list_, radek, sloupec, BYL_TU if ucastnik.byl_pritomen(den) else None)
        sloupec += 1
    excel_styl.bunka(list_, radek, sloupec, ucastnik.osobodny_vysledne)
    excel_styl.bunka(list_, radek, sloupec + 1, ucastnik.detodny_vysledne)

    # Vsechno od prvniho dne doprava je uzka kolonka - na stred.
    zarovnani = {1: excel_styl.NA_STRED}
    zarovnani.update({s: excel_styl.NA_STRED for s in range(prvni_den, posledni + 1)})
    excel_styl.oblec_radek(
        list_,
        radek,
        za=posledni + 1,
        zarovnani=zarovnani,
        podbarvit=poradi % 2 == 0,
        format_cisel=True,
    )


def _dochazka(sesit: Workbook, akce: Akce) -> None:
    """Druhy list: mrizka dnu akce. Bez terminu akce zadne dny nejsou, takze
    list vubec nevznikne - byla by to prazdna tabulka o par sloupcich."""
    if not akce.dny:
        return

    list_ = sesit.create_sheet("Docházka")
    prvni_den = len(DOCHAZKA_SLOUPCE) + 1
    posledni = prvni_den + len(akce.dny) + 1  # + osobodny a detodny celkem
    _nadpis_stranky(list_, akce, "Denní účast", sloupec_akce=prvni_den)
    _dochazka_zahlavi(list_, akce, prvni_den=prvni_den, posledni=posledni)

    radek = DOCHAZKA_PRVNI_DATOVY_RADEK
    for poradi, ucastnik in enumerate(akce.ucastnici, start=1):
        _dochazka_radek(
            list_, radek, ucastnik, poradi, akce, prvni_den=prvni_den, posledni=posledni
        )
        radek += 1

    _dochazka_soucet(list_, akce, radek, prvni_den=prvni_den, posledni=posledni)
    excel_styl.nastav_tisk(
        list_, na_sirku=True, opakovat=f"{DOCHAZKA_RADEK_ZAHLAVI}:{DOCHAZKA_RADEK_ZAHLAVI}"
    )


def _dochazka_soucet(
    list_: Worksheet, akce: Akce, radek: int, *, prvni_den: int, posledni: int
) -> None:
    """Pod mrizkou: kolik lidi bylo ktery den a osobodny/detodny celkem."""
    excel_styl.bunka(list_, radek, 1, "SOUČET:")
    list_.merge_cells(start_row=radek, start_column=1, end_row=radek, end_column=prvni_den - 1)

    sloupec = prvni_den
    for den in akce.dny:
        excel_styl.bunka(
            list_, radek, sloupec, sum(1 for u in akce.ucastnici if u.byl_pritomen(den))
        )
        sloupec += 1
    excel_styl.bunka(list_, radek, sloupec, sum(u.osobodny_vysledne for u in akce.ucastnici))
    excel_styl.bunka(list_, radek, sloupec + 1, sum(u.detodny_vysledne for u in akce.ucastnici))

    excel_styl.souctovy_pruh(list_, radek, za=posledni + 1, popisek_do=prvni_den)


# --- sesit -----------------------------------------------------------------


def sestav_sesit(akce: Akce) -> Workbook:
    sesit, list_ = excel_styl.novy_sesit("Seznam účastníků")

    kde = _zahlavi(list_, akce)
    radek_za_daty = _zapis_ucastniky(list_, akce, kde)
    _zapis_soucet(list_, akce, kde, radek_za_daty)
    excel_styl.nastav_tisk(list_, na_sirku=True, opakovat="2:4")

    _dochazka(sesit, akce)
    return sesit


def do_bajtu(akce: Akce) -> bytes:
    """Hotovy soubor XLSX pripraveny ke stazeni."""
    return excel_styl.do_bajtu(sestav_sesit(akce))


def nazev_souboru(akce: Akce) -> str:
    return f"{bezpecny_nazev(akce.nazev)}_seznam ucastniku.xlsx"
