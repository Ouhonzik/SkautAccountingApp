"""Texty a pravidla pro log cinnosti: jak se jmenuje tabulka, kterym polem se
popisuje, co se nikdy neloguje a jak se hodnoty ukazuji.

Pridavas-li tabulku, zarad ji sem: bud do `NAZVY_OBJEKTU` (a podle potreby do
`POPISNE_POLE` a `CESTA_K_AKCI`), nebo do `NELOGOVANE_TRIDY`. Na to, ze zadna
tabulka nechybi, dohlizi test automaticky test.
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.formaty import datum, koruny
from app.models import (
    Akce,
    Clovek,
    Kategorie,
    OdeslanyEmail,
    PouzitiAI,
    Sken,
    Slozka,
    TokenUzivatele,
    Vozidlo,
    ZaznamCinnosti,
)

log = logging.getLogger(__name__)

# Tridy, jejichz zmeny se do logu NIKDY nezapisuji: log sam, spotreba AI
# a dve tabulky, ktere by v logu prozradily to, co tam byt nesmi - tela
# e-mailu a otisky tokenu z odkazu. Jeden vycet pro vsechny
# cesty v hooku (pridani, uprava, smazani, souhrn smazane akce) - kdyby byl
# na vic mistech, pridala by se nova citliva tabulka jen na nektera a jeji
# zmeny by se tise zacaly logovat.
NELOGOVANE_TRIDY = (ZaznamCinnosti, PouzitiAI, OdeslanyEmail, TokenUzivatele)
NELOGOVANE_TABULKY = {trida.__tablename__ for trida in NELOGOVANE_TRIDY}

# Tabulky s osobnimi udaji deti - do popisu ani do detail_json se u nich
# nikdy nedostane hodnota, jen pocet a nazvy zmenenych poli.
OSOBNI_TABULKY = {"ucastnik", "obdarovany"}

# Pole, jejichz hodnota se v logu nikdy neukazuje (ale zmena se zminuje).
# Bydliste a kontakt jsou osobni udaj i u dospeleho vedouciho - v logu staci,
# ze se zmenily.
SKRYTA_POLE = {"otisk_hesla", "rozpis_json", "dochazka_json", "adresa", "kontakt"}

# Pole, jejichz zmena se v logu vubec neresi - bud jsou nezajimava (poradi
# pri tazeni mysi), nebo se meni pri kazdem pozadavku a zaplavily by log
# (verze_hesla, posledni_prihlaseni pri kazdem prihlaseni).
IGNOROVANA_POLE = {"poradi", "poradi_v_knize", "verze_hesla", "posledni_prihlaseni"}

NAZVY_OBJEKTU = {
    "akce": "akce",
    "kategorie": "kategorie",
    "clovek": "člověk v číselníku",
    "vozidlo": "vozidlo",
    "vozidlo_foto": "fotka technického průkazu",
    "slozka": "složka",
    "sken": "účtenka",
    "cast_skenu": "rozdělení účtenky",
    "prijem": "příjem",
    "pokladni_radek": "řádek pokladní knihy",
    "ucastnik": "účastník",
    "obdarovany": "obdarovaný",
    "dar": "věc k rozdání",
    "cestak": "cestovní příkaz",
    "jizda": "jízda",
    "uzivatel": "uživatel",
}

# Kterym polem se objekt v logu pojmenuje (prvni neprazdne). Tabulka tu
# nemusi byt - pak v logu zustane jen typ objektu. Ucastnik a obdarovany tu
# zamerne nejsou (osobni udaj, viz `je_osobni`).
POPISNE_POLE = {
    "akce": ("nazev",),
    "kategorie": ("nazev",),
    "slozka": ("nazev",),
    "dar": ("nazev",),
    "vozidlo": ("nazev",),
    "vozidlo_foto": ("puvodni_nazev",),
    "clovek": ("jmeno",),
    "sken": ("nazev", "puvodni_nazev"),
    "prijem": ("popis",),
    "cestak": ("ucel_cesty",),
    "pokladni_radek": ("ucel",),
    "uzivatel": ("login",),
}

# Tabulky bez vlastniho sloupce `akce_id`: pres kterou vazbu se k akci
# dostanou. Jizda visi na cestaku, cast skenu na skenu - bez toho by se jejich
# zaznamy nedaly na /log vyfiltrovat podle akce.
CESTA_K_AKCI = {"cast_skenu": "sken", "jizda": "cestak"}

POPISKY_POLI = {
    "nazev": "název",
    "castka": "částka",
    "cena_za_litr": "cena za litr",
    "datum": "datum",
    "datum_od": "začátek",
    "datum_do": "konec",
    "datum_vyuctovani": "datum vyúčtování",
    "obchodnik": "obchodník",
    "druh": "druh",
    "kategorie_id": "kategorie",
    "clovek_id": "kdo platil / vybral",
    "slozka_id": "složka",
    "pokladnik_id": "pokladník",
    "priloha_k_id": "patří k účtence",
    "je_priloha": "je příloha",
    "zkontrolovano": "zkontrolováno",
    "varovani_ztiseno": "varování ztišeno",
    "jistota": "jistota",
    "poznamka": "poznámka",
    "ucel": "účel platby",
    "ucel_platby": "účel platby",
    "smer": "směr",
    "stav": "stav",
    "prefix_dokladu": "prefix dokladu",
    "popis": "popis",
    "kdo": "kdo vybral",
    "login": "přihlašovací jméno",
    "jmeno": "jméno",
    "email": "e-mail",
    "email_overen": "e-mail ověřen",
    "uzivatel_id": "napojený účet",
    "role": "role",
    "aktivni": "aktivní",
    "musi_zmenit_heslo": "vynutit změnu hesla",
    "obdarovany_id": "komu",
    "sken_id": "z účtenky",
    "otisk_hesla": "heslo",
    "spz": "SPZ",
    "spotreba": "spotřeba",
    "soubor": "soubor",
    "puvodni_nazev": "název souboru",
    "ucel_cesty": "účel cesty",
    "misto": "místo",
    "spolucestujici": "spolucestující",
    "jednotka": "jednotka",
    "cestujici_id": "cestující",
    "vozidlo_id": "vozidlo",
    "palivova_uctenka_id": "palivová účtenka",
    "opotrebeni": "opotřebení",
    "adresa": "bydliště",
    "kontakt": "kontakt",
    "odkud": "odkud",
    "kam": "kam",
    "prostredek": "prostředek",
    "km": "kilometry",
    "jizdne": "jízdné",
}

# Pole, ktera nejsou penize, i kdyz je nesou jako Decimal. Bez toho by se
# spotreba 6,00 l/100 km (a kilometry jizdy) v logu hlasila jako "6,00 Kc".
POLE_BEZ_KORUN = {"spotreba", "km"}

# Cizi klice, ktere se v logu dereferencuji na citelne jmeno. `obdarovany_id`
# tu zamerne neni - Obdarovany je osobni udaj, takze zustava jen jako cislo.
_FK_MODELY = {
    "kategorie_id": Kategorie,
    "clovek_id": Clovek,
    "slozka_id": Slozka,
    "pokladnik_id": Clovek,
    "priloha_k_id": Sken,
    "cestujici_id": Clovek,
    "vozidlo_id": Vozidlo,
    "palivova_uctenka_id": Sken,
}


def loguje_se(obj) -> bool:
    """Zapisuji se zmeny tohoto objektu do logu? (Ne u `NELOGOVANE_TRIDY`.)"""
    return not isinstance(obj, NELOGOVANE_TRIDY)


def je_osobni(obj) -> bool:
    """Objekt nesouci osobni udaj ditete - bez ohledu na tabulku.

    Vetsinou je to cela tabulka (`OSOBNI_TABULKY`), ale radek pokladni knihy
    je osobni jen kdyz je to vratka (ma vazbu na ucastnika) - obycejny rucni
    radek osobni udaj nenese a chova se dal jako dnes.
    """
    tabulka = getattr(obj, "__tablename__", None)
    if tabulka in OSOBNI_TABULKY:
        return True
    if tabulka == "pokladni_radek":
        return getattr(obj, "ucastnik_id", None) is not None
    return False


def popisne_jmeno(obj) -> str | None:
    """Nejlepsi citelne jmeno objektu do logu. None u osobnich udaju a
    u vseho, co zadne rozumne jmeno nema (tam zustane jen typ objektu)."""
    tabulka = getattr(obj, "__tablename__", None)
    if tabulka is None or je_osobni(obj):
        return None
    for pole in POPISNE_POLE.get(tabulka, ()):
        hodnota = getattr(obj, pole)
        if hodnota:
            return hodnota
    return None


def nazev_v_uvozovkach(obj) -> str:
    jmeno = popisne_jmeno(obj)
    return f" „{jmeno}“" if jmeno else ""


def akce_id_objektu(obj) -> int | None:
    mapovana_pole = sa.inspect(obj).mapper.columns.keys()
    if "akce_id" in mapovana_pole:
        return getattr(obj, "akce_id", None)
    tabulka: str = obj.__tablename__
    if tabulka == "akce":
        return obj.id
    vazba = CESTA_K_AKCI.get(tabulka)
    if vazba is None:
        return None
    try:
        rodic = getattr(obj, vazba)
        return rodic.akce_id if rodic is not None else None
    except Exception:  # log nikdy nesmi spadnout kvuli detailu, jen se zaloguje
        log.warning("Nepodařilo se zjistit akci k %s %s", tabulka, obj.id, exc_info=True)
        return None


def nazev_akce(sezeni: Session, akce_id: int | None) -> str | None:
    if akce_id is None:
        return None
    akce = sezeni.get(Akce, akce_id)
    return akce.nazev if akce else None


def formatuj_hodnotu(sezeni: Session, nazev_pole: str, hodnota) -> str:
    if hodnota is None:
        return "—"
    if nazev_pole in _FK_MODELY:
        objekt = sezeni.get(_FK_MODELY[nazev_pole], hodnota)
        jmeno = popisne_jmeno(objekt) if objekt is not None else None
        return jmeno or f"#{hodnota}"
    if isinstance(hodnota, bool):
        return "ano" if hodnota else "ne"
    if isinstance(hodnota, Decimal):
        return f"{hodnota}" if nazev_pole in POLE_BEZ_KORUN else koruny(hodnota)
    if isinstance(hodnota, dt.date):
        return datum(hodnota)
    return str(hodnota)
