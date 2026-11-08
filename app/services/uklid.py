"""Mazani starych udaju.

Duvod je bezpecnost a GDPR (omezeni uchovani), ne misto na disku: co na serveru
neni, nemuze z nej uniknout - ani z kopie disku ci zalohy. Lhuty:

- tokeny z odkazu v e-mailech: 30 dni po vyprseni (`platnost_do`),
- zaznamy odeslanych e-mailu: 12 mesicu,
- IP v logu cinnosti: po 12 mesicich se vynuluje, zaznam zustane,
- `data/posta/*.eml` (e-maily ulozene bez SMTP, nesou odkaz s tokenem): 7 dni.

Kazda funkce bere `cas` jako parametr (testy nepodvrhuji hodiny). Funkce
s databazi jen provedou prikaz, `commit` je na volajicim (`uklid_vse`).
Do logu jdou jen pocty, nikdy hodnoty.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from sqlalchemy import CursorResult, Executable, delete, select, update
from sqlalchemy.orm import Session

from app.cas import ted
from app.config import nastaveni
from app.models import OdeslanyEmail, Sken, TokenUzivatele, VozidloFoto, ZaznamCinnosti

log = logging.getLogger(__name__)

TOKEN_PO_VYPRSENI = dt.timedelta(days=30)
# 12 mesicu = 365 dni; presnost na den tu nehraje roli.
EMAIL_A_IP = dt.timedelta(days=365)
EML = dt.timedelta(days=7)
# Soubor mladsi nez den se za osireli nepovazuje - mozna se prave nahrava
# a jeho radek v databazi jeste neni zapsany.
OSIRELY_NEJMENE_STARY = dt.timedelta(days=1)

# Jak casto bezi uklid po startu appky (sekundy).
INTERVAL_S = 24 * 3600


def _pocet_radku(sezeni: Session, prikaz: Executable) -> int:
    """Provede DELETE/UPDATE a vrati, kolik radku se zmenilo."""
    return cast("CursorResult[Any]", sezeni.execute(prikaz)).rowcount


def smaz_stare_tokeny(sezeni: Session, cas: dt.datetime) -> int:
    return _pocet_radku(
        sezeni,
        delete(TokenUzivatele).where(TokenUzivatele.platnost_do < cas - TOKEN_PO_VYPRSENI),
    )


def smaz_stare_emaily(sezeni: Session, cas: dt.datetime) -> int:
    return _pocet_radku(
        sezeni, delete(OdeslanyEmail).where(OdeslanyEmail.vytvoreno < cas - EMAIL_A_IP)
    )


def vynuluj_stare_ip(sezeni: Session, cas: dt.datetime) -> int:
    # Primy prikaz, ne zmena objektu: log cinnosti by jinak zapsal dalsi radky.
    return _pocet_radku(
        sezeni,
        update(ZaznamCinnosti)
        .where(ZaznamCinnosti.do < cas - EMAIL_A_IP, ZaznamCinnosti.ip.is_not(None))
        .values(ip=None),
    )


def _stari_souboru_od(cesta: Path, cas: dt.datetime) -> dt.timedelta:
    zmeneno = dt.datetime.fromtimestamp(cesta.stat().st_mtime, dt.UTC).replace(tzinfo=None)
    return cas - zmeneno


def smaz_stare_eml(cas: dt.datetime) -> int:
    smazano = 0
    slozka = nastaveni.slozka_posty
    if not slozka.is_dir():
        return 0
    for cesta in slozka.glob("*.eml"):
        if cesta.is_file() and _stari_souboru_od(cesta, cas) > EML:
            cesta.unlink(missing_ok=True)
            smazano += 1
    return smazano


def najdi_osirele_soubory(sezeni: Session, cas: dt.datetime) -> list[tuple[Path, int]]:
    """Soubory v `uploads/`, na ktere neodkazuje zadny sken ani fotka vozidla
    (zustaly po akcich smazanych drive, nez se mazaly i soubory). Jen vypis
    (cesta, velikost v bajtech) - nic nemaze, o smazani rozhoduje Honza."""
    pouzite = set(sezeni.scalars(select(Sken.soubor))) | set(
        sezeni.scalars(select(VozidloFoto.soubor))
    )
    slozka = nastaveni.slozka_uploadu
    if not slozka.is_dir():
        return []
    nalezene = [
        (cesta, cesta.stat().st_size)
        for cesta in sorted(slozka.iterdir())
        if cesta.is_file()
        and cesta.name not in pouzite
        and _stari_souboru_od(cesta, cas) > OSIRELY_NEJMENE_STARY
    ]
    return nalezene


def uklid_vse(sezeni: Session, cas: dt.datetime | None = None) -> dict[str, int]:
    """Provede cely uklid a vrati pocty (do logu jdou jen ony)."""
    cas = cas or ted()
    pocty = {
        "tokeny": smaz_stare_tokeny(sezeni, cas),
        "emaily": smaz_stare_emaily(sezeni, cas),
        "ip": vynuluj_stare_ip(sezeni, cas),
    }
    sezeni.commit()
    pocty["eml"] = smaz_stare_eml(cas)
    log.info("Uklid starych udaju: %s", pocty)
    return pocty


def _jeden_beh(otevri_sezeni: Callable[[], Session]) -> None:
    with otevri_sezeni() as sezeni:
        uklid_vse(sezeni)


async def denni_smycka(otevri_sezeni: Callable[[], Session]) -> None:
    """Uklid hned po startu a pak po `INTERVAL_S`. Chyba uklidu se jen zapise
    do zurnalu - appku nesmi shodit, dalsi beh to zkusi znovu."""
    while True:
        try:
            await asyncio.to_thread(_jeden_beh, otevri_sezeni)
        except Exception:
            log.exception("Uklid starych udaju selhal")
        await asyncio.sleep(INTERVAL_S)
