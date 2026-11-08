"""Log cinnosti - graficky prehled, kdo co kdy udelal. Cely router je jen
pro spravce, viz app/main.py."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic.functional_validators import BeforeValidator

from app.routes.spolecne import Databaze
from app.sablony import strana
from app.services import log_cinnosti

router = APIRouter()

POCET_NA_STRANKU = 300
VYCHOZI_TYP = "vse"
VYCHOZI_OBDOBI = "30"


def _prazdne_na_none(hodnota: Any) -> Any:
    return None if hodnota == "" else hodnota


# Formulář filtru posílá nevybranou volbu („— všichni —“) jako prázdný text
# (`uzivatel=&akce=`); bez tohohle by `int | None` takový dotaz odmítlo jako 422.
IdNeboNic = Annotated[int | None, BeforeValidator(_prazdne_na_none)]


@router.get("/log", response_class=HTMLResponse)
async def prehled(
    request: Request,
    sezeni: Databaze,
    uzivatel: IdNeboNic = None,
    akce: IdNeboNic = None,
    typ: str = VYCHOZI_TYP,
    obdobi: str = VYCHOZI_OBDOBI,
    strana_c: int = 1,
):
    hranice = log_cinnosti.obdobi_hranice(obdobi)
    zaznamy = log_cinnosti.dotaz_zaznamu(
        sezeni,
        uzivatel_id=uzivatel,
        akce_id=akce,
        typ=typ,
        hranice=hranice,
        limit=POCET_NA_STRANKU,
        offset=(max(strana_c, 1) - 1) * POCET_NA_STRANKU,
    )
    graf = log_cinnosti.aktivita_po_dnech(sezeni, hranice)
    # Kolik voleb filtru se liší od výchozích; na telefonu podle toho zůstane
    # nabídka „Filtr“ otevřená, aby bylo vidět, že se filtruje.
    pocet_filtru = sum(
        (uzivatel is not None, akce is not None, typ != VYCHOZI_TYP, obdobi != VYCHOZI_OBDOBI)
    )
    return strana(
        request,
        "log.html",
        po_dnech=log_cinnosti.seskup_po_dnech(zaznamy),
        souhrn=log_cinnosti.souhrn(sezeni, hranice),
        graf=graf,
        graf_max=max((d["zmeny"] + d["smazani"] for d in graf), default=1) or 1,
        uzivatele=log_cinnosti.seznam_uzivatelu_pro_filtr(sezeni),
        akce_seznam=log_cinnosti.seznam_akci_v_logu(sezeni),
        obdobi_volby=log_cinnosti.OBDOBI_VOLBY,
        vybrany_uzivatel=uzivatel,
        vybrana_akce=akce,
        vybrany_typ=typ,
        vybrane_obdobi=obdobi,
        pocet_filtru=pocet_filtru,
        strana_c=strana_c,
        ma_dalsi_stranku=len(zaznamy) == POCET_NA_STRANKU,
    )
