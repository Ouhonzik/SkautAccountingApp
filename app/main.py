"""Vstupni bod webove aplikace. Spousti se prikazem `uv run app`."""

import asyncio
import contextlib
import logging
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response
from starlette.types import Scope

from app import auth, chyby, formulare
from app.config import nastaveni, zkontroluj_nastaveni
from app.db import Sezeni, engine
from app.logovani import nastav_logovani
from app.routes import (
    akce,
    cestaky,
    doklady,
    export,
    kategorie,
    lide,
    log,
    mapa,
    moje_doklady,
    obdarovani,
    obnova,
    pokladni_kniha,
    prijmy,
    profil,
    skeny,
    slozky,
    ucastnici,
    ucet,
    uzivatele,
    vozidla,
)
from app.routes.spolecne import (
    HLASKA_AKCE_UZAVRENA,
    HLASKA_PRIHLASENI_VYPRSELO,
    AkceUzavrena,
    Databaze,
    adresa_zpet,
    je_prubezne,
    odpoved_401,
    prubezna_chyba,
)
from app.sablony import SLOZKA_APP, oznam, zpet
from app.services import soubory, stav_migraci, uklid
from app.services import zdravi as kontrola_zdravi

# Nastaveni logovani pri importu, ne v `run()`: uvicorn s RELOAD=true spousti
# appku v podprocesu, ktery `run()` nevola.
nastav_logovani(nastaveni.log_uroven)

zurnal = logging.getLogger(__name__)
# Pristupovy log: jen to, co je potreba resit (viz `zapisuj_pozadavky`).
zurnal_pozadavku = logging.getLogger("app.pozadavky")
# Pomalejsi pozadavek (v sekundach) se zapise i bez chyby.
POMALY_POZADAVEK_S = 3.0


@asynccontextmanager
async def pri_startu(_: FastAPI):
    # Nastaveni se kontroluje jako prvni, driv nez appka sahne na disk nebo
    # databazi: na serveru spatne nastaveni znamena, ze se nesmi spustit,
    # a vsechny chyby se vypisou najednou, aby se neopravovaly po jedne.
    chyby, varovani = zkontroluj_nastaveni()
    if chyby:
        raise RuntimeError(
            "Appka se nespustí, nastavení v .env nesedí:\n  - " + "\n  - ".join(chyby)
        )
    for varovani_text in varovani:
        zurnal.warning("Nastavení: %s", varovani_text)
    # Do ciselniku kategorii se pri startu uz nesaha. Vychozi sadu nalije
    # jednou migrace e3a91c47b5d2, pak uz je ciselnik Honzuv.
    nastaveni.zaloz_slozky()
    soubory.vycisti_tmp()
    if not inspect(engine).has_table("uzivatel"):
        zurnal.error("Databáze ještě nemá tabulku uzivatel. Spusť: uv run alembic upgrade head")
    # Zastarala databaze: jen varovani do logu a spravci, nic se nemigruje.
    stav_migraci.zkontroluj_pri_startu(engine)
    # Mazani starych udaju: hned po startu a pak jednou denne.
    uklid_ukol = asyncio.create_task(uklid.denni_smycka(Sezeni))
    try:
        yield
    finally:
        uklid_ukol.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await uklid_ukol


# Automatickou dokumentaci API (/docs, /redoc, /openapi.json) appka nepotrebuje -
# vsechno jsou HTML stranky. FastAPI by ji pridal primo na `app`, mimo routery,
# takze by ji hlidal jen middleware bez databaze: stara cookie deaktivovaneho
# uctu by si stahla mapu vsech endpointu vcetne spravcovskych.
app = FastAPI(
    title="Účtování akcí",
    lifespan=pri_startu,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


class StatickeSoubory(StaticFiles):
    """StaticFiles s hlavickou Cache-Control.

    Bez ni se prohlizec pred kazdym pouzitim zeptal serveru, jestli se soubor zmenil,
    a na kazde strance tak cekal na 5-8 odpovedi 304. Odkaz s `?v=<razitko>` (viz
    `sablony.verze`) dostane po zmene souboru novou adresu, proto smi platit rok
    (`immutable` = prohlizec se ani pri obnoveni stranky nepta). Bez razitka (pisma
    z app.css, obrazky z knihoven) by zmena zustala nevidet, proto jen hodinu.
    """

    def file_response(
        self, full_path, stat_result, scope: Scope, status_code: int = 200
    ) -> Response:
        odpoved = super().file_response(full_path, stat_result, scope, status_code)
        razitko = parse_qs(scope.get("query_string", b"").decode("latin-1")).get("v", [""])[0]
        odpoved.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if razitko else "public, max-age=3600"
        )
        return odpoved


