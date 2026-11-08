"""Které akce se ukážou na úvodní stránce a které čekají pod „Starší akce“.

Akce se nemažou, jen uzavírají, takže seznam by rostl navždy a s ním práce
úvodní stránky (u každé akce se počítají součty ze složek a účtenek). Proto:

- rozpracované akce se ukazují vždy, jak jsou staré jakkoli - někdo na nich
  pořád pracuje, nebo na ně zapomněl a pak je dobře, že jsou vidět;
- uzavřené akce jen za poslední rok;
- starší uzavřené jsou pod „Starší akce“ rozdělené po letech a načtou se až
  po výběru roku. Nic se nemaže ani neskrývá natrvalo.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import STAV_UZAVRENA, Akce

# Jak dlouho po konci akce zůstává uzavřená akce mezi běžnými.
DNI_MEZI_BEZNYMI = 365

# Kdy akce byla: konec, jinak začátek, jinak datum vyúčtování, jinak den založení
# (akce založené dřív, než pole s termínem vzniklo, žádné datum nemají). Datum je
# v SQLite text ve tvaru RRRR-MM-DD, takže se porovnává i seskupuje jako text.
DATUM_AKCE = func.coalesce(
    Akce.datum_do, Akce.datum_od, Akce.datum_vyuctovani, func.date(Akce.vytvoreno)
)


def _s_vazbami(dotaz):
    # Součty v tabulce sahají na složky (a jejich účtenky), ruční řádky knihy
    # a příjmy každé akce - bez selectinload je to dotaz na každou akci zvlášť.
    return dotaz.options(
        selectinload(Akce.slozky),
        selectinload(Akce.pokladni_radky),
        selectinload(Akce.prijmy),
    ).order_by(Akce.vytvoreno.desc())


def hranice(dnes: dt.date) -> dt.date:
    """Uzavřená akce, která skončila před tímhle dnem, je „starší“."""
    return dnes - dt.timedelta(days=DNI_MEZI_BEZNYMI)


def beznych(sezeni: Session, dnes: dt.date) -> list[Akce]:
    """Rozpracované akce a uzavřené z posledního roku, nejnovější první."""
    dotaz = select(Akce).where((Akce.stav != STAV_UZAVRENA) | (DATUM_AKCE >= hranice(dnes)))
    return list(sezeni.scalars(_s_vazbami(dotaz)))


def roky_starsich(sezeni: Session, dnes: dt.date) -> list[tuple[int, int]]:
    """Roky, ve kterých jsou starší uzavřené akce, a kolik jich v nich je -
    od nejnovějšího roku. Čte jen dvojice čísel, žádné akce."""
    rok = func.strftime("%Y", DATUM_AKCE)
    radky = sezeni.execute(
        select(rok, func.count())
        .where(Akce.stav == STAV_UZAVRENA, DATUM_AKCE < hranice(dnes))
        .group_by(rok)
        .order_by(rok.desc())
    )
    return [(int(r), pocet) for r, pocet in radky]


def starsi_v_roce(sezeni: Session, dnes: dt.date, rok: int) -> list[Akce]:
    """Starší uzavřené akce jednoho roku."""
    dotaz = select(Akce).where(
        Akce.stav == STAV_UZAVRENA,
        DATUM_AKCE < hranice(dnes),
        func.strftime("%Y", DATUM_AKCE) == f"{rok:04d}",
    )
    return list(sezeni.scalars(_s_vazbami(dotaz)))
