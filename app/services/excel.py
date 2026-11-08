"""Export tabulky pro hospodare.

Format je odvozeny ze vzorove tabulky hospodare (neni soucasti repozitare). Rozvrzeni:

    radek 1   nazev tiskopisu, stredisko, nazev a cislo akce (ve vzoru prazdny)
    radek 2   hlavicka
    radek 3+  jeden radek na sken, serazeno po slozkach (rozdelena uctenka
              zabere tolik radku, kolik ma kategorii); za nimi cestaky ve
              vydajich, jeden radek na cestak
              sloupec A  datum vyuctovani, jen na prvnim datovem radku
              sloupec F  soucet slozky (nebo cestaku), jen na poslednim radku
    niz       Balance sheet a blok Prijmy

Vzhled (barvy, pisma, mrizka, nastaveni tisku) je v `excel_styl`; rozvrzeni
podle vzoru se nemeni.
"""

from __future__ import annotations

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from app.models import Akce
from app.services import cestaky as vypocet_cestaku
from app.services import excel_styl, pokladni_kniha
from app.services.excel_styl import na_cele_koruny
from app.services.soubory import bezpecny_nazev

HLAVICKA = [
    "Datum",
    "Název složky na disku",
    "název scanu na disku",
    "Částka",
    "Kdo platil",
    "celkem",
]

SIRKY_SLOUPCU = {"A": 12, "B": 32, "C": 34, "D": 11, "E": 18, "F": 11, "H": 14, "I": 11}

PRVNI_DATOVY_RADEK = 3

# Cisla sloupcu podle poradi v `HLAVICKA`. Pridani sloupce se tak da zapsat na jednom
# miste, ne prepocitavanim cisel po celem souboru.
SL_DATUM = 1
SL_SLOZKA = 2
SL_NAZEV = 3
SL_CASTKA = 4
SL_KDO = 5
SL_CELKEM = 6
SL_BALANCE_POPISEK = 8  # blok "Balance sheet" stoji mimo tabulku, o sloupec dal
SL_BALANCE = 9
POSLEDNI_SLOUPEC = SL_CELKEM

# Zarovnani sloupcu tabulky: castky doprava, datum na stred, texty doleva.
ZAROVNANI = {
    SL_DATUM: excel_styl.NA_STRED,
    SL_CASTKA: excel_styl.DOPRAVA,
    SL_CELKEM: excel_styl.DOPRAVA,
}


def radky_skenu(sken) -> list[tuple[str, int]]:
    """Radky, ktere uctenka zabere v tabulce: (nazev, castka v celych korunach).

    Nerozdelena uctenka je jeden radek, rozdelena tolik radku, kolik ma casti -
    aby hospodar videl, ze na nakupu jidla byl i material.

    Zaokrouhluje se tu opatrne. `na_cele_koruny` se normalne pousti na celou
    uctenku; kdybychom misto toho zaokrouhlili kazdou cast zvlast, uctenka za
    905,50 rozdelena na 500,25 a 405,25 by dala 500 + 405 = 905, kdezto bilancni
    blok pod tabulkou by dal pocital 906. Sloupec by se rozesel s bilanci o
    korunu a nebylo by poznat proc. Proto posledni cast pobere zbytek: soucet
    radku se vzdycky rovna `na_cele_koruny(sken.castka)`.
    """
    nazev = sken.nazev or sken.puvodni_nazev
    celkem = na_cele_koruny(sken.castka)
    if not sken.je_rozdelena:
        return [(nazev, celkem)]

    radky: list[tuple[str, int]] = []
    zbyva = celkem
    for poradi, cast in enumerate(sken.casti, start=1):
        posledni = poradi == len(sken.casti)
        castka = zbyva if posledni else na_cele_koruny(cast.castka)
        zbyva -= castka
        radky.append((f"{nazev} — {cast.nazev_kategorie}", castka))
    return radky


def _zahlavi(list_: Worksheet, akce: Akce) -> None:
    """Nadpis a nazev akce nad tabulkou.

    Vzor ma prvni radek prazdny, takze sloupec A zustava prazdny i tady -
    ale bez nazvu akce je vytisteny papir k nerozeznani od jineho vyuctovani.
    """
    excel_styl.nadpis_stranky(
        list_,
        akce,
        "Seznam prvotních dokladů",
        sloupec_nadpisu=2,
        sloupec_strediska=4,
        sloupec_akce=8,
        sloupec_cisla=10,
    )
    list_.merge_cells("B1:C1")
    list_.merge_cells("D1:G1")

    for sloupec, nadpis in enumerate(HLAVICKA, start=1):
        list_.cell(row=2, column=sloupec, value=nadpis)
    excel_styl.zahlavi_pruh(list_, 2, 2, POSLEDNI_SLOUPEC)


