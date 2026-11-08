"""Volani Mapy.com pro planovani trasy cestovnich prikazu.

Cely provoz jde pres nas server, ne primo z prohlizece (routa
app/routes/mapa.py) - diky tomu klic z .env nikdy neuvidi prohlizec ani
serverovy log (posila se hlavickou X-Mapy-Api-Key, ne v adrese).

Bez klice (MAPY_API_KEY v .env prazdny) je mapa proste vypnuta - je_zapnuta()
vraci False a v cestaku se jizdy pisou rucne, viz app/routes/cestaky.py.
"""

from __future__ import annotations

import logging
import threading
import time
from decimal import ROUND_HALF_UP, Decimal

import httpx

from app.config import nastaveni
from app.services.omezeni_prihlaseni import Pravidlo, omezovac

log = logging.getLogger(__name__)

ZAKLADNI_URL = "https://api.mapy.com/v1"
# Mapy.com obcas odpovidaji pomaleji nez bezna API - radeji chvili pockat,
# nez hned hlasit chybu a nutit Honzu kliknout znovu.
CASOVY_LIMIT = 10.0

# Zakladni (turisticky nepretizena) mapa staci na planovani trasy autem.
# "256" je velikost dlazdice v bodech. Varianta s priponou "@1"/"@2" (retina)
# vypada podle stareho zapisu API rozumne, ale aktualni Mapy.com na ni
# odpovidaji 404 - overeno primym volanim, funguje jen holé cislo.
MAPSET = "basic"
VELIKOST_DLAZDICE = "256"

# Kolik vysledku ukazat v naseptavaci hledani - vic se do panelu u mapy
# stejne nevejde a kazdy dotaz stoji kredity.
POCET_VYSLEDKU_HLEDANI = 6

# Limity dotazu na Mapy.com na uzivatele: kazdy dotaz stoji kredity
# klice strediska a skript s platnou cookie by je za chvili vycerpal vsem.
# Posouvani mapy se do 600 dlazdic za 10 minut vejde s rezervou. Hledani,
# nazev mista a trasa maji spolecny rozpocet (60 za 10 minut); pridani jizdy
# z mapy se pocita jako jedno hledani. Drzi se v pameti, restart je vynuluje.
DLAZDICE = "dlazdice"
HLEDANI = "hledani"
LIMITY_DOTAZU = {DLAZDICE: (600, 600), HLEDANI: (60, 600)}


def zkus_dotaz(uzivatel_id: int, druh: str) -> bool:
    """Smi uzivatel ted poslat dalsi dotaz tohoto druhu? Kdyz ano, rovnou se
    zapocita. False = limit vycerpan, na Mapy.com se nic neposila."""
    limit, okno_s = LIMITY_DOTAZU[druh]
    return omezovac.zkus_pouzit(Pravidlo(f"mapa-{druh}:{uzivatel_id}", limit, okno_s))


# Jedno spojeni na Mapy.com pro cely proces, ne nove pro kazdy dotaz. Mapa
# si pri kazdem posunu rekne o desitky dlazdic - s httpx.get() by se pro
# kazdou z nich znovu navazovalo sifrovane spojeni, a to bylo znat. Klient
# se zaklada az pri prvnim pouziti (testy ho tak nikdy nevytvori) a je
# chraneny zamkem, protoze routy map bezi ve vice vlaknech najednou.
_klient: httpx.Client | None = None
_zamek_klienta = threading.Lock()


class ChybaMapy(Exception):
    """Mapy.com neodpovedely, nebo vratily neco, co se necekalo.

    Text jde primo uzivateli (flash v cestaku, odpoved /mapa/*), proto je to
    vzdy hotova ceska veta - nikdy do ni nevkladej text puvodni vyjimky:
    httpx v nem nese celou adresu pozadavku. Podrobnosti se zapisuji do logu
    pres `_zaloguj_chybu`.
    """


