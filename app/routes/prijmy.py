"""Prijmy akce - typicky ucastnicke poplatky."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.cas import dnes
from app.chyby_poli import StavFormulare, stav_s_chybami
from app.formulare import PrijemForm, PrijemRadekForm, formular_s_chybami, id_z_textu, over_delky
from app.models import Akce, Clovek, Prijem
from app.routes.spolecne import (
    Databaze,
    hlaska_necitelnych,
    kam_dal,
    na_datum,
    nacti_akci,
    nacti_formular,
    nacti_otevrenou_akci,
    over_otevrenou,
    prazdne_na_none,
    prubezne_ulozeni,
    text_z_formulare,
    uz_smazano_kam,
)
from app.sablony import oznam, strana, strana_s_chybou, zpet
from app.services.ciselniky import dalsi_poradi, vsichni_lide

router = APIRouter()


def _strana_prijmu(
    request: Request, sezeni: Session, akce: Akce, stav: StavFormulare | None = None
) -> HTMLResponse:
    """Prijmy akce; se `stav` je to znovu vykresleny formular „Pridat prijem“ s chybou u pole."""
    kontext = {
        "akce": akce,
        # Predvyplnene datum u noveho prijmu - nejcasteji se zadava dnesni den.
        "dnes": dnes().isoformat(),
        "lide": vsichni_lide(sezeni),
    }
    if stav is not None:
        return strana_s_chybou(request, "prijmy.html", stav, **kontext)
    return strana(request, "prijmy.html", **kontext)


@router.get("/akce/{akce_id}/prijmy", response_class=HTMLResponse)
async def prehled(request: Request, akce_id: int, sezeni: Databaze):
    return _strana_prijmu(request, sezeni, nacti_akci(sezeni, akce_id))


@router.post("/akce/{akce_id}/prijmy/pridat")
async def pridat(
    request: Request,
    akce_id: int,
    data: Annotated[PrijemForm | StavFormulare, Depends(formular_s_chybami(PrijemForm))],
    sezeni: Databaze,
):
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    # Chyba formulare: stranka znovu, s chybou u pole a tim, co clovek napsal.
    if isinstance(data, StavFormulare):
        return _strana_prijmu(request, sezeni, akce, data)
    # Neexistujici clovek by jinak shodil ulozeni na cizim klici (chyba 500).
    if data.clovek is not None and sezeni.get(Clovek, data.clovek) is None:
        return _strana_prijmu(
            request,
            sezeni,
            akce,
            stav_s_chybami(
                request, clovek="Vybraný člověk v nabídce není. Vyber někoho ze seznamu."
            ),
        )
    poradi = dalsi_poradi(p.poradi for p in akce.prijmy)
    sezeni.add(
        Prijem(
            akce=akce,
            popis=data.popis or "Příjem",
            castka=data.castka,
            datum=data.datum,
            clovek_id=data.clovek,
            poradi=poradi,
        )
    )
    sezeni.commit()
    oznam(request, "Příjem přidán.")
    return zpet(f"/akce/{akce_id}/prijmy")


# Hodnota volby "puvodni text" v nabidce u prijmu, jehoz jmeno se v ciselniku
# nenaslo (viz prijmy.html): vyber znamena "nech to, jak to je".
VOLBA_PUVODNI_TEXT = "text"


def _uloz_kdo(sezeni: Session, prijem: Prijem, hodnota: str) -> None:
    """Nastavi, kdo prijem vybral. Prazdna volba cloveka i stary text smaze,
    vybrany clovek stary text nahradi; nectitelne nebo neexistujici ID se
    preskoci - cizi klic by jinak shodil ulozeni chybou 500."""
    if hodnota == VOLBA_PUVODNI_TEXT:
        return
    if not hodnota:
        prijem.clovek_id = None
        prijem.kdo = None
    elif (cislo := id_z_textu(hodnota)) is not None and sezeni.get(Clovek, cislo):
        prijem.clovek_id = cislo
        prijem.kdo = None


@router.post("/akce/{akce_id}/prijmy/ulozit")
async def ulozit(request: Request, akce_id: int, sezeni: Databaze):
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    formular = await nacti_formular(request)
    over_delky(formular, {"popis": Prijem.popis})
    # Prubezne ukladani posila jen zmenena pole: pole, ktere neprislo,
    # zustava, jak je. Prazdna hodnota naopak znamena "smazat".
    necitelnych = 0
    for prijem in akce.prijmy:
        popis = prazdne_na_none(text_z_formulare(formular, f"popis_{prijem.id}"))
        if popis:
            prijem.popis = popis
        # Nečitelná částka (např. „abc“) se nesmí tvářit jako uložená: ostatní pole se
        # uloží a odpověď je 422 s větou pro bublinu. Prázdná částka zůstává beze změny.
        if f"castka_{prijem.id}" in formular:
            zadano = PrijemRadekForm.model_validate(
                {"castka": text_z_formulare(formular, f"castka_{prijem.id}")}
            )
            necitelnych += zadano.necitelnych()
            if zadano.zmeny().get("castka") is not None:
                prijem.castka = zadano.castka
        if f"datum_{prijem.id}" in formular:
            prijem.datum = na_datum(text_z_formulare(formular, f"datum_{prijem.id}"))
        if f"kdo_{prijem.id}" in formular:
            _uloz_kdo(sezeni, prijem, text_z_formulare(formular, f"kdo_{prijem.id}"))
    sezeni.commit()
    if (hotovo := prubezne_ulozeni(request, necitelnych)) is not None:
        return hotovo
    oznam(request, "Uloženo.")
    if necitelnych:
        oznam(request, hlaska_necitelnych(necitelnych), "chyba")
    return zpet(f"/akce/{akce_id}/prijmy")


@router.post("/prijem/{prijem_id}/smazat")
async def smazat(request: Request, prijem_id: int, sezeni: Databaze):
    prijem = sezeni.get(Prijem, prijem_id)
    if prijem is None:
        return await uz_smazano_kam(request)
    over_otevrenou(prijem.akce)
    akce_id = prijem.akce_id
    sezeni.delete(prijem)
    sezeni.commit()
    oznam(request, "Příjem smazán.")
    return zpet(await kam_dal(request, f"/akce/{akce_id}/prijmy"))
