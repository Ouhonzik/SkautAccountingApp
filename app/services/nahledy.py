"""Male nahledy skenu pro tabulku.

Prevod PDF nebo HEIC na obrazek neni zadarmo. Bez ukladani by se pri kazdem
nacteni stranky prepocitavaly vsechny nahledy znovu - u padesati uctenek by
se tabulka nenacetla vubec.

Nahledy jsou odvozena data: kdyz se slozka smaze, nic se neztrati, jen se
priste vyrobi znovu.
"""

from __future__ import annotations

import io
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path

import pillow_heif
from PIL import Image

from app.config import nastaveni
from app.services import obrazky

log = logging.getLogger(__name__)

# Fotky z iPhonu byvaji HEIC - Pillow sam neumi. Registrace je bezpecna volat
# vicekrat (jen prida plugin do Pillow), takze nevadi, ze to same dela
# app/services/soubory.py a ocr.py.
pillow_heif.register_heif_opener()

MAX_HRANA_PX = 200
# Velky nahled je pro rezim lupy, kde se uctenka cte pres pul obrazovky.
# Na 200 px by se z uctenky nedaly precist polozky a kontrola by nemela smysl.
MAX_HRANA_VELKA_PX = 1600
KVALITA = 75
KVALITA_VELKA = 82

# Vykresleni slozite PDF stranky nebo velkeho obrazku muze u zlomyslne
# slozeneho souboru trvat dlouho - bezi proto ve vlastnim vlakne
# s casovym limitem, misto aby zablokovalo cely pozadavek na neomezenou dobu.
# Dokud routy zustavaji `async def` (kapitola 28 K-019), limit jen ohranici
# cekaci dobu jednoho pozadavku - server po tu dobu porad stoji cely.
LIMIT_VYKRESLENI_S = 20
_vykreslovac = ThreadPoolExecutor(max_workers=2)


def slozka_nahledu() -> Path:
    cesta = nastaveni.data_dir / "nahledy"
    cesta.mkdir(parents=True, exist_ok=True)
    return cesta


def klic(soubor: str | Path) -> str:
    """Jmeno, pod kterym nahled lezi na disku.

    Odvozuje se ze *jmena nahraneho souboru* (nahodne uuid), ne z id skenu.
    Id se totiz po smazani skenu vraci zpatky do hry - SQLite dava novemu
    radku nejvyssi volne cislo - takze by novy sken zdedil ulozeny nahled
    davno smazaneho a v tabulce by svitila cizi uctenka.
    """
    return re.sub(r"[^A-Za-z0-9_-]", "_", Path(soubor).stem)


def _cil(soubor: str | Path, strana: int, hrana: int) -> Path:
    # Velikost (`hrana`) je soucasti jmena, aby zmena MAX_HRANA_PX/MAX_HRANA_VELKA_PX
    # nezacala servirovat stare, jinak velke nahledy z cache.
    return slozka_nahledu() / f"{klic(soubor)}-{strana}-{hrana}.jpg"


def dej_nahled(
    zdroj: Path, pocet_stranek: int, strana: int = 1, velky: bool = False
) -> bytes | None:
    """Vrati JPEG nahled. Poprve ho vyrobi, pak uz jen cte z disku.

    `pocet_stranek` je skutecny pocet stranek souboru (`Sken.pocet_stranek`
    z databaze) - jmeno souboru v cache se sklada z NEJ (orezane, skutecne
    zobrazene stranky), ne z libovolneho `strana` z adresy. Jinak by kazde
    nove cislo v `?strana_c=` vyrobilo pri kazdem pozadavku dalsi soubor na
    disku, i kdyz sken jednu stranku.

    `velky` vraci verzi pro lupu (rezim pres celou obrazovku). Uklada se
    zvlast, aby maly nahled v tabulce zustal maly a nacital se rychle.
    """
    index = min(max(strana, 1), max(pocet_stranek, 1)) - 1
    hrana = MAX_HRANA_VELKA_PX if velky else MAX_HRANA_PX
    cil = _cil(zdroj, index + 1, hrana)
    if cil.is_file():
        return cil.read_bytes()

    try:
        budouci = _vykreslovac.submit(_vykresli, zdroj, index, hrana, velky)
        data = budouci.result(timeout=LIMIT_VYKRESLENI_S)
    except FuturesTimeoutError:
        log.warning("Vykreslení náhledu %s vypršelo (limit %s s)", klic(zdroj), LIMIT_VYKRESLENI_S)
        return None
    except Exception:  # poskozeny soubor nesmi shodit stranku, jen se zaloguje
        log.warning("Nepodařilo se vyrobit náhled souboru %s", klic(zdroj), exc_info=True)
        return None

    cil.write_bytes(data)
    return data


def _vykresli(zdroj: Path, index: int, hrana: int, velky: bool) -> bytes:
    """Vykresli/zmensi na pozadovanou velikost rovnou, bez mezikroku ve vetsim
    rozliseni. Bezi v `_vykreslovac` (vlastni vlakno) - vola ji jen `dej_nahled`,
    nikdy primo request handler."""
    if zdroj.suffix.lower() == ".pdf":
        return obrazky.vykresli_stranku(zdroj, index, hrana)

    obrazek = Image.open(zdroj)
    obrazek.thumbnail((hrana, hrana))
    if obrazek.mode != "RGB":
        obrazek = obrazek.convert("RGB")
    vystup = io.BytesIO()
    obrazek.save(vystup, "JPEG", quality=KVALITA_VELKA if velky else KVALITA)
    return vystup.getvalue()


def zapomen(soubor: str | Path) -> None:
    """Smaze ulozene nahledy jednoho nahraneho souboru."""
    for stary in slozka_nahledu().glob(f"{klic(soubor)}-*.jpg"):
        stary.unlink(missing_ok=True)
