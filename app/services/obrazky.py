"""Priprava obrazku pro cteni AI a pro nahledy.

Prevadi skeny (PDF, HEIC, JPEG, PNG) na JPEG rozumne velikosti. Vola ho
cteni pres Gemini (`ocr.py`) i nahledy v tabulce (`nahledy.py`) - kazdy jinou
velikost, proto jsou rozmery parametr, ne konstanta uvnitr. Zmena zmensovani
se tak projevi na jednom miste, a ne nahodou v tom druhem modulu.
"""

from __future__ import annotations

import io
from pathlib import Path

import pillow_heif
from PIL import Image

# Fotky z iPhonu byvaji ve formatu HEIC, ktery Pillow sam neumi.
pillow_heif.register_heif_opener()

# Delsi strana obrazku po zmenseni. Vic nema smysl - uctenky jsou citelne
# i takhle a mensi obrazek znamena rychlejsi a levnejsi dotaz.
MAX_HRANA_PX = 1600
# Stranka PDF se vykresluje vetsi. Faktura z e-shopu nebo vypis z uctu je
# hustsi text pres cele A4 - na 1600 px vyjde pismo tak male, ze ho model
# cte s chybami. 2200 px na delsi strane je u A4 zhruba 190 DPI.
MAX_HRANA_PDF_PX = 2200
KVALITA_JPEG = 82


def obrazky_ze_souboru(cesta: Path) -> list[bytes]:
    """Prevede sken na seznam JPEG obrazku, jeden na stranku.

    PDF se vykresluje stranku po strance, jako by se tisklo. Drive se z nej
    tahal vlozeny obrazek, coz fungovalo jen u skenu z tiskarny. U faktury
    z e-shopu nebo u vypisu z uctu je text primo v PDF a jediny vlozeny
    obrazek je logo - model pak dostal jen to logo a nemel co cist.
    """
    if cesta.suffix.lower() == ".pdf":
        return vykresli_pdf(cesta)

    return [zmensit(cesta.read_bytes())]


def vykresli_pdf(cesta: Path) -> list[bytes]:
    """Vykresli kazdou stranku PDF do JPEGu (pouziva cteni AI, ktere potrebuje
    videt vsechny stranky najednou)."""
    import pypdfium2

    dokument = pypdfium2.PdfDocument(cesta)
    try:
        return [
            _vykresli_stranku_dokumentu(dokument[i], MAX_HRANA_PDF_PX) for i in range(len(dokument))
        ]
    finally:
        dokument.close()


def _vykresli_stranku_dokumentu(strana, hrana: int) -> bytes:
    # get_size() je v bodech (72 na palec), meritko je nasobek proti teto
    # velikosti - spocitame ho tak, aby delsi strana vysla na `hrana` px.
    sirka, vyska = strana.get_size()
    meritko = hrana / max(sirka, vyska)
    return na_jpeg(strana.render(scale=meritko).to_pil(), hrana)


def vykresli_stranku(cesta: Path, index: int, hrana: int = MAX_HRANA_PDF_PX) -> bytes:
    """Vykresli jen jednu stranku PDF, ne cely dokument.

    Pouziva nahled (`app/services/nahledy.py`) - ten potrebuje videt jen
    stranku, kterou si uzivatel prave otevrel, ne vsechny (B-037c: PDF s
    desetitisici stranek by jinak appka musela cele vykreslit kvuli jedine
    stranicce v tabulce). Zaroven umi vykreslit rovnou v mensim rozliseni
    (`hrana`), misto vykresleni nastro a nasledneho zmenseni.
    """
    import pypdfium2

    dokument = pypdfium2.PdfDocument(cesta)
    try:
        return _vykresli_stranku_dokumentu(dokument[index], hrana)
    finally:
        dokument.close()


def zmensit(data: bytes) -> bytes:
    """Zmensi obrazek na rozumnou velikost a prevede na JPEG."""
    return na_jpeg(Image.open(io.BytesIO(data)), MAX_HRANA_PX)


def na_jpeg(im: Image.Image, hrana: int = MAX_HRANA_PDF_PX) -> bytes:
    """Zmensi obrazek na `hrana` px a ulozi jako JPEG."""
    if im.mode != "RGB":
        im = im.convert("RGB")
    im.thumbnail((hrana, hrana))
    vystup = io.BytesIO()
    im.save(vystup, "JPEG", quality=KVALITA_JPEG)
    return vystup.getvalue()