# Stejna chyba (koncovy bod + kod) se do logu zapise nejvys jednou za minutu.
# Mapa si pri kazdem posunu rekne o desitky dlazdic - pri neplatnem klici by
# selhaly vsechny a log by se zaplavil. Routy bezi ve vice vlaknech.
INTERVAL_ZAPISU_CHYB_S = 60.0
_posledni_zapis: dict[tuple[str, str], float] = {}
_zamek_zapisu = threading.Lock()


def _ted() -> float:
    return time.monotonic()


def _zaloguj_chybu(koncovy_bod: str, chyba: httpx.HTTPError) -> None:
    """Zapise do logu serveru, co se s Mapy.com stalo: jen koncovy bod
    (`/suggest`, `/maptiles`, ...) a stavovy kod, u vypadku spojeni nazev typu
    vyjimky. Nikdy adresu s parametry (jsou v ni souradnice soukromych cest)
    a nikdy text vyjimky."""
    odpoved = getattr(chyba, "response", None)
    kod = str(odpoved.status_code) if odpoved is not None else type(chyba).__name__
    klic = (koncovy_bod, kod)
    with _zamek_zapisu:
        ted = _ted()
        minule = _posledni_zapis.get(klic)
        if minule is not None and ted - minule < INTERVAL_ZAPISU_CHYB_S:
            return
        _posledni_zapis[klic] = ted
    log.warning("Mapy.com: %s selhalo (%s)", koncovy_bod, kod)


def je_zapnuta() -> bool:
    """Bez klice v .env se mapa v cestaku vubec nenabizi."""
    return bool(nastaveni.mapy_api_key)


def _get(url: str, params: dict[str, str] | None = None) -> httpx.Response:
    """Jediny misto, kudy jde pozadavek ven. Testy nahrazuji prave tuhle funkci."""
    global _klient
    if _klient is None:
        with _zamek_klienta:
            if _klient is None:
                _klient = httpx.Client(
                    timeout=CASOVY_LIMIT,
                    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                )
    return _klient.get(url, params=params, headers={"X-Mapy-Api-Key": nastaveni.mapy_api_key})


def _over_zapnuti() -> None:
    if not je_zapnuta():
        raise ChybaMapy("Mapa není zapnutá.")


def _pozadavek(cesta: str, parametry: dict[str, str]) -> dict:
    """Spolecne jadro vsech dotazu - GET s klicem v hlavicce a hlidanim chyb."""
    try:
        odpoved = _get(f"{ZAKLADNI_URL}{cesta}", parametry)
        odpoved.raise_for_status()
    except httpx.HTTPError as chyba:
        _zaloguj_chybu(cesta, chyba)
        raise ChybaMapy("Mapy.com teď neodpovídají, zkus to později.") from chyba
    try:
        return odpoved.json()
    except ValueError as chyba:
        raise ChybaMapy("Mapy.com vrátily odpověď, které nerozumím.") from chyba


def _dotaz_na_trasu(z_lat: float, z_lon: float, do_lat: float, do_lon: float, **navic) -> dict:
    return _pozadavek(
        "/routing/route",
        {
            "start": f"{z_lon},{z_lat}",
            "end": f"{do_lon},{do_lat}",
            "routeType": "car_fast",
            **navic,
        },
    )


def _km_z_trasy(data: dict) -> Decimal:
    """Delka trasy v metrech -> kilometry na jedno desetinne misto (12 370 m -> 12,4).

    Stejne pravidlo jako u rucne zadanych km (`formulare.sloupce_jizdy`), aby stejna
    jizda vysla stejne, at ji clovek napsal, nebo ji spocitala mapa.
    """
    try:
        delka_m = Decimal(str(data["length"]))
    except (KeyError, TypeError, ValueError) as chyba:
        raise ChybaMapy("Mapy.com vrátily neočekávanou odpověď na dotaz na trasu.") from chyba
    return (delka_m / 1000).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def trasa(z_lat: float, z_lon: float, do_lat: float, do_lon: float) -> dict:
    """Trasa po silnici: {"km": 7.5, "body": [[lat, lon], ...]}.

    Kilometry (na desetiny) prohlizec doplni do pole radku jizdy; clovek je
    pred ulozenim muze prepsat. Pocita je vzdy server, klic k Mapy.com
    prohlizec nevidi.
    """
    _over_zapnuti()
    data = _dotaz_na_trasu(z_lat, z_lon, do_lat, do_lon, format="geojson")
    km = _km_z_trasy(data)
    try:
        souradnice = data["geometry"]["geometry"]["coordinates"]
        # GeoJSON pise [lon, lat], Leaflet chce [lat, lon].
        body = [[float(bod[1]), float(bod[0])] for bod in souradnice]
    except (KeyError, TypeError, ValueError, IndexError) as chyba:
        raise ChybaMapy("Mapy.com vrátily neočekávanou odpověď na dotaz na trasu.") from chyba
    return {"km": km, "body": body}


