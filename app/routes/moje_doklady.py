"""Doklady k podpisu prihlaseneho cloveka.

Jediny router, na ktery se dostane i ucet role Podepisujici - viz
`auth.pristup_k_akcim`. Zatim jen prazdna stranka, skutecny obsah (seznam
dokladu cekajicich na podpis a podepsanych) prijde s Etapou 12.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.sablony import strana

router = APIRouter()


@router.get("/moje-doklady", response_class=HTMLResponse)
async def seznam(request: Request):
    return strana(request, "moje_doklady.html")
