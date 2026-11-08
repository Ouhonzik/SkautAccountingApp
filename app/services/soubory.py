"""Prace s nazvy souboru a bezpecne ulozeni nahranych souboru.

Nazvy skenu vymysli Honza ("Albert 758,-", "Jízdné 28x32=896") a pouzivaji se
jako skutecne nazvy souboru v ZIPu. Musi tedy projit pres Windows.
"""

from __future__ import annotations

import io
import os
import re
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

import pillow_heif
from fastapi import UploadFile
from PIL import Image

from app.config import nastaveni

if TYPE_CHECKING:
    from app.models import Akce

# Fotky z iPhonu byvaji HEIC - Pillow sam neumi, viz i app/services/ocr.py.
# Registrace je bezpecna volat vicekrat (jen prida plugin do Pillow).
pillow_heif.register_heif_opener()

# Obrana proti "dekompresni bombe": nastaveni.max_obrazek_px hlida
# odmitnuti pri nahrani, tohle navic omezi Pillow globalne pro cely proces -
# i pro soubory ulozene pred touhle opravou a soubory ze zaloh. Vychozi
# Pillow mez (~89 Mpx varovani, ~179 Mpx chyba) je na malou VPS moc vysoka.
Image.MAX_IMAGE_PIXELS = nastaveni.max_obrazek_px

# Ktere pripony appka prijima pri nahravani skenu i fotek technickeho prukazu
#. Puvodne zilo jen v skeny.py, vozidla.py si ho pujcovalo odjinud -
# spravne misto je tady, u funkce, ktera obsah podle pripony i overuje.
POVOLENE_PRIPONY = {".pdf", ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}

# Ocekavany format Pillow (Image.format) pro kazdou obrazkovou priponu -
# soubor s cizim obsahem pod touhle priponou (napr. GIF prejmenovany na .jpg)
# se timhle porovnanim odhali, i kdyz ho Pillow bez potizi otevre.
_OCEKAVANY_FORMAT = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".webp": "WEBP",
    ".heic": "HEIF",
    ".heif": "HEIF",
}

VELIKOST_KUSU = 1024 * 1024  # cte a zapisuje se po 1 MB, ne cely soubor najednou


class ChybaObsahu(Exception):
    """Soubor se odmita - spatna pripona, moc velky, nebo uvnitr neni to, co
    pripona slibuje. Text je vzdy cesky a strucny, meze se na hlasku uzivateli."""


# Znaky, ktere Windows v nazvu souboru nedovoli.
ZAKAZANE_ZNAKY = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Jmena vyhrazena systemem - soubor se tak nesmi jmenovat ani s priponou.
VYHRAZENA_JMENA = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

MAX_DELKA = 120


def bezpecny_nazev(nazev: str, nahrada: str = "-") -> str:
    """Upravi nazev tak, aby z nej sel udelat soubor nebo slozka na Windows.

    Carky, rovnitka ani diakritika problem nejsou, takze "Albert 758,-"
    i "Jízdné 28x32=896" projdou beze zmeny.
    """
    ocisteny = ZAKAZANE_ZNAKY.sub(nahrada, nazev or "")
    # Windows umi zahodit tecky a mezery na konci - radeji je odstranime sami.
    ocisteny = ocisteny.strip().rstrip(". ")
    ocisteny = ocisteny[:MAX_DELKA].rstrip(". ")

    if not ocisteny:
        return "bez nazvu"
    if ocisteny.upper() in VYHRAZENA_JMENA:
        return f"_{ocisteny}"
    return ocisteny


def zaber_nazev(nazev: str, obsazene: set[str], *, pripona: bool = True) -> str:
    """Doplni ' (2)', ' (3)' ... kdyz uz se nazev v teze slozce vyskytuje, a vraceny
    nazev ZAPISE do `obsazene` (proto `zaber_`: volajici na tom stoji - bez zapisu
    by dve uctenky v ZIPu dostaly stejne jmeno).

    `obsazene` drzi nazvy bez ohledu na velikost pisma (`casefold`): Windows
    a macOS berou "Albert.pdf" a "albert.pdf" jako jeden soubor, takze ZIP
    s obema by se pri rozbaleni tise prepsal. Mnozinu proto plni jen
    tahle funkce. `pripona=False` je pro nazvy slozek - tecka v nich k pripone
    nepatri ("Tabor 2.5" nema dostat " (2)" doprostred).
    """
    if nazev.casefold() not in obsazene:
        obsazene.add(nazev.casefold())
        return nazev

    zaklad, tecka, koncovka = nazev.rpartition(".") if pripona else (nazev, "", "")
    if not tecka:
        zaklad, koncovka = nazev, ""

    poradi = 2
    while True:
        kandidat = f"{zaklad} ({poradi}){'.' + koncovka if koncovka else ''}"
        if kandidat.casefold() not in obsazene:
            obsazene.add(kandidat.casefold())
            return kandidat
        poradi += 1