app.mount("/static", StatickeSoubory(directory=str(SLOZKA_APP / "static")), name="static")


@app.middleware("http")
async def hlidej_prihlaseni(request: Request, dalsi):
    """Bez prihlaseni se dovnitr nedostane nikdo krom verejnych adres.

    Tohle je jen rychla kontrola, ze v cookie vubec neco je - jestli ucet
    opravdu existuje, je aktivni a smi na danou stranku, resi az zavislosti
    v app/auth.py (nacti_uzivatele, zapis_jen_pro_editory, vyzaduj_spravce)
    zaregistrovane u jednotlivych routeru nize. Middleware sam databazi
    nema: `dependency_overrides` pouzivane v testech se na middleware
    nevztahuje, takze by testy sahaly na skutecnou data/akce.db.

    Zaroven tu je zakladni ochrana proti CSRF: cizi puvod pozna
    `auth.je_cizi_pozadavek` z hlavicky `Sec-Fetch-Site`, a kdyz chybi,
    z `Origin` proti VEREJNA_URL. `SameSite=Lax` u prihlasovaci cookie uz
    cross-site POST beztak nepusti k session, tohle navic odmitne
    i pozadavek ze sesterske subdomeny (`Sec-Fetch-Site: same-site`), ktery
    appka nepotrebuje - vlastni formulare i `fetch` posilaji jen same-origin.
    Kontrola plati i pro verejne cesty (napr. pokus o prihlaseni z cizi
    stranky).
    """
    if auth.je_cizi_pozadavek(request):
        return PlainTextResponse("Požadavek odmítnut (cizí původ).", status_code=403)
    if auth.je_verejna(request.url.path) or auth.je_prihlasen(request):
        auth.obnov_prihlaseni(request)
        return await dalsi(request)
    # Prubezne ukladani (fetch) by presmerovani nasledoval a bublina by rekla
    # "Ulozeno" - proto 401 s vetou, ne 303.
    if je_prubezne(request):
        return odpoved_401()
    return zpet("/prihlaseni")


# POZOR NA PORADI: co se prida pozdeji, obaluje to drivejsi. SessionMiddleware
# proto musi byt az za kontrolou prihlaseni - jinak by kontrola bezela driv,
# nez session vubec existuje, a spadla by.
# Prihlaseni si pamatuje podepsana cookie. Zmenou SECRET_KEY se vsichni odhlasi.
app.add_middleware(
    SessionMiddleware,
    secret_key=nastaveni.secret_key,
    max_age=auth.PLATNOST_PRIHLASENI_S,
    same_site="lax",
    https_only=nastaveni.cookie_https_only,
)


# B-036: bez tohodle by appka odpovedela na jakoukoli hlavicku Host a slozila
# z ni adresy statickych souboru - dnes to chrani jen konfigurace Caddy
# (jeden blok pro jednu domenu). Pridano AZ ZA SessionMiddleware (pozdejsi
# add_middleware obaluje drivejsi, viz komentar vyse), takze cizi Host dostane
# 400 driv, nez SessionMiddleware vubec zacne cist cookie. www_redirect=False,
# appka zadnou variantu s "www." nema a presmerovani by tu nic nevyresilo.
# Testy pridavaji "testserver" (hlavicka Host, kterou posila TestClient) -
# viz testy.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=auth.povolene_hosty(), www_redirect=False)


@app.middleware("http")
async def bezpecnostni_hlavicky(request: Request, dalsi):
    """Zaregistrovano jako posledni, takze obaluje uplne vsechno (i 403 od
    hlidej_prihlaseni a 400 od TrustedHostMiddleware) - hlavicky tak dostane
    kazda odpoved, ne jen ty, ktere projdou kontrolou prihlaseni."""
    odpoved = await dalsi(request)
    odpoved.headers["X-Content-Type-Options"] = "nosniff"
    odpoved.headers["X-Frame-Options"] = "DENY"
    return odpoved


