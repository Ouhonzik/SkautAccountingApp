"""Proxy na Mapy.com - dlazdice, hledani, nazvy mist a nahled trasy.

Leaflet i panel u mapy v prohlizeci se ptaji jen na nasi vlastni adrese
(/mapa/...) - tyhle routy to preposilaji dal na Mapy.com a pripoji API klic.
Diky tomu klic nikdy nedorazi do prohlizece ani do zadneho verejneho
JavaScriptu.

Vsechny routy jsou schvalne obycejne `def`, ne `async def`. Uvnitr se vola
synchronni httpx - v `async def` by kazdy dotaz zablokoval cely server
a dlazdice by se stahovaly jedna po druhe (presne tak se mapa predtim
nacitala). Obycejnou `def` FastAPI pusti ve zvlastnim vlakne a dlazdice
jdou soubezne.

Jizdy se do cestaku pridavaji obycejnym radkem (app/routes/cestaky.py); mapa
ho jen vyplnuje v prohlizeci (app/static/cestak_mapa.js). Tady se jen cte.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.services import mapy

router = APIRouter()

# Dlazdice se na Mapy.com meni jen zridka - prohlizec si je muze tyden
# pamatovat a pri dalsim priblizeni nebo navratu na stejny vyrez je uz
# nestahuje. "private" = jen prohlizec, ne pripadne sdilene proxy cestou.
# Na disk serveru se schvalne neukladaji: podminky Mapy.com trvale
# skladovani dlazdic neumoznuji.
# Kratsi dotaz hledani nic rozumneho nenajde. Musi sedet s `MIN_DELKA_DOTAZU` v
# app/static/cestak_mapa.js - jinak by prohlizec posilal dotazy, ktere server zahodi.
MIN_DELKA_DOTAZU = 2
CACHE_DLAZDIC = "private, max-age=604800"


def _over_zapnuti() -> None:
    if not mapy.je_zapnuta():
        raise HTTPException(status_code=404, detail="Mapa není zapnutá.")


def _hlidej_limit(request: Request, druh: str) -> None:
    """Odmitne dotaz nad limitem. Uzivatele nacetla zavislost routeru
    (`zapis_jen_pro_editory`); limit se pocita na nej, ne na IP adresu."""
    if not mapy.zkus_dotaz(request.state.uzivatel.id, druh):
        raise HTTPException(
            status_code=429,
            detail="Mapa dostala za poslední dobu moc dotazů. Zkus to za pár minut.",
        )


def _jen_pro_editory(request: Request) -> None:
    """Hledani a plan trasy stoji kredity a revizor je nepotrebuje - cte jen
    hotove trasy. Dlazdice videt smi."""
    if not request.state.uzivatel.muze_zapisovat:
        raise HTTPException(status_code=403, detail="Revizor mapu hledat ani plánovat nesmí.")


@router.get("/mapa/dlazdice/{z}/{x}/{y}")
def dlazdice(request: Request, z: int, x: int, y: int):
    _over_zapnuti()
    _hlidej_limit(request, mapy.DLAZDICE)
    try:
        obsah, typ = mapy.dlazdice(z, x, y)
    except mapy.ChybaMapy as chyba:
        raise HTTPException(status_code=502, detail=str(chyba)) from chyba
    return Response(content=obsah, media_type=typ, headers={"Cache-Control": CACHE_DLAZDIC})


@router.get("/mapa/hledat")
def hledat(
    request: Request,
    q: str = Query("", max_length=200),
    lat: float | None = Query(None),
    lon: float | None = Query(None),
):
    _over_zapnuti()
    _jen_pro_editory(request)
    dotaz = q.strip()
    # Jedno pismeno nic rozumneho nenajde a stalo by kredity - a proto se ani
    # nepocita do limitu.
    if len(dotaz) < MIN_DELKA_DOTAZU:
        return []
    _hlidej_limit(request, mapy.HLEDANI)
    try:
        return mapy.hledej(dotaz, lat, lon)
    except mapy.ChybaMapy as chyba:
        raise HTTPException(status_code=502, detail=str(chyba)) from chyba


@router.get("/mapa/misto")
def misto(request: Request, lat: float, lon: float):
    _over_zapnuti()
    _jen_pro_editory(request)
    _hlidej_limit(request, mapy.HLEDANI)
    try:
        return {"nazev": mapy.nazev_mista(lat, lon)}
    except mapy.ChybaMapy as chyba:
        raise HTTPException(status_code=502, detail=str(chyba)) from chyba


@router.get("/mapa/trasa")
def trasa(request: Request, z_lat: float, z_lon: float, do_lat: float, do_lon: float):
    _over_zapnuti()
    _jen_pro_editory(request)
    _hlidej_limit(request, mapy.HLEDANI)
    try:
        return mapy.trasa(z_lat, z_lon, do_lat, do_lon)
    except mapy.ChybaMapy as chyba:
        raise HTTPException(status_code=502, detail=str(chyba)) from chyba