def docasny_soubor() -> Path:
    """Prazdny soubor v `data/tmp/`, kam se sklada ZIP ke stazeni.

    ZIP s fotkami cele akce muze mit stovky MB - v pameti by pár souběžných
    stažení vyčerpalo malý server. Kdo soubor vyrobí, musí ho i smazat
    (routa `export.py` po odeslání); co přesto zbude po pádu, uklidí
    `vycisti_tmp()` při startu.
    """
    nastaveni.slozka_tmp.mkdir(parents=True, exist_ok=True)
    fd, cesta = tempfile.mkstemp(dir=nastaveni.slozka_tmp, suffix=".zip")
    os.close(fd)
    return Path(cesta)


def vycisti_tmp() -> None:
    """Smaze zbytky po pádu v `data/tmp/` (volá se při startu appky)."""
    if not nastaveni.slozka_tmp.is_dir():
        return
    for cesta in nastaveni.slozka_tmp.iterdir():
        if cesta.is_file():
            cesta.unlink(missing_ok=True)


def _pocet_stranek_pdf(zdroj: Path | bytes) -> int:
    """Otevre PDF pres pypdfium2 a spocita stranky.

    Driv se tu pocitalo pres pypdf a vykreslovalo (cteni AI, nahledy) pres
    pypdfium2 - dva ruzne parsery PDF, ktere se u poskozeneho souboru mohly
    rozejit. Otevreni zaroven overi, ze soubor je vubec citelne PDF -
    kdyz ne, vyhodi vyjimku a `uloz_nahrany` ho odmitne, misto aby si tise
    poznamenal "1 stranka" u souboru, ktery zadna PDF stranka neni.
    """
    import pypdfium2

    dokument = pypdfium2.PdfDocument(zdroj)
    try:
        pocet = len(dokument)
    finally:
        dokument.close()
    if pocet < 1:
        raise ChybaObsahu("PDF nemá žádnou stránku")
    # Stranky PDF muzou sdilet jeden obsah, takze desetitisice stranek jsou
    # jen stovky kB souboru - bez meze by je appka pak musela cele vykreslit
    # kvuli nahledu jedine stranky nebo cteni AI (B-037b).
    if pocet > nastaveni.max_stranek_pdf:
        raise ChybaObsahu(f"má víc než {nastaveni.max_stranek_pdf} stránek")
    return pocet


def _over_obrazek(zdroj: Path | bytes, pripona: str) -> None:
    """Overi, ze soubor je citelny obrazek ocekavaneho formatu.

    `Image.verify()` odmitne poskozena data; porovnani formatu navic odmitne
    cizi obsah pod spravnou priponou (GIF prejmenovany na .jpg) - to `verify()`
    samo o sobe nechyta, protoze Pillow format pozna z obsahu, ne z pripony.
    Po `verify()` uz se s obrazkem nesmi nic delat (dokumentace Pillow), proto
    se format cte driv.
    """
    try:
        with Image.open(io.BytesIO(zdroj) if isinstance(zdroj, bytes) else zdroj) as obrazek:
            format_obrazku = obrazek.format
            sirka, vyska = obrazek.size
            obrazek.verify()
    except Exception as chyba:
        raise ChybaObsahu("uvnitř není čitelný obrázek") from chyba
    if format_obrazku != _OCEKAVANY_FORMAT[pripona]:
        raise ChybaObsahu(f"přípona neodpovídá obsahu ({format_obrazku or 'neznámý formát'})")
    # Rozmery Pillow precte z hlavicky souboru, ne az z rozbalenych dat - male
    # PNG s obrim rozmerem (napr. 13000x13000 jedne barvy) tak neprojde dal,
    # kde by se rozbalilo na stovky MB v pameti (B-037a).
    if sirka * vyska > nastaveni.max_obrazek_px:
        raise ChybaObsahu("obrázek je příliš velký")


def over_obsah(zdroj: Path | bytes, pripona: str) -> int:
    """Overi obsah souboru podle pripony (PDF, nebo obrazek ocekavaneho formatu)
    a vrati pocet stranek. Spolecne pro nahravani (`uloz_nahrany`, soubor na disku)
    i obnovu ze zalohy (`obnova_zalohy`, bajty ze ZIPu, B-065) - obnova nesmi
    na disk pustit nic, co by nahravani odmitlo. Vyhazuje `ChybaObsahu`.
    """
    try:
        if pripona == ".pdf":
            return _pocet_stranek_pdf(zdroj)
        _over_obrazek(zdroj, pripona)
        return 1
    except ChybaObsahu:
        raise
    except Exception as chyba:
        raise ChybaObsahu("soubor se nepodařilo zpracovat") from chyba


