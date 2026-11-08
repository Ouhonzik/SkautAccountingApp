"""Chybove stranky a upozorneni spravci na chybu 500.

Pravidla:
- bezny pozadavek dostane ceskou HTML stranku s odkazem zpet (`chyba.html`),
  pozadavek z prubezneho ukladani nebo s `Accept: application/json` (mapa)
  kratky JSON `{"detail": "..."}`;
- chyba 500 se zapise do logu s tracebackem (`zurnal.exception`) a spravcum
  odejde e-mail, nejvys jednou za 15 minut. E-mail nese JEN cas, cestu bez
  query stringu (token v adrese maskuje `auth.cesta_pro_log`) a typ vyjimky -
  nikdy text vyjimky, telo pozadavku ani jmena: text vyjimky muze obsahovat
  osobni udaje deti.
"""

from __future__ import annotations

import http
import logging
import threading
import time
from collections.abc import Mapping

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy import select
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import auth
from app.cas import ted
from app.db import Sezeni
from app.formaty import cas as cas_hodin
from app.models import ROLE_SPRAVCE, UCEL_CHYBA_APPKY, Uzivatel
from app.routes.spolecne import adresa_zpet, je_prubezne
from app.sablony import sablony
from app.services import posta

zurnal = logging.getLogger(__name__)

TEXT_404 = "Tohle v appce už není — možná to mezitím někdo smazal."
TEXT_422 = "Formulář nepřišel celý, obnov stránku a zkus to znovu."
TEXT_500 = (
    "Něco se pokazilo na naší straně. Zkus to znovu; když to nepůjde, "
    "napiš správci, kdy se to stalo."
)
# Pro kody bez vlastni vety (Starlette u nich dava anglickou frazi).
TEXT_OBECNY = "Požadavek se nepodařilo vyřídit."
TEXTY = {
    404: TEXT_404,
    405: "Tahle akce tady není povolená.",
    422: TEXT_422,
    500: TEXT_500,
}
NADPISY = {
    403: "Sem nemáš přístup",
    404: "Tohle tu není",
    409: "Teď to nejde",
    422: "Neúplný formulář",
    500: "Něco se pokazilo",
}

# Nejmene tolik sekund mezi dvema upozornenimi spravci na chybu 500.
INTERVAL_UPOZORNENI_S = 15 * 60
_ted = time.monotonic
_zamek = threading.Lock()
_posledni_upozorneni: float | None = None


def _text_chyby(stav: int, detail: object) -> str:
    """Veta pro uzivatele: vlastni `detail` routy (ceska veta) ma prednost,
    anglickou frazi Starlette (`Not Found`) nahradi nase veta podle kodu."""
    if isinstance(detail, str) and detail and detail != http.HTTPStatus(stav).phrase:
        return detail
    return TEXTY.get(stav, TEXT_OBECNY)


def _chce_json(request: Request) -> bool:
    """`fetch` (prubezne ukladani, mapa) cte odpoved skriptem, ne ocima. Adresy
    pod /mapa/ jsou jen pro skript (hledani, dlazdice, trasa), proto JSON dostanou
    i bez hlavicky Accept."""
    return (
        je_prubezne(request)
        or "application/json" in request.headers.get("accept", "")
        or request.url.path.startswith("/mapa/")
    )


def odpoved_chyby(
    request: Request,
    stav: int,
    text: str,
    headers: Mapping[str, str] | None = None,
    background: BackgroundTask | None = None,
) -> Response:
    """Odpoved na chybu: JSON pro `fetch`, jinak stranka `chyba.html`."""
    if _chce_json(request):
        return JSONResponse(
            {"detail": text}, status_code=stav, headers=headers, background=background
        )
    kontext = {
        "nadpis": NADPISY.get(stav, "Něco se nepovedlo"),
        "text": text,
        "cas": cas_hodin(ted()) if stav >= 500 else "",
        "zpet": adresa_zpet(request),
        "oznameni": [],
    }
    # Uzivatel z request.state po chybe uz nemusi jit cist (objekt odpojeny od
    # session) - stranka pak vznikne bez menu, ale vznikne.
    for uzivatel in (getattr(request.state, "uzivatel", None), None):
        try:
            odpoved = sablony.TemplateResponse(
                request,
                "chyba.html",
                {**kontext, "uzivatel": uzivatel},
                status_code=stav,
                headers=headers,
            )
        except Exception:  # noqa: BLE001 - chybova stranka nesmi sama shodit obsluhu chyby
            zurnal.exception("Stránku s chybou %s se nepodařilo vykreslit", stav)
            continue
        odpoved.background = background
        return odpoved
    return PlainTextResponse(text, status_code=stav, headers=headers, background=background)


