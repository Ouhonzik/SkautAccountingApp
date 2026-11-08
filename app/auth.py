"""Prihlaseni a opravneni.

Ucty ziji v databazi (tabulka `uzivatel`), ne v .env - kazdy clovek ze
strediska ma svuj login, heslo a roli. Session si po prihlaseni pamatuje jen
id uctu a "verzi hesla"; skutecna data (jmeno, role, aktivni) se pri kazdem
pozadavku nactou z databaze, aby zmena role nebo deaktivace zabrala hned,
ne az po dalsim prihlaseni.

Heslo se v databazi neuklada citelne, ale jako otisk (hash) pres bcrypt.
Z otisku se puvodni heslo zpetne zjistit neda.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import NamedTuple
from urllib.parse import urlsplit

import bcrypt
from fastapi import Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.config import nastaveni
from app.db import Databaze
from app.models import Uzivatel
from app.services.uzivatele import (
    najdi_ucet,
    zahesluj,  # noqa: F401 - re-export, pouziva `auth.zahesluj` v testech
)

KLIC_UZIVATEL = "uzivatel_id"
KLIC_VERZE_HESLA = "verze_hesla"
KLIC_AKTIVNI_V = "aktivni_v"

# Prihlaseni vyprsi po dni bez aktivity. Cookie nese podpis s casem
# a Starlette ji znovu podepise jen kdyz se session zmeni - bez `obnov_prihlaseni`
# by platila od prihlaseni, ne od posledni aktivity, a vyhodila by i toho,
# kdo pracuje.
PLATNOST_PRIHLASENI_S = 24 * 3600
# Cas aktivity se do cookie zapisuje nejvys jednou za tuhle dobu, aby
# Set-Cookie nesla kazda odpoved vcetne obrazku a stylu.
OBNOVA_PODPISU_S = 300

# Hodnoty APP_PASSWORD_HASH, ktere znamenaji "jeste nevyplneno". Pouziva
# migrace b42fe20a9964 - pozna podle nich, jestli ma z .env prevzit ucet,
# nebo je .env jen nedotcena ukazka a spravce se ma zalozit prikazem
# `uv run spravce`.
NEVYPLNENO = {"", "vygenerujeme-pozdeji"}

# Adresy dostupne bez prihlaseni.
VEREJNE_CESTY = (
    "/prihlaseni",
    "/odhlaseni",
    "/static",
    "/zdravi",
    "/favicon",
    "/pozvanka",
    "/zapomenute-heslo",
    "/heslo",
)

# Cesty, jejichz posledni cast je jednorazovy token (pozvanka, obnova hesla,
# potvrzeni e-mailu). V logu serveru se token nahradi `***` - kdo cte
# zurnal, by jinak mohl platny token pouzit. Nova cesta s tokenem patri sem.
CESTY_S_TOKENEM = (
    "/pozvanka/",
    "/heslo/obnovit/",
    "/profil/email/potvrdit/",
)


def cesta_pro_log(cesta: str) -> str:
    """Cesta bezpecna pro zapis do logu: token v `CESTY_S_TOKENEM` nahrazen `***`.
    Query string se sem nedostane (bere se jen `request.url.path`)."""
    for predpona in CESTY_S_TOKENEM:
        if cesta.startswith(predpona):
            return predpona + "***"
    return cesta


# Otisk neexistujiciho hesla. Kdyz nekdo zkusi neznamy login, porovnava se
# stejne dlouho, jako kdyby ucet existoval - jinak by rychlost odpovedi
# prozradila, ktere loginy v databazi jsou.
_FALESNY_OTISK = bcrypt.hashpw(b"neexistujici-ucet", bcrypt.gensalt()).decode()


class Aktor(NamedTuple):
    """Kdo pravě dela zmeny v databazi - pro log cinnosti (app/services/log_cinnosti/zapisovani.py)."""

    id: int | None
    jmeno: str


class Presmerovat(Exception):
    """Zavislost (Depends) nemuze vratit presmerovani jako FastAPI response,
    proto ho ohlasi vyjimkou - main.py ji zachyti a promeni na skutecny
    redirect."""

    def __init__(self, kam: str, zprava: str | None = None, druh: str = "pozor"):
        self.kam = kam
        self.zprava = zprava
        self.druh = druh
        super().__init__(zprava or kam)


def over_heslo(uzivatel: Uzivatel, heslo: str) -> bool:
    try:
        return bcrypt.checkpw(heslo.encode(), uzivatel.otisk_hesla.encode())
    except ValueError:
        return False


def overit(sezeni: Session, login: str, heslo: str) -> Uzivatel | None:
    """Overi jmeno a heslo proti databazi. Vrati ucet, nebo None.

    Kdyz `login` obsahuje zavinac, hleda se podle e-mailu (bez ohledu na
    velikost pismen) - appka jinak nema samostatne "prihlasit e-mailem" pole,
    jen jedno spolecne (`uzivatele.najdi_ucet`)."""
    uzivatel = najdi_ucet(sezeni, login)
    if uzivatel is not None:
        spravne_heslo = over_heslo(uzivatel, heslo)
    else:
        # Porovnat i tak, proti falesnemu otisku - jinak by rychlost odpovedi
        # prozradila, ktere loginy v databazi jsou.
        try:
            bcrypt.checkpw(heslo.encode(), _FALESNY_OTISK.encode())
        except ValueError:
            pass
        spravne_heslo = False
    if uzivatel is None or not uzivatel.aktivni or not spravne_heslo:
        return None
    if uzivatel.docasne_heslo_do is not None and uzivatel.docasne_heslo_do < _ted_utc():
        # Docasne heslo od spravce vyprselo. Stejna odpoved jako u
        # spatneho hesla - spravce vygeneruje nove.
        return None
    return uzivatel


def _ted_utc() -> dt.datetime:
    """Naivni UTC - stejna konvence jako u ostatnich casu v databazi."""
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def existuje_ucet(sezeni: Session) -> bool:
    return bool(sezeni.scalar(select(func.count(Uzivatel.id))))


def _ted_unix() -> int:
    return int(time.time())


def prihlas(request: Request, uzivatel: Uzivatel) -> None:
    """Zapise prihlaseni do session. `clear()` smaze i zbytky predchozi
    session (napr. volbu tarifu) - kazde prihlaseni zacina od nuly."""
    request.session.clear()
    request.session[KLIC_UZIVATEL] = uzivatel.id
    request.session[KLIC_VERZE_HESLA] = uzivatel.verze_hesla
    request.session[KLIC_AKTIVNI_V] = _ted_unix()


def obnov_prihlaseni(request: Request) -> None:
    """Posune platnost prihlaseni: zapis do session cookie znovu podepise.
    Neprihlasenemu nedela nic."""
    if KLIC_UZIVATEL not in request.session:
        return
    posledni = request.session.get(KLIC_AKTIVNI_V, 0)
    if _ted_unix() - posledni >= OBNOVA_PODPISU_S:
        request.session[KLIC_AKTIVNI_V] = _ted_unix()


def cookie_odpovida_uctu(request: Request, uzivatel: Uzivatel) -> bool:
    """Plati cookie jeste pro tenhle ucet? (verze hesla v cookie = v databazi)"""
    return request.session.get(KLIC_VERZE_HESLA) == uzivatel.verze_hesla


def odhlas(request: Request, uzivatel: Uzivatel | None = None) -> None:
    """Smaze session a - kdyz je predan ucet, jehoz platnou cookie to je -
    zvedne mu verzi hesla, aby prestala platit i zkopirovana cookie.
    Odhlasi to ucet na vsech zarizenich: appka o jednotlivych prihlasenich
    nevede zaznam, jen o verzi u uctu.

    Stara (uz neplatna) cookie verzi nezvedne - utocnik s opsanou cookie by
    jinak "odhlasenim" vyhodil majitele, ktery se mezitim prihlasil znovu."""
    if uzivatel is not None and cookie_odpovida_uctu(request, uzivatel):
        uzivatel.verze_hesla += 1
    request.session.clear()


def je_prihlasen(request: Request) -> bool:
    """Rychla kontrola pro middleware - jen jestli je v cookie nejake id.
    Jestli ucet opravdu existuje a je aktivni, overuje az `nacti_uzivatele`,
    ktery uz ma pristup k databazi."""
    return KLIC_UZIVATEL in request.session


def je_verejna(cesta: str) -> bool:
    return cesta.startswith(VEREJNE_CESTY)


def _verejny_puvod() -> str:
    """Puvod appky (schema+host+port) pro porovnani s hlavickou `Origin`."""
    rozebrane = urlsplit(nastaveni.verejna_url)
    return f"{rozebrane.scheme}://{rozebrane.netloc}".lower()


def je_cizi_pozadavek(request: Request) -> bool:
    """Zaklad CSRF ochrany appky - appka nema token, jen tohle.

    Prohlizec u kazdeho pozadavku posila hlavicku `Sec-Fetch-Site` sam, appka
    ji jen cte: "same-origin"/"none" je v poradku, "same-site" (sesterska
    subdomena) i "cross-site" je cizi puvod. Appka nic jineho nez
    "same-origin" nepotrebuje - vlastni formulare i `fetch` posilaji jen ten.

    Kdyz hlavicka chybi (starsi prohlizec bez Fetch Metadata, napr. Safari
    pred 16.4), porovna se aspon `Origin` proti `nastaveni.verejna_url`.
    Kdyz chybi obe hlavicky, pozadavek se pusti - takovy prohlizec by stejne
    nepripojil cookii `SameSite=Lax` a nastroje jako `curl` cookii nemaji
    vubec (stejne pravidlo pouziva `CrossOriginProtection` v Go 1.25).

    GET/HEAD se nekontroluje - jde jen o ochranu pred zmenou dat.
    """
    if request.method in ("GET", "HEAD"):
        return False
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None:
        return fetch_site not in ("same-origin", "none")
    origin = request.headers.get("origin")
    if origin is not None:
        return origin.lower() != _verejny_puvod()
    return False


def povolene_hosty() -> list[str]:
    """Domeny, pro ktere appka odpovi (`TrustedHostMiddleware`, B-036).

    Bez tohohle by appka odpovedela na jakoukoli hlavicku `Host` a slozila
    z ni adresy statickych souboru - dnes to chrani jen konfigurace Caddy
    (jeden blok pro jednu domenu v konfiguraci Caddy), tady se to hlida
    i v kodu appky. `localhost` a `127.0.0.1` zustavaji vzdy, at funguje
    lokalni vyvoj bez ohledu na VEREJNA_URL.
    """
    host = urlsplit(nastaveni.verejna_url).hostname or "localhost"
    return sorted({host, "localhost", "127.0.0.1"})


def nacti_uzivatele(request: Request, sezeni: Databaze) -> Uzivatel:
    """Aktualni prihlaseny uzivatel nacteny z databaze.

    Presmeruje na prihlaseni, kdyz ucet mezitim zmizel, byl deaktivovan,
    nebo si zmenil heslo (verze_hesla v cookie uz nesouhlasi - stara cookie
    tak prestane platit hned, ne az ji nekdo zneuzije).
    """
    uzivatel_id = request.session.get(KLIC_UZIVATEL)
    uzivatel = sezeni.get(Uzivatel, uzivatel_id) if uzivatel_id else None
    if (
        uzivatel is None
        or not uzivatel.aktivni
        or request.session.get(KLIC_VERZE_HESLA) != uzivatel.verze_hesla
    ):
        request.session.clear()
        raise Presmerovat("/prihlaseni", "Přihlas se znovu.")
    request.state.uzivatel = uzivatel
    sezeni.info["aktor"] = Aktor(uzivatel.id, uzivatel.jmeno)
    return uzivatel


def prihlaseny(uzivatel: Uzivatel = Depends(nacti_uzivatele)) -> Uzivatel:
    """Jako `nacti_uzivatele`, ale navic posle na /profil clovka, ktery se
    prihlasil docasnym heslem a jeste si nenastavil vlastni."""
    if uzivatel.musi_zmenit_heslo:
        raise Presmerovat("/profil", "Přihlásil(a) jsi se dočasným heslem — nastav si vlastní.")
    return uzivatel


def pristup_k_akcim(uzivatel: Uzivatel = Depends(prihlaseny)) -> Uzivatel:
    """Ucet role Podepisujici nevidi zadnou akci ani jeji data - smi jen na
    /moje-doklady a /profil. Vsechny routery s akcemi (a s tim
    spojenymi opravnenimi editor/spravce) na tuhle zavislost navazuji, takze
    ji zdedi automaticky - viz `zapis_jen_pro_editory` a `vyzaduj_spravce`."""
    if not uzivatel.ma_pristup_k_akcim:
        raise Presmerovat("/moje-doklady", "Tenhle účet slouží jen k podpisům dokladů.")
    return uzivatel


def zapis_jen_pro_editory(request: Request, uzivatel: Uzivatel = Depends(pristup_k_akcim)) -> None:
    """Revizor smi jen cist. Server to hlida tady - u kazde routy, ktera
    tuhle zavislost dostane pres `main.py` - takze i kdyby v sablone zbylo
    zapomenute tlacitko, zapis samotny neprojde. Pta se na `muze_zapisovat`
    (vyslovny seznam roli), ne na `je_revizor` - neznama role nezapise nic."""
    if not uzivatel.muze_zapisovat and request.method not in ("GET", "HEAD"):
        raise HTTPException(status_code=403, detail="Revizor má jen čtení, nic tu neuloží.")


def vyzaduj_spravce(uzivatel: Uzivatel = Depends(pristup_k_akcim)) -> Uzivatel:
    if not uzivatel.je_spravce:
        raise HTTPException(status_code=403, detail="Tahle stránka je jen pro správce.")
    return uzivatel
