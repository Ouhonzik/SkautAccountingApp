"""Ciselnik kategorii. Edituje se tady, ne v kodu."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth
from app.chyby_poli import StavFormulare, stav_s_chybami
from app.formulare import KategorieForm, formular_s_chybami, over_delky, poradi_z_textu
from app.models import Kategorie, Slozka
from app.routes.spolecne import Databaze, nacti_formular, prazdne_na_none, text_z_formulare
from app.sablony import oznam, strana, strana_s_chybou, zpet
from app.services.ciselniky import (
    dalsi_poradi,
    pouziti_kategorii,
    prejmenuj_bez_kolizi,
    proc_nazev_je_vyhrazen,
    proc_nazev_koliduje,
    proc_nejde_prejmenovat,
    proc_nejde_smazat,
    proc_nejde_zmenit_klic,
    uz_existuje,
    vsechny_kategorie,
)

router = APIRouter()

# Ciselnik patri vsem akcim, takze ho upravuje a maze jen spravce.
# Na jednotlivych routach, ne na celem routeru - cist ho musi kazdy, formulare
# akci z nej vybiraji.
JEN_SPRAVCE = [Depends(auth.vyzaduj_spravce)]


def _strana_kategorii(request: Request, sezeni: Session, stav: StavFormulare | None = None):
    """Prehled kategorii; se `stav` je to znovu vykresleny formular pridani s chybou u pole."""
    kontext = {"kategorie": vsechny_kategorie(sezeni), "pouziti": pouziti_kategorii(sezeni)}
    if stav is not None:
        return strana_s_chybou(request, "kategorie.html", stav, **kontext)
    return strana(request, "kategorie.html", **kontext)


@router.get("/kategorie", response_class=HTMLResponse)
async def prehled(request: Request, sezeni: Databaze):
    return _strana_kategorii(request, sezeni)


@router.post("/kategorie/pridat", dependencies=JEN_SPRAVCE)
async def pridat(
    request: Request,
    sezeni: Databaze,
    data: Annotated[KategorieForm | StavFormulare, Depends(formular_s_chybami(KategorieForm))],
):
    if isinstance(data, StavFormulare):
        return _strana_kategorii(request, sezeni, data)
    nazev = data.nazev
    if not nazev:
        stav = stav_s_chybami(request, nazev="Kategorie musí mít název.")
        return _strana_kategorii(request, sezeni, stav)
    kategorie_vse = vsechny_kategorie(sezeni)
    # Hlaska u pole rika i to, co delat, kdyz nazev patri pevne kategorii.
    if hlaska := proc_nazev_koliduje(nazev, data.klic, kategorie_vse):
        return _strana_kategorii(request, sezeni, stav_s_chybami(request, nazev=hlaska))
    if hlaska := proc_nazev_je_vyhrazen(nazev, data.klic, kategorie_vse):
        return _strana_kategorii(request, sezeni, stav_s_chybami(request, nazev=hlaska))
    # Klic je taky unikatni - bez kontroly by pridani spadlo na chybu 500.
    klic = data.klic
    if klic and uz_existuje(klic, sezeni.scalars(select(Kategorie.klic))):
        stav = stav_s_chybami(request, klic=f"Klíč „{klic}“ už má jiná kategorie.")
        return _strana_kategorii(request, sezeni, stav)

    poradi = dalsi_poradi(sezeni.scalars(select(Kategorie.poradi)), zacatek=1)
    sezeni.add(Kategorie(nazev=nazev, klic=prazdne_na_none(klic), poradi=poradi))
    sezeni.commit()
    oznam(request, f"Kategorie „{nazev}“ přidána.")
    return zpet("/kategorie")


@router.post("/kategorie/ulozit", dependencies=JEN_SPRAVCE)
async def ulozit(request: Request, sezeni: Databaze):
    formular = await nacti_formular(request)
    over_delky(formular, {"nazev": Kategorie.nazev})
    kategorie_vse = vsechny_kategorie(sezeni)
    # Pevna kategorie se neprejmenovava ani nemeni klic - server to vynucuje, pole v tabulce
    # je jen pro cteni. Hlaska se ukaze jen tehdy, kdyz nekdo doopravdy poslal jiny nazev.
    nazvy = {k.id: text_z_formulare(formular, f"nazev_{k.id}") for k in kategorie_vse}
    pevne_odmitnute: list[str] = []
    for kategorie in kategorie_vse:
        # Formular klic neposila; kdyby ho nekdo poslal primo, pevne se nemeni.
        if (
            f"klic_{kategorie.id}" in formular
            and text_z_formulare(formular, f"klic_{kategorie.id}").strip() != (kategorie.klic or "")
            and (hlaska := proc_nejde_zmenit_klic(kategorie))
        ):
            pevne_odmitnute.append(hlaska)
        novy = nazvy[kategorie.id].strip()
        if kategorie.pevna and novy and novy != kategorie.nazev:
            pevne_odmitnute.append(proc_nejde_prejmenovat(kategorie) or "")
        elif (
            novy
            and novy != kategorie.nazev
            # Obycejna kategorie se nesmi jmenovat jako pevna, ktera jeste neexistuje.
            and (
                hlaska := proc_nazev_je_vyhrazen(
                    novy, kategorie.klic, kategorie_vse, prejmenovani=True
                )
            )
        ):
            pevne_odmitnute.append(hlaska)
            nazvy[kategorie.id] = ""
        if kategorie.pevna:
            nazvy[kategorie.id] = ""
    odmitnute = prejmenuj_bez_kolizi(kategorie_vse, nazvy, "nazev")
    for kategorie in kategorie_vse:
        poradi = text_z_formulare(formular, f"poradi_{kategorie.id}")
        if (cislo := poradi_z_textu(poradi)) is not None:
            kategorie.poradi = cislo
    sezeni.commit()
    for nazev in odmitnute:
        oznam(request, f"„{nazev}“ už v seznamu je, přejmenování jsem neuložil.", "chyba")
    for hlaska in pevne_odmitnute:
        oznam(request, hlaska, "chyba")
    if not odmitnute and not pevne_odmitnute:
        oznam(request, "Uloženo.")
    return zpet("/kategorie")


@router.post("/kategorie/{kategorie_id}/smazat", dependencies=JEN_SPRAVCE)
async def smazat(request: Request, kategorie_id: int, sezeni: Databaze):
    kategorie = sezeni.get(Kategorie, kategorie_id)
    if kategorie is None:
        return zpet("/kategorie")

    if duvod := proc_nejde_smazat(sezeni, kategorie):
        oznam(request, duvod, "chyba")
        return zpet("/kategorie")

    sezeni.query(Slozka).filter(Slozka.kategorie_id == kategorie_id).update({"kategorie_id": None})
    sezeni.delete(kategorie)
    sezeni.commit()
    oznam(request, f"Kategorie „{kategorie.nazev}“ smazána.")
    return zpet("/kategorie")
