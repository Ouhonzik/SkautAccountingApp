"""Vystupy: Excel pro hospodare, ZIP se slozkami a zaloha akce."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.config import nastaveni
from app.models import CINNOST_STAZENI, Akce
from app.routes.spolecne import Databaze, nacti_akci
from app.sablony import oznam
from app.services import (
    excel,
    excel_obdarovani,
    excel_pokladni_kniha,
    excel_ucastnici,
    log_cinnosti,
    soubory,
    zaloha,
    zip_export,
)

router = APIRouter()

TYP_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _ke_stazeni(data: bytes, nazev: str, typ: str) -> Response:
    """Odpoved, kterou prohlizec ulozi jako soubor.

    Nazev souboru ma diakritiku, proto filename* podle RFC 5987 - jinak by ho
    prohlizec zkomolil. Excel, ZIP i zaloha nesou udaje ucastniku - nesmi
    zustat v cache prohlizece (B-039, rozhodnuti R20).
    """
    return Response(
        content=data,
        media_type=typ,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(nazev)}",
            "Cache-Control": "private, no-store",
        },
    )


def _archiv_ke_stazeni[T](sestav: Callable[[Path], T], nazev: str) -> tuple[FileResponse, T]:
    """ZIP se sklada do souboru v `data/tmp/`, ne do pameti.

    Soubor se smaze po odeslani; kdyz skladani spadne, hned. Co zbude po padu
    procesu, uklidi start appky (`soubory.vycisti_tmp`).
    """
    cesta = soubory.docasny_soubor()
    try:
        vysledek = sestav(cesta)
    except BaseException:
        cesta.unlink(missing_ok=True)
        raise
    odpoved = FileResponse(
        cesta,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(nazev)}",
            "Cache-Control": "private, no-store",
        },
        background=BackgroundTask(cesta.unlink, missing_ok=True),
    )
    return odpoved, vysledek


def _zapis_stazeni(sezeni: Session, akce: Akce, co: str) -> None:
    """Zapise do logu cinnosti, kdo si stahl soubor s udaji deti.

    Seznam ucastniku, seznam obdarovanych i zaloha nesou jmena, adresy
    a data narozeni - smi je stahnout kazda role s pristupem k akcim, ale
    kazde stazeni musi jit dohledat. Zaznam nese jen co, kdo a z ktere akce,
    nikdy zadnou hodnotu. Zapisuje server, ne uzivatel, proto jde i u revizora.
    """
    aktor = sezeni.info.get("aktor")
    log_cinnosti.zapis(
        sezeni,
        CINNOST_STAZENI,
        uzivatel_id=aktor.id if aktor else None,
        uzivatel_jmeno=aktor.jmeno if aktor else "neznámé jméno",
        popis=f"stáhl(a) {co}",
        akce_id=akce.id,
        akce_nazev=akce.nazev,
    )
    sezeni.commit()


@router.get("/akce/{akce_id}/export/excel")
def excel_pro_hospodare(akce_id: int, sezeni: Databaze):
    akce = nacti_akci(sezeni, akce_id)
    return _ke_stazeni(excel.do_bajtu(akce), excel.nazev_souboru(akce), TYP_XLSX)


@router.get("/akce/{akce_id}/export/pokladni-kniha")
def pokladni_kniha_xlsx(akce_id: int, sezeni: Databaze):
    akce = nacti_akci(sezeni, akce_id)
    return _ke_stazeni(
        excel_pokladni_kniha.do_bajtu(akce),
        excel_pokladni_kniha.nazev_souboru(akce),
        TYP_XLSX,
    )


@router.get("/akce/{akce_id}/export/ucastnici")
def seznam_ucastniku_xlsx(akce_id: int, sezeni: Databaze):
    akce = nacti_akci(sezeni, akce_id)
    _zapis_stazeni(sezeni, akce, "Seznam účastníků (Excel)")
    return _ke_stazeni(
        excel_ucastnici.do_bajtu(akce),
        excel_ucastnici.nazev_souboru(akce),
        TYP_XLSX,
    )


@router.get("/akce/{akce_id}/export/obdarovani")
def seznam_obdarovanych_xlsx(akce_id: int, sezeni: Databaze):
    akce = nacti_akci(sezeni, akce_id)
    _zapis_stazeni(sezeni, akce, "Seznam obdarovaných (Excel)")
    return _ke_stazeni(
        excel_obdarovani.do_bajtu(akce),
        excel_obdarovani.nazev_souboru(akce),
        TYP_XLSX,
    )


@router.get("/akce/{akce_id}/export/zip")
def zip_se_slozkami(request: Request, akce_id: int, sezeni: Databaze):
    akce = nacti_akci(sezeni, akce_id)
    odpoved, problemy = _archiv_ke_stazeni(
        lambda cil: zip_export.sestav_zip(akce, nastaveni.slozka_uploadu, cil),
        zip_export.nazev_souboru(akce),
    )
    if problemy:
        oznam(request, f"Pozor: {len(problemy)} skenů se nenašlo na disku.", "chyba")
    return odpoved


@router.get("/akce/{akce_id}/zaloha")
def zaloha_akce(akce_id: int, sezeni: Databaze):
    akce = nacti_akci(sezeni, akce_id)
    _zapis_stazeni(sezeni, akce, "zálohu akce (ZIP s plnými údaji)")
    odpoved, _ = _archiv_ke_stazeni(
        lambda cil: zaloha.sestav_zalohu(akce, nastaveni.slozka_uploadu, cil),
        zaloha.nazev_souboru(akce),
    )
    return odpoved