def nazev_mista(lat: float, lon: float) -> str:
    """Nazev mista pro zadane souradnice, napr. 'Brno' nebo 'Rozhledna Kozi horka'."""
    _over_zapnuti()
    data = _pozadavek("/rgeocode", {"lon": str(lon), "lat": str(lat)})
    try:
        polozky = data["items"]
    except (KeyError, TypeError) as chyba:
        raise ChybaMapy("Mapy.com vrátily neočekávanou odpověď na dotaz na místo.") from chyba
    if not polozky:
        return "Neznámé místo"
    try:
        return polozky[0]["name"]
    except (KeyError, TypeError, IndexError) as chyba:
        raise ChybaMapy("Mapy.com vrátily neočekávanou odpověď na dotaz na místo.") from chyba


def hledej(dotaz: str, lat: float | None = None, lon: float | None = None) -> list[dict]:
    """Naseptavac mist a adres: [{"nazev", "popis", "lat", "lon"}, ...].

    S lat/lon (stred mapy) Mapy.com radi vys vysledky pobliz - "Lidl" pak
    najde ten v Brne, ne v Asi.
    """
    _over_zapnuti()
    parametry = {"query": dotaz, "lang": "cs", "limit": str(POCET_VYSLEDKU_HLEDANI)}
    if lat is not None and lon is not None:
        parametry["preferNear"] = f"{lon},{lat}"
    data = _pozadavek("/suggest", parametry)
    try:
        polozky = data["items"]
    except (KeyError, TypeError) as chyba:
        raise ChybaMapy("Mapy.com vrátily neočekávanou odpověď na hledání.") from chyba
    vysledky = []
    for polozka in polozky or []:
        try:
            vysledky.append(
                {
                    "nazev": polozka["name"],
                    # "Supermarket · Šumavská 519/35, Brno - Veveří" - podle
                    # popisu se pozna, ktery ze stejne pojmenovanych Lidlu to je.
                    "popis": " · ".join(
                        cast for cast in (polozka.get("label"), polozka.get("location")) if cast
                    ),
                    "lat": float(polozka["position"]["lat"]),
                    "lon": float(polozka["position"]["lon"]),
                }
            )
        except KeyError, TypeError, ValueError:
            # Jedna podivna polozka nema shodit cele hledani.
            continue
    return vysledky


def dlazdice(z: int, x: int, y: int) -> tuple[bytes, str]:
    """Stahne jednu mapovou dlazdici a vrati (obsah, typ obsahu pro hlavicku).

    Volana z app/routes/mapa.py - Leaflet v prohlizeci si o dlazdice rika na
    nasi vlastni adrese, aby klic nikdy nedorazil do prohlizece.
    """
    _over_zapnuti()
    try:
        odpoved = _get(f"{ZAKLADNI_URL}/maptiles/{MAPSET}/{VELIKOST_DLAZDICE}/{z}/{x}/{y}")
        odpoved.raise_for_status()
    except httpx.HTTPError as chyba:
        _zaloguj_chybu("/maptiles", chyba)
        raise ChybaMapy("Mapy.com teď neposlaly mapový podklad, zkus to později.") from chyba
    return odpoved.content, odpoved.headers.get("content-type", "image/png")