async def http_chyba(request: Request, chyba: Exception) -> Response:
    """`HTTPException` z rout i chyby Starlette (404 na neznamou adresu, 405)."""
    assert isinstance(chyba, StarletteHTTPException)  # jen na ni je handler zaregistrovany
    # Chybejici staticky soubor je jen 404 pro prohlizec, ne stranka pro cloveka.
    if request.url.path.startswith("/static/"):
        return PlainTextResponse(
            "Soubor neexistuje.", status_code=chyba.status_code, headers=chyba.headers
        )
    return odpoved_chyby(
        request, chyba.status_code, _text_chyby(chyba.status_code, chyba.detail), chyba.headers
    )


def _smi_upozornit() -> bool:
    """True nejvys jednou za `INTERVAL_UPOZORNENI_S`; cas se zapisuje hned, aby
    se i selhane odeslani neopakovalo pri kazdem dalsim padu."""
    global _posledni_upozorneni
    with _zamek:
        ted_s = _ted()
        if (
            _posledni_upozorneni is not None
            and ted_s - _posledni_upozorneni < INTERVAL_UPOZORNENI_S
        ):
            return False
        _posledni_upozorneni = ted_s
        return True


def upozorni_spravce(cesta: str, typ: str) -> None:
    """Posle e-mail kazdemu aktivnimu spravci s adresou. Nikdy nevyhazuje vyjimku:
    pad uvnitr obsluhy chyby by zakryl puvodni chybu.

    Selhani odeslani nema komu ukazat (bezi po odeslani odpovedi, bez uzivatele),
    zustane proto v logu serveru a v `OdeslanyEmail` (stav `selhalo`)."""
    try:
        with Sezeni() as sezeni:
            spravci = sezeni.scalars(
                select(Uzivatel).where(
                    Uzivatel.role == ROLE_SPRAVCE,
                    Uzivatel.aktivni.is_(True),
                    Uzivatel.email.is_not(None),
                    Uzivatel.email != "",
                )
            ).all()
            text = posta.vykresli("chyba_appky.txt", cas=cas_hodin(ted()), cesta=cesta, typ=typ)
            for spravce in spravci:
                zprava = posta.Zprava(
                    komu=spravce.email or "",
                    predmet="Účtování akcí: v aplikaci nastala chyba",
                    text=text,
                )
                # Bez uzivatel_id: limit na ucet (3 za hodinu) by pri vice chybach
                # zastavil upozorneni, ktere je potreba doručit; plati jen celkovy.
                zaznam = posta.posli(sezeni, zprava, UCEL_CHYBA_APPKY)
                if zaznam.stav == "selhalo":
                    zurnal.error("Upozornění správci na chybu 500 se neodeslalo")
            sezeni.commit()
    except Exception:  # noqa: BLE001 - pad pri upozorneni by zakryl puvodni chybu 500
        zurnal.exception("Upozornění správci na chybu 500 selhalo")


async def neosetrena_chyba(request: Request, chyba: Exception) -> Response:
    """Neosetrena vyjimka -> stranka 500 a (nejvys jednou za 15 minut) e-mail
    spravcum. Bezi mimo SessionMiddleware, proto nesahame na `request.session`."""
    cesta = auth.cesta_pro_log(request.url.path)
    zurnal.error(
        "Chyba 500: %s %s (uživatel %s)",
        request.method,
        cesta,
        (request.scope.get("session") or {}).get(auth.KLIC_UZIVATEL, "-"),
        exc_info=chyba,
    )
    ukol = (
        BackgroundTask(upozorni_spravce, cesta, type(chyba).__name__) if _smi_upozornit() else None
    )
    return odpoved_chyby(request, 500, TEXT_500, background=ukol)
