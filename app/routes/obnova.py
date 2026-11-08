"""Obnova akce ze zalohy. Cely router je jen pro spravce - viz main.py."""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import nastaveni
from app.formaty import cas, datum
from app.models import CINNOST_OBNOVA, Uzivatel
from app.routes.spolecne import Databaze
from app.sablony import oznam, strana, zpet
from app.services import log_cinnosti
from app.services.obnova_zalohy import (
    ChybaObnovy,
    VysledekObnovy,
    obnov,
    otisk_zip,
    popis_obnovy,
    predchozi_obnova,
)
from app.services.soubory import ChybaObsahu, precti_s_limitem, smaz_soubory

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/obnova", response_class=HTMLResponse)
async def formular(request: Request):
    return strana(request, "obnova.html")


def _zahod_zapsane(vysledek: VysledekObnovy) -> None:
    """`obnov()` zapsal soubory skenu na disk jeste pred ulozenim do databaze -
    kdyz ulozeni selze, bez uklidu by na disku zustaly osirele."""
    smaz_soubory(vysledek.zapsane_soubory, nastaveni.slozka_uploadu)


def _obnov_do_databaze(
    sezeni: Session, obsah: bytes
) -> tuple[VysledekObnovy | None, str, Uzivatel | None]:
    """Provede obnovu a ulozi ji. Vraci (vysledek, hlaska chyby, aktér): pri
    chybe je vysledek None a hlaska vyplnena, jinak je hlaska prazdna.

    Aktera pro dobu obnovy schovame - kdyby zustal, kazdy jednotlivy sken,
    slozka nebo ucastnik by si vyrobil vlastni radek v logu cinnosti
    (app/services/log_cinnosti/zapisovani.py). Chceme jeden souhrnny radek, ne desitky
    drobnych. Aktera vrati kontext sam (i pri chybe); hlasku uzivateli ale
    ukazujeme az PO nem, aby bylo sezeni uz v puvodnim stavu."""
    vysledek: VysledekObnovy | None = None
    hlaska_chyby = ""
    with log_cinnosti.bez_automatickeho_zapisu(sezeni) as aktor:
        try:
            vysledek = obnov(sezeni, obsah, nastaveni.slozka_uploadu)
            sezeni.commit()
        except ChybaObnovy as chyba:
            sezeni.rollback()
            hlaska_chyby = str(chyba)
        except Exception:
            sezeni.rollback()
            if vysledek is None:
                log.exception("Obnova ze zálohy selhala neočekávaně")
                hlaska_chyby = (
                    "Obnova se nepovedla kvůli neočekávané chybě. Nic se neuložilo, zkus to znovu."
                )
            else:
                _zahod_zapsane(vysledek)
                log.exception("Obnova ze zálohy: uložení do databáze selhalo")
                hlaska_chyby = "Obnova se nepovedla při ukládání do databáze."
        if hlaska_chyby:
            vysledek = None
    return vysledek, hlaska_chyby, aktor


def _hlaska_o_drivejsi_obnove(drivejsi) -> str:
    return (
        f"Tenhle soubor už byl obnoven ({datum(drivejsi.do)} v {cas(drivejsi.do)}, "
        f"akce „{drivejsi.akce_nazev or '?'}“). Obnovení znovu založí další kopii "
        "akce. Chceš-li ji opravdu, zaškrtni „Obnovit i přesto“ a nahraj soubor znovu."
    )


def _hlaska_po_obnove(vysledek: VysledekObnovy) -> str:
    hlaska = f"Akce „{vysledek.akce.nazev}“ obnovena ze zálohy."
    if vysledek.chybejici_soubory:
        hlaska += (
            f" Pozor: {len(vysledek.chybejici_soubory)} skenů v záloze nemělo "
            "přiložený soubor, zůstaly bez fotky účtenky."
        )
    return hlaska


@router.post("/obnova")
async def obnovit(
    request: Request,
    sezeni: Databaze,
    soubor: UploadFile = File(...),
    znovu: str = Form(""),
):
    nastaveni.zaloz_slozky()
    try:
        obsah = await precti_s_limitem(soubor, nastaveni.max_velikost_zalohy_mb)
    except ChybaObsahu as chyba:
        oznam(request, f"Soubor {chyba}.", "chyba")
        return zpet("/obnova")

    # Tentyz ZIP podruhe (dvojklik, znovu nahrany soubor) by vyrobil druhou kopii
    # akce se vsemi skeny. Bez zaskrtnuteho "Obnovit i presto" se proto neobnovuje.
    otisk = otisk_zip(obsah)
    if not znovu and (drivejsi := predchozi_obnova(sezeni, otisk)) is not None:
        oznam(request, _hlaska_o_drivejsi_obnove(drivejsi), "pozor")
        return zpet("/obnova")

    vysledek, hlaska_chyby, aktor = _obnov_do_databaze(sezeni, obsah)
    if vysledek is None:
        oznam(request, hlaska_chyby, "chyba")
        return zpet("/obnova")

    if aktor is not None:
        log_cinnosti.zapis(
            sezeni,
            CINNOST_OBNOVA,
            uzivatel_id=aktor.id,
            uzivatel_jmeno=aktor.jmeno,
            popis=popis_obnovy(vysledek.akce.nazev, otisk),
            akce_id=vysledek.akce.id,
            akce_nazev=vysledek.akce.nazev,
        )
        sezeni.commit()

    oznam(request, _hlaska_po_obnove(vysledek))
    return zpet(f"/akce/{vysledek.akce.id}")