@app.middleware("http")
async def zapisuj_pozadavky(request: Request, dalsi):
    """Vlastni pristupovy log misto uvicornovho: zapise jen
    odpovedi se stavem 400 a vic (vcetne 403 z kontroly puvodu, prihlaseni
    a roli) a pozadavky pomalejsi nez `POMALY_POZADAVEK_S`. Uspesne a presmerovane
    pozadavky ne - jinak by chybu 500 pohrbily stovky radku za dlazdice mapy
    a prubezne ukladani.

    Zaregistrovano jako posledni, takze obaluje uplne vsechno (i 400 od
    TrustedHostMiddleware). Zapisuje metodu, cestu BEZ query stringu a s
    maskovanym tokenem, stav, dobu a id uzivatele - nikdy jmeno, hlavicky
    ani telo. ID uzivatele se cte ze `scope`, protoze SessionMiddleware ho
    uvnitr naplni az pri pruchodu.
    """
    zacatek = time.monotonic()
    stav = 500
    try:
        odpoved = await dalsi(request)
        stav = odpoved.status_code
        return odpoved
    finally:
        doba = time.monotonic() - zacatek
        if stav >= 400 or doba >= POMALY_POZADAVEK_S:
            sezeni = request.scope.get("session") or {}
            zurnal_pozadavku.log(
                logging.ERROR if stav >= 500 else logging.WARNING,
                "%s %s -> %s (%d ms, uživatel %s)",
                request.method,
                auth.cesta_pro_log(request.url.path),
                stav,
                round(doba * 1000),
                sezeni.get(auth.KLIC_UZIVATEL, "-"),
            )


@app.exception_handler(auth.Presmerovat)
async def presmeruj(request: Request, chyba: auth.Presmerovat):
    """Zavislosti (Depends) nemuzou vratit presmerovani primo - ohlasi ho
    touhle vyjimkou a tady se z ni udela skutecny redirect. Bezi to uvnitr
    SessionMiddleware, takze session je dostupna.

    Prubezne ukladani dostane misto presmerovani 401 s vetou: `fetch`
    by redirect nasledoval a bublina by hlasila "Ulozeno". Hlaska se pak nevklada
    do session, kde by vyskocila az na jine strance.
    """
    if je_prubezne(request):
        zprava = chyba.zprava if chyba.kam != "/prihlaseni" else HLASKA_PRIHLASENI_VYPRSELO
        return odpoved_401(zprava or HLASKA_PRIHLASENI_VYPRSELO)
    if chyba.zprava:
        oznam(request, chyba.zprava, chyba.druh)
    return zpet(chyba.kam)


@app.exception_handler(AkceUzavrena)
async def akce_je_uzavrena(request: Request, _chyba: AkceUzavrena):
    """Zapis do uzavrene akce: server ho odmitl, tady se jen rekne
    cloveku proc. Stav 409 s vetou dostane prubezne ukladani (`fetch` redirect
    nasleduje a bublina by rekla "Ulozeno"), obycejny formular hlasku a navrat na
    stranku, odkud prisel - misto samostatne chybove stranky.

    Bezi uvnitr SessionMiddleware, takze `oznam` ma kam hlasku ulozit.
    """
    odpoved = prubezna_chyba(request, HLASKA_AKCE_UZAVRENA)
    if odpoved is not None:
        return odpoved
    oznam(request, HLASKA_AKCE_UZAVRENA, "pozor")
    return zpet(adresa_zpet(request))


@app.exception_handler(RequestValidationError)
async def chyba_formulare(request: Request, chyba: RequestValidationError):
    """Spatne vyplneny formular (app/formulare.py): hlaska a navrat na stranku,
    odkud clovek prisel - misto surove odpovedi 422 s JSONem. Do databaze se pri
    tom nic nezapsalo, route se vubec nespustila. Chyby v adrese nebo dotazu
    (ne v tele formulare) dal vyridi vychozi obsluha FastAPI.

    Bezi uvnitr SessionMiddleware, takze `oznam` ma kam hlasku ulozit.
    """
    zprava = formulare.hlaska(chyba)
    if zprava is None or request.method != "POST":
        # Chyba v adrese nebo dotazu, nebo formular bez ceske vety: stranka s chybou.
        return chyby.odpoved_chyby(request, 422, chyby.TEXT_422)
    # Jen jmena poli a typy chyb - zadane hodnoty jsou osobni udaje.
    # Cesta s maskovanym tokenem (/pozvanka/***) - token z adresy nesmi do logu.
    zurnal.info(
        "Formulář %s zamítnut, pole: %s",
        auth.cesta_pro_log(request.url.path),
        formulare.pole_s_chybou(chyba),
    )
    # Prubezne ukladani (fetch) presmerovani nasleduje a bublina by rekla
    # "Ulozeno", i kdyz se nic neulozilo - proto 422 s vetou, ne 303.
    if je_prubezne(request):
        return PlainTextResponse(zprava, status_code=422)
    oznam(request, zprava, "chyba")
    return zpet(adresa_zpet(request))


