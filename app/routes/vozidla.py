"""Ciselnik aut, kterymi se jezdi na akce.

Stejny princip jako kategorie a lide: auto se zalozi jednou, pak uz se jen
vybira na cestovnim prikazu. Navic si u sebe drzi fotky maleho technickeho
prukazu (knizecka ma dve strany) - jsou doklad ke spotrebe a SPZ, ktere jdou
na papir cestaku.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth
from app.chyby_poli import StavFormulare, stav_s_chybami
from app.config import nastaveni
from app.db import zamkni_zapis
from app.formaty import mnozne
from app.formulare import VozidloForm, VozidloRadekForm, formular_s_chybami, mez_delky, over_delky
from app.models import Vozidlo, VozidloFoto
from app.routes.spolecne import (
    Databaze,
    hlaska_necitelnych,
    nacti_formular,
    prazdne_na_none,
    prubezne_ulozeni,
    radky_z_formulare,
)
from app.sablony import oznam, strana, strana_s_chybou, zpet
from app.services import cestaky, nahledy
from app.services import soubory as soubory_sluzba
from app.services.ciselniky import (
    dalsi_poradi,
    pouziti_vozidel,
    prejmenuj_bez_kolizi,
    proc_nejde_smazat,
    uz_existuje,
    vsechna_vozidla,
)
from app.services.soubory import POVOLENE_PRIPONY

router = APIRouter()
log = logging.getLogger(__name__)

# Ciselnik patri vsem akcim, takze ho upravuje a maze jen spravce.
# Na jednotlivych routach, ne na celem routeru - cist ho musi kazdy, formulare
# akci z nej vybiraji.
JEN_SPRAVCE = [Depends(auth.vyzaduj_spravce)]


def _strana_vozidel(request: Request, sezeni: Session, stav: StavFormulare | None = None):
    """Prehled vozidel; se `stav` je to znovu vykresleny formular pridani s chybou u pole."""
    kontext = {"vozidla": vsechna_vozidla(sezeni), "pouziti": pouziti_vozidel(sezeni)}
    if stav is not None:
        return strana_s_chybou(request, "vozidla.html", stav, **kontext)
    return strana(request, "vozidla.html", **kontext)


@router.get("/vozidla", response_class=HTMLResponse)
async def prehled(request: Request, sezeni: Databaze):
    return _strana_vozidel(request, sezeni)


@router.post("/vozidla/pridat")
async def pridat(
    request: Request,
    sezeni: Databaze,
    data: Annotated[VozidloForm | StavFormulare, Depends(formular_s_chybami(VozidloForm))],
):
    if isinstance(data, StavFormulare):
        return _strana_vozidel(request, sezeni, data)
    nazev = data.nazev
    if not nazev:
        stav = stav_s_chybami(request, nazev="Vozidlo musí mít název.")
        return _strana_vozidel(request, sezeni, stav)
    if uz_existuje(nazev, sezeni.scalars(select(Vozidlo.nazev))):
        stav = stav_s_chybami(request, nazev=f"„{nazev}“ už v seznamu je.")
        return _strana_vozidel(request, sezeni, stav)

    poradi = dalsi_poradi(sezeni.scalars(select(Vozidlo.poradi)), zacatek=1)
    sezeni.add(
        Vozidlo(
            nazev=nazev,
            spz=data.spz,
            spotreba=data.spotreba,
            poradi=poradi,
        )
    )
    sezeni.commit()
    oznam(request, f"„{nazev}“ přidáno do seznamu.")
    return zpet("/vozidla")


@router.post("/vozidla/ulozit", dependencies=JEN_SPRAVCE)
async def ulozit(request: Request, sezeni: Databaze):
    formular = await nacti_formular(request)
    over_delky(formular, {"nazev": Vozidlo.nazev, "spz": Vozidlo.spz})
    radky = radky_z_formulare(formular, ("nazev", "spz", "spotreba"))
    necitelnych = 0
    # Zmena spotreby se promita do cestaku: "cti -> pocitej -> zapis" pod zamkem.
    if any(pole.startswith("spotreba_") for pole in formular):
        zamkni_zapis(sezeni)
    vozidla = vsechna_vozidla(sezeni)
    odmitnute = prejmenuj_bez_kolizi(
        vozidla, {v.id: radky.get(v.id, {}).get("nazev", "") for v in vozidla}, "nazev"
    )
    for vozidlo in vozidla:
        pole = radky.get(vozidlo.id, {})
        if "spz" in pole:
            vozidlo.spz = prazdne_na_none(pole["spz"])
        if "spotreba" in pole:
            zadano = VozidloRadekForm.model_validate({"spotreba": pole["spotreba"]})
            necitelnych += zadano.necitelnych()
            if "spotreba" in zadano.zmeny() and zadano.spotreba != vozidlo.spotreba:
                vozidlo.spotreba = zadano.spotreba
                # Cestaky otevrenych akci dostanou novou spotrebu, uzavrene ne.
                cestaky.promit_spotrebu_vozidla(sezeni, vozidlo)
    sezeni.commit()
    if (hotovo := prubezne_ulozeni(request, necitelnych)) is not None:
        return hotovo
    for nazev in odmitnute:
        oznam(request, f"„{nazev}“ už v seznamu je, přejmenování jsem neuložil.", "chyba")
    if not odmitnute:
        oznam(request, "Uloženo.")
    if necitelnych:
        oznam(request, hlaska_necitelnych(necitelnych), "chyba")
    return zpet("/vozidla")


def _je_id(text: str) -> bool:
    """Jen ASCII cislice: "²" projde `isdigit()`, ale `int()` na ni spadne (chyba 500)."""
    return text.isascii() and text.isdigit()


@router.post("/vozidla/poradi", dependencies=JEN_SPRAVCE)
async def zmenit_poradi(
    request: Request,
    sezeni: Databaze,
    poradi: str = Form(""),
    nahoru: str = Form(""),
):
    """Nove poradi aut po pretazeni za uchyt.

    JavaScript posila id vsech aut v novem poradi ("3,1,2"). Bez nej je uchyt
    obycejne tlacitko a umi auto posunout jen o jedno misto nahoru (`nahoru`).
    Poradi se vzdycky precisluje od 1, aby po case nevznikaly diry a shody.
    """
    vozidla = vsechna_vozidla(sezeni)
    podle_id = {v.id: v for v in vozidla}

    if poradi:
        cisla = dict.fromkeys(int(c) for c in poradi.split(",") if _je_id(c.strip()))
        nove = [podle_id[c] for c in cisla if c in podle_id]
        # Auto, ktere mezitim pribylo v jinem okne, v seznamu z prohlizece
        # chybi - zustane na konci, misto aby se ztratilo z poradi.
        nove += [v for v in vozidla if v not in nove]
    else:
        nove = list(vozidla)
        if _je_id(nahoru) and int(nahoru) in podle_id:
            kde = nove.index(podle_id[int(nahoru)])
            if kde > 0:
                nove[kde - 1], nove[kde] = nove[kde], nove[kde - 1]

    for cislo, vozidlo in enumerate(nove, start=1):
        vozidlo.poradi = cislo
    sezeni.commit()
    if (hotovo := prubezne_ulozeni(request)) is not None:
        return hotovo
    return zpet("/vozidla")


@router.post("/vozidla/{vozidlo_id}/smazat", dependencies=JEN_SPRAVCE)
async def smazat(request: Request, vozidlo_id: int, sezeni: Databaze):
    vozidlo = sezeni.get(Vozidlo, vozidlo_id)
    if vozidlo is None:
        return zpet("/vozidla")

    if duvod := proc_nejde_smazat(sezeni, vozidlo):
        oznam(request, duvod, "chyba")
        return zpet("/vozidla")

    nazev, soubory = vozidlo.nazev, [f.soubor for f in vozidlo.fotky]
    sezeni.delete(vozidlo)
    sezeni.commit()
    for soubor in soubory:
        _zahod_soubor(soubor)
    oznam(request, f"„{nazev}“ smazáno ze seznamu.")
    return zpet("/vozidla")


# --- Fotky maleho technickeho prukazu --------------------------------------


@router.post("/vozidla/{vozidlo_id}/foto")
async def nahrat_foto(
    request: Request,
    vozidlo_id: int,
    sezeni: Databaze,
    soubory: list[UploadFile] = File(...),
):
    """Prida jednu nebo vic fotek. Nic nenahrazuje - druha strana prukazu se
    prida k prvni a nepovedena fotka se smaze krizkem zvlast."""
    vozidlo = sezeni.get(Vozidlo, vozidlo_id)
    if vozidlo is None:
        return zpet("/vozidla")

    nastaveni.zaloz_slozky()

    if len(soubory) > nastaveni.max_souboru_na_nahrani:
        oznam(
            request,
            f"Najednou jde nahrát nejvýš {nastaveni.max_souboru_na_nahrani} souborů.",
            "chyba",
        )
        return zpet("/vozidla")

    pridano, odmitnuto, poskozeno, zapsane = 0, [], [], []
    nejvyssi = max((f.poradi for f in vozidlo.fotky), default=0)
    for nahrany in soubory:
        if not nahrany.filename:
            continue
        # Stejne jako u skenu: prilis dlouhy nazev se odmita, v hlasce zkraceny.
        if len(nahrany.filename) > mez_delky(VozidloFoto.puvodni_nazev):
            poskozeno.append(
                f"{soubory_sluzba.zkrat_pro_hlasku(nahrany.filename)} "
                f"(název souboru má víc než {mez_delky(VozidloFoto.puvodni_nazev)} znaků)"
            )
            continue
        pripona = Path(nahrany.filename).suffix.lower()
        if pripona not in POVOLENE_PRIPONY:
            odmitnuto.append(nahrany.filename)
            continue

        try:
            # Nazev na disku je nahodny ze stejneho duvodu jako u skenu - id
            # smazane fotky dostane driv nebo pozdeji jina a v prohlizeci by
            # pod nim svitil cizi technicak. Ulozeni i overeni obsahu
            # dela stejna funkce jako u skenu.
            nazev_na_disku, _ = await soubory_sluzba.uloz_nahrany(
                nahrany, nastaveni.slozka_uploadu, pripona
            )
        except soubory_sluzba.ChybaObsahu as chyba:
            poskozeno.append(f"{nahrany.filename} ({chyba})")
            continue

        zapsane.append(nazev_na_disku)
        nejvyssi += 1
        vozidlo.fotky.append(
            VozidloFoto(soubor=nazev_na_disku, puvodni_nazev=nahrany.filename, poradi=nejvyssi)
        )
        pridano += 1

    try:
        sezeni.commit()
    except Exception:
        sezeni.rollback()
        soubory_sluzba.smaz_soubory(zapsane, nastaveni.slozka_uploadu)
        log.exception("Nahrání fotky technického průkazu selhalo (vozidlo %s)", vozidlo_id)
        oznam(request, "Nahrání se nepovedlo kvůli neočekávané chybě. Nic se neuložilo.", "chyba")
        return zpet("/vozidla")

    if odmitnuto:
        oznam(request, f"Nepodporovaný formát: {', '.join(odmitnuto)}", "chyba")
    if poskozeno:
        oznam(request, "Nepřijato: " + ", ".join(poskozeno), "chyba")
    if pridano == 1:
        oznam(request, f"Fotka technického průkazu k „{vozidlo.nazev}“ nahrána.")
    elif pridano:
        fotek = mnozne(pridano, "fotka", "fotky", "fotek")
        oznam(request, f"K „{vozidlo.nazev}“ nahráno {fotek} technického průkazu.")
    return zpet("/vozidla")


@router.post("/vozidla/foto/{foto_id}/smazat", dependencies=JEN_SPRAVCE)
async def smazat_foto(request: Request, foto_id: int, sezeni: Databaze):
    foto = sezeni.get(VozidloFoto, foto_id)
    if foto is None:
        return zpet("/vozidla")
    soubor = foto.soubor
    sezeni.delete(foto)
    sezeni.commit()
    _zahod_soubor(soubor)
    oznam(request, "Fotka technického průkazu smazána.")
    return zpet("/vozidla")


@router.get("/vozidla/foto/{foto_id}")
async def puvodni_foto(foto_id: int, sezeni: Databaze):
    cesta, foto = _cesta_k_fotu(sezeni, foto_id)
    if cesta is None or foto is None:
        return Response(status_code=404)
    # Technicak nese jmeno, adresu a datum narozeni majitele - nesmi zustat
    # v cache prohlizece (B-039, rozhodnuti R20).
    return FileResponse(cesta, filename=foto.puvodni_nazev, headers={"Cache-Control": "no-store"})


@router.get("/vozidla/foto/{foto_id}/nahled")
def nahled(foto_id: int, sezeni: Databaze, v: str = "", velky: bool = False):
    """Nahled technickeho prukazu jako JPEG - prohlizec neumi PDF ani HEIC.
    `velky=1` je verze pro prohlizeni pres celou obrazovku."""
    cesta, foto = _cesta_k_fotu(sezeni, foto_id)
    if cesta is None or foto is None:
        return Response(status_code=404)

    # Fotka technickeho prukazu nema stranky - vzdy jde o prvni (a jedinou).
    data = nahledy.dej_nahled(cesta, pocet_stranek=1, velky=velky)
    if data is None:
        return Response(status_code=415)

    # Nahledy technickych prukazu jsou jen na strance Vozidla - vykon tam
    # nevadi, rozhodnuto R20: na rozdil od uctenek rovnou `no-store`.
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


def _cesta_k_fotu(sezeni, foto_id: int) -> tuple[Path | None, VozidloFoto | None]:
    foto = sezeni.get(VozidloFoto, foto_id)
    if foto is None:
        return None, None
    cesta = nastaveni.slozka_uploadu / foto.soubor
    return (cesta if cesta.is_file() else None), foto


def _zahod_soubor(nazev_souboru: str) -> None:
    """Smaze soubor i jeho nahledy. Vola se az po commitu, stejne jako u skenu."""
    (nastaveni.slozka_uploadu / nazev_souboru).unlink(missing_ok=True)
    nahledy.zapomen(nazev_souboru)
