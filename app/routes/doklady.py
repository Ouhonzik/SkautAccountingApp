"""Tiskopisy PPD a VPD.

Jedina routa - stranka s doklady akce. Tisk resi prohlizec (Ctrl+P), zadny
generator PDF; predpisy pro papir jsou v app/static/tisk.css.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.config import nastaveni
from app.routes.spolecne import Databaze, nacti_akci
from app.sablony import strana
from app.services import doklady as tiskopisy_sluzba

router = APIRouter()

# Adresa titulniho listu v `?doklad=`. Neni to klic zadneho tiskopisu
# (ty jsou "slozka:5"), takze se s nimi nemuze potkat.
TITULNI = "titulni"


@router.get("/akce/{akce_id}/doklady", response_class=HTMLResponse)
async def prehled(
    request: Request,
    akce_id: int,
    sezeni: Databaze,
    doklad: str = "",
):
    """Vsechny doklady akce, nebo jen jeden - `?doklad=slozka:5`.

    `?doklad=titulni` vytiskne samotnou uvodni stranu. Pocita se porad ze
    vsech dokladu akce, i kdyz se tisknou jinde - jinak by soupis na ni
    neodpovidal tomu, co ve slozce lezi.
    """
    akce = nacti_akci(sezeni, akce_id)
    tiskopisy = tiskopisy_sluzba.sestav(akce)
    jen_titulni = doklad == TITULNI

    jen_jeden = (
        tiskopisy_sluzba.jeden(akce, doklad, tiskopisy) if doklad and not jen_titulni else None
    )
    if jen_jeden is not None:
        tiskopisy = [jen_jeden]

    # Titulni list jen pri tisku cele slozky nebo jeho samotneho. Jednotlivy
    # doklad se dotiskuje do slozky, ktera uz svou uvodni stranu ma - druha
    # by tam jen prekazela.
    titulni = None if jen_jeden is not None else tiskopisy_sluzba.titulni_list(akce, tiskopisy)

    bez_data, mimo_slozku = tiskopisy_sluzba.problemy(tiskopisy)
    return strana(
        request,
        "doklady.html",
        akce=akce,
        tiskopisy=tiskopisy,
        titulni=titulni,
        jen_jeden=jen_jeden is not None,
        jen_titulni=jen_titulni,
        pocet_bez_data=bez_data,
        pocet_mimo_slozku=mimo_slozku,
        stredisko=nastaveni.stredisko_nazev,
        sidlo=nastaveni.stredisko_sidlo,
        ico=nastaveni.stredisko_ico,
        dic=nastaveni.stredisko_dic,
    )