def _zapis_slozky(list_: Worksheet, akce: Akce, cisla: pokladni_kniha.CislaDokladu) -> int:
    """Zapise vydaje po slozkach. Vraci cislo radku za poslednim zapsanym."""
    radek = PRVNI_DATOVY_RADEK
    poradi_slozky = 0
    # Nazev slozky vcetne cisla dokladu - tentyz, jaky ma prihradka v ZIPu.
    nazvy = pokladni_kniha.nazvy_slozek(akce, cisla)

    for slozka in akce.slozky:
        skeny = slozka.skeny_s_radkem
        if not skeny:
            continue

        # Rozdelena uctenka zabere tolik radku, kolik ma kategorii - proto se
        # nechodi po skenech, ale po radcich, ktere z nich vyjdou.
        radky_slozky = [
            (sken, nazev_radku, castka)
            for sken in skeny
            for nazev_radku, castka in radky_skenu(sken)
        ]

        soucet_slozky = 0
        for poradi, (sken, nazev_radku, castka) in enumerate(radky_slozky, start=1):
            soucet_slozky += castka

            excel_styl.zapis(list_, radek, SL_SLOZKA, nazvy[slozka.id])
            excel_styl.zapis(list_, radek, SL_NAZEV, nazev_radku)
            excel_styl.zapis(list_, radek, SL_CASTKA, castka)
            excel_styl.zapis(list_, radek, SL_KDO, sken.kdo_platil or slozka.kdo_platil)

            # Podbarveni se stridá po slozkach, ne po radcich - je pak videt,
            # kde jedna slozka konci a zacina druha.
            excel_styl.oblec_radek(
                list_,
                radek,
                za=POSLEDNI_SLOUPEC + 1,
                zarovnani=ZAROVNANI,
                podbarvit=bool(poradi_slozky % 2),
            )

            # Soucet patri jen na posledni radek slozky - tak to ma hospodar ve vzoru.
            if poradi == len(radky_slozky):
                excel_styl.oznac_soucet(excel_styl.zapis(list_, radek, SL_CELKEM, soucet_slozky))

            radek += 1
        poradi_slozky += 1

    return radek


def _zapis_cestaky(
    list_: Worksheet, akce: Akce, radek: int, cisla: pokladni_kniha.CislaDokladu
) -> int:
    """Zapise cestaky, ktere jsou ve vydajich, hned za slozky - jeden radek na
    kazdy, stejnym stylem jako posledni radek slozky (podbarveny soucet).

    Driv se cestak do Excelu dostal tak, ze se nahral jako PDF a stal se z nej
    uctenka ve slozce. Ted ho appka vyrabi rovnou, ale hospodar ma dostat
    porad totez - proto se sem pise stejnym zpusobem, jen bez rozpisu jizd
    (ten je na vytistenem VPD, ne v tomhle seznamu). Vraci cislo radku za
    poslednim zapsanym.
    """
    nazvy = pokladni_kniha.nazvy_cestaku(akce, cisla)
    poradi_cestaku = 0
    for cestak in akce.cestaky:
        if cestak.pokladni_radek is None:
            continue

        castka = na_cele_koruny(cestak.pokladni_radek.castka)
        excel_styl.zapis(list_, radek, SL_SLOZKA, nazvy[cestak.id])
        excel_styl.zapis(list_, radek, SL_NAZEV, vypocet_cestaku.ucel_v_knize(cestak))
        excel_styl.zapis(list_, radek, SL_CASTKA, castka)
        excel_styl.zapis(list_, radek, SL_KDO, cestak.cestujici.jmeno if cestak.cestujici else None)

        excel_styl.oblec_radek(
            list_,
            radek,
            za=POSLEDNI_SLOUPEC + 1,
            zarovnani=ZAROVNANI,
            podbarvit=bool(poradi_cestaku % 2),
        )
        excel_styl.oznac_soucet(excel_styl.zapis(list_, radek, SL_CELKEM, castka))

        radek += 1
        poradi_cestaku += 1

    return radek