def zkrat_pro_hlasku(nazev: str, nejvic: int = 60) -> str:
    """Nazev souboru do hlasky: hlasky lezi v session cookie, takze nazev o tisicich
    znacich by ji nafoukl."""
    return nazev if len(nazev) <= nejvic else nazev[: nejvic - 1] + "…"


async def uloz_nahrany(nahrany: UploadFile, cilova_slozka: Path, pripona: str) -> tuple[str, int]:
    """Ulozi nahrany soubor pod nahodnym jmenem a overi ho.

    Volajici uz musi mit priponu overenou proti `POVOLENE_PRIPONY` - tahle
    funkce se stara jen o bezpecne ulozeni: cte a zapisuje po `VELIKOST_KUSU`
    (soubor se nenacita cely do pameti), skonci hned, jak velikost prekroci
    `nastaveni.max_velikost_souboru_mb`, a nakonec overi, ze uvnitr je opravdu
    PDF nebo obrazek slibovaneho typu. Pri jakekoli chybe soubor na disku
    nezustane. Vraci (nazev_na_disku, pocet_stranek).
    """
    nazev_na_disku = f"{uuid.uuid4().hex}{pripona}"
    cil = cilova_slozka / nazev_na_disku
    max_bajtu = nastaveni.max_velikost_souboru_mb * 1024 * 1024

    try:
        velikost = 0
        with cil.open("wb") as vystup:
            while kus := await nahrany.read(VELIKOST_KUSU):
                velikost += len(kus)
                if velikost > max_bajtu:
                    raise ChybaObsahu(f"má víc než {nastaveni.max_velikost_souboru_mb} MB")
                vystup.write(kus)

        pocet_stranek = over_obsah(cil, pripona)
    except ChybaObsahu:
        cil.unlink(missing_ok=True)
        raise
    except Exception as chyba:
        cil.unlink(missing_ok=True)
        raise ChybaObsahu("soubor se nepodařilo zpracovat") from chyba

    return nazev_na_disku, pocet_stranek


def smaz_soubory(nazvy: list[str], slozka: Path) -> None:
    """Uklid po chybe uprostred nahravani nebo ukladani - soubory uz
    zapsane na disk se vrati zpet, stejne jako `sezeni.rollback()` vrati
    databazi. Chybejici soubor (uz smazany, nebo se nikdy nezapsal) se
    preskoci beze slova."""
    for nazev in nazvy:
        (slozka / nazev).unlink(missing_ok=True)


def limit_mista_akce_mb(akce: Akce) -> int:
    """Kolik MB souboru smi mit akce dohromady. Prazdne `misto_mb_limit`
    (vetsina akci) znamena vychozi hodnotu z `.env` - spravce ji prebiji jen
    tam, kde na to je duvod (napr. velky tabor s mnoha uctenkami)."""
    return (
        akce.misto_mb_limit if akce.misto_mb_limit is not None else nastaveni.vychozi_misto_akce_mb
    )


def velikost_souboru_akce(akce: Akce, slozka_uploadu: Path) -> int:
    """Soucet velikosti (v bajtech) souboru vsech skenu akce, ktere dnes
    doopravdy lezi na disku - sken bez souboru (napr. po chybe) se nepocita."""
    celkem = 0
    for sken in akce.skeny:
        cesta = slozka_uploadu / sken.soubor
        if cesta.is_file():
            celkem += cesta.stat().st_size
    return celkem


def rozbalena_velikost_zipu(archiv: zipfile.ZipFile) -> int:
    """Soucet nekomprimovane velikosti vsech polozek v ZIPu (v bajtech).

    xlsx export i zaloha appky jsou taky ZIP archivy - `sharedStrings.xml`
    nebo `data.json` v nich muze byt samo male, ale po rozbaleni zabrat
    stovky MB pameti ("zip bomba", B-037c). Kontrola je jen soucet velikosti
    z hlavicky archivu (`ZipInfo.file_size`), nic se nerozbaluje.
    """
    return sum(polozka.file_size for polozka in archiv.infolist())


async def precti_s_limitem(nahrany: UploadFile, max_mb: int) -> bytes:
    """Precte cely UploadFile do pameti, ale po kouscich a s limitem.

    Pouziva se u xlsx (import ucastniku) a ZIPu (obnova ze zalohy) - obsah
    uz overi az `openpyxl`/`zipfile`, tady se hlida jen to, aby se velky
    soubor nenacetl cely do pameti drive, nez appka pozna, ze je moc velky.
    """
    max_bajtu = max_mb * 1024 * 1024
    kusy: list[bytes] = []
    velikost = 0
    while kus := await nahrany.read(VELIKOST_KUSU):
        velikost += len(kus)
        if velikost > max_bajtu:
            raise ChybaObsahu(f"má víc než {max_mb} MB")
        kusy.append(kus)
    return b"".join(kusy)