# Vlastni chybove stranky: bez nich by 404/403 ukazaly syrovy JSON
# a neosetrena chyba anglicke "Internal Server Error". Handler pro `Exception`
# bezi mimo SessionMiddleware (viz `chyby.neosetrena_chyba`).
app.add_exception_handler(StarletteHTTPException, chyby.http_chyba)
app.add_exception_handler(Exception, chyby.neosetrena_chyba)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Prohlizec si o ikonu rika sam. Bez tohohle by ho prihlasovaci zed
    posilala na prihlaseni a v konzoli by svitila chyba."""
    return RedirectResponse("/static/favicon.svg", status_code=301)


@app.get("/zdravi")
def zdravi(sezeni: Databaze):
    """Kontrola, ze appka bezi, databaze jde cist a na disku je misto. Zvenku ji
    kazdych 5 minut vola UptimeRobot. Obycejna `def` (SQLite je synchronni).
    Chyba = 503 bez podrobnosti, adresa je verejna."""
    if kontrola_zdravi.je_v_poradku(sezeni):
        return {"stav": "ok"}
    return JSONResponse({"stav": "chyba"}, status_code=503)


# Bezna prace s akcemi: kdokoli prihlaseny cte, revizor nesmi zapisovat -
# `zapis_jen_pro_editory` to vynuti servrem u kazde POST/PUT/DELETE.
CTENI_I_ZAPIS = [Depends(auth.zapis_jen_pro_editory)]
for smerovac in (
    akce,
    skeny,
    slozky,
    prijmy,
    pokladni_kniha,
    doklady,
    ucastnici,
    obdarovani,
    kategorie,
    lide,
    vozidla,
    cestaky,
    mapa,
    export,
):
    app.include_router(smerovac.router, dependencies=CTENI_I_ZAPIS)

# Profil: i s docasnym heslem, i revizor - viz docstring v app/routes/profil.py.
app.include_router(profil.router, dependencies=[Depends(auth.nacti_uzivatele)])
# Moje doklady: jediny router dostupny i pro roli Podepisujici (auth.prihlaseny
# nekontroluje auth.pristup_k_akcim).
app.include_router(moje_doklady.router, dependencies=[Depends(auth.prihlaseny)])
# Ucet: verejne cesty (pozvanka, zapomenute heslo, obnova hesla) - bez zavislosti, jsou
# v auth.VEREJNE_CESTY, takze se k nim middleware pusti bez prihlaseni.
app.include_router(ucet.router)
# Sprava uctu, log cinnosti a obnova ze zalohy jsou jen pro spravce.
app.include_router(uzivatele.router, dependencies=[Depends(auth.vyzaduj_spravce)])
app.include_router(log.router, dependencies=[Depends(auth.vyzaduj_spravce)])
app.include_router(obnova.router, dependencies=[Depends(auth.vyzaduj_spravce)])


def run() -> None:
    nastaveni.zaloz_slozky()
    # Vychozi 127.0.0.1 ukazuje na "localhost" jako dosud; jina adresa (HOST) se
    # vypise doslova, IPv6 v hranatych zavorkach jako v URL.
    adresa = "localhost" if nastaveni.host == "127.0.0.1" else nastaveni.host
    if ":" in adresa:
        adresa = f"[{adresa}]"
    print(f"\n  Aplikace běží na http://{adresa}:{nastaveni.port}\n")
    # proxy_headers/forwarded_allow_ips: appka na serveru beží za Caddy
    # (reverse proxy), ktery klientskou IP posila v hlavicce
    # X-Forwarded-For - bez tohohle by request.client.host videl vsude jen
    # Caddyho vlastni adresu. Allow-list (FORWARDED_ALLOW_IPS) znamena
    # "duveruj tehle hlavicce jen kdyz pozadavek prisel z techto adres" -
    # vychozi "127.0.0.1" je presne odkud Caddy pristupuje, kdyz bezi primo na
    # serveru, takze je to bezpecne i lokalne bez Caddyho. Kdyz Caddy bezi
    # v Dockeru, pristupuje z Docker site a appka (HOST) slucha na jeho brane;
    # pak se do allow-listu da sit (napr. 172.18.0.0/16). Nikdy "*": hlavicku
    # by pak mohl podvrhnout kdokoli, kdo se k appce dostane primo.
    uvicorn.run(
        "app.main:app",
        host=nastaveni.host,
        port=nastaveni.port,
        reload=nastaveni.reload,
        # Vlastni log chyb viz `zapisuj_pozadavky`: uvicornovy pristupovy log
        # zapisuje i tokeny z adres a kazdou dlazdici mapy.
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips=nastaveni.forwarded_allow_ips,
    )