def _zapis_prijmy(list_: Worksheet, akce: Akce, radek: int) -> None:
    prvni = True
    for prijem in akce.prijmy:
        castka = na_cele_koruny(prijem.castka)
        if prvni:
            excel_styl.zapis(list_, radek, SL_DATUM, "Přijmy").font = excel_styl.PODNADPIS
            prvni = False
        excel_styl.zapis(list_, radek, SL_NAZEV, prijem.popis)
        excel_styl.zapis(list_, radek, SL_CASTKA, castka)
        excel_styl.zapis(list_, radek, SL_KDO, prijem.kdo_vybral)
        excel_styl.oznac_soucet(excel_styl.zapis(list_, radek, SL_CELKEM, castka))

        excel_styl.oblec_radek(
            list_, radek, za=POSLEDNI_SLOUPEC + 1, zarovnani=ZAROVNANI, od_sloupce=3
        )
        radek += 1


def soucty_v_celych_korunach(akce: Akce) -> tuple[int, int, int]:
    """Vydaje, prijmy a balance sheet tak, jak vyjdou v Excelu.

    Scita se az po zaokrouhleni jednotlivych polozek, aby sloupec v tabulce
    doslova sedel, kdyz ho nekdo secte na kalkulacce.
    """
    vydaje = sum(
        castka
        for slozka in akce.slozky
        for sken in slozka.skeny_s_radkem
        for _, castka in radky_skenu(sken)
    )
    vydaje += sum(
        na_cele_koruny(c.pokladni_radek.castka)
        for c in akce.cestaky
        if c.pokladni_radek is not None
    )
    prijmy = sum(na_cele_koruny(p.castka) for p in akce.prijmy)
    return vydaje, prijmy, prijmy - vydaje


def _zapis_balance(list_: Worksheet, radek: int, balance: int) -> None:
    """Vysledek akce. Jediné misto v tabulce, kde svitit zelena nebo cervena."""
    popisek = excel_styl.zapis(list_, radek, SL_BALANCE_POPISEK, "Balance sheet")
    popisek.font = excel_styl.SOUCET
    hodnota = excel_styl.zapis(list_, radek, SL_BALANCE, balance)
    excel_styl.obarvi_vysledek(hodnota, balance)
    for bunka in (popisek, hodnota):
        bunka.fill = excel_styl.VYPLN_SOUCTU
        bunka.border = excel_styl.MRIZKA_SOUCTU
    hodnota.alignment = excel_styl.DOPRAVA
    list_.row_dimensions[radek].height = 20


def sestav_sesit(akce: Akce) -> Workbook:
    sesit, list_ = excel_styl.novy_sesit("seznam prvotních dokladů")

    for sloupec, sirka in SIRKY_SLOUPCU.items():
        list_.column_dimensions[sloupec].width = sirka

    _zahlavi(list_, akce)
    # Kniha se sestavuje jednou pro slozky i cestaky.
    cisla = pokladni_kniha.cisla_dokladu(akce)
    radek_za_daty = _zapis_slozky(list_, akce, cisla)
    radek_za_daty = _zapis_cestaky(list_, akce, radek_za_daty, cisla)

    # Datum vyuctovani patri jen na uplne prvni datovy radek tabulky, at je
    # to slozka nebo cestak zapsany hned za nimi.
    if akce.datum_vyuctovani and radek_za_daty > PRVNI_DATOVY_RADEK:
        datum = list_.cell(row=PRVNI_DATOVY_RADEK, column=SL_DATUM, value=akce.datum_vyuctovani)
        datum.number_format = excel_styl.FORMAT_DATA

    _, _, balance = soucty_v_celych_korunach(akce)
    radek_balance = radek_za_daty + 1
    _zapis_balance(list_, radek_balance, balance)

    _zapis_prijmy(list_, akce, radek_balance + 3)

    for radek in list_.iter_rows(min_row=PRVNI_DATOVY_RADEK, min_col=SL_CASTKA, max_col=SL_CELKEM):
        for bunka in radek:
            if isinstance(bunka.value, int):
                bunka.number_format = excel_styl.CISLO
    list_.cell(row=radek_balance, column=SL_BALANCE).number_format = excel_styl.CISLO

    excel_styl.nastav_tisk(list_, na_sirku=True, opakovat="2:2")
    return sesit


def do_bajtu(akce: Akce) -> bytes:
    """Hotovy soubor XLSX pripraveny ke stazeni."""
    return excel_styl.do_bajtu(sestav_sesit(akce))


def nazev_souboru(akce: Akce) -> str:
    return f"{bezpecny_nazev(akce.nazev)}_seznam prvotnich dokladu.xlsx"
