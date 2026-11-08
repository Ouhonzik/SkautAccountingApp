"""Pravidla terminu akce - jedno misto pro formular i obnovu ze zalohy.

Z terminu se sestavuje mrizka dochazky (ucastnik x den) a ta se zapisuje do
databaze i do Excelu. Akce s rozsahem 0001-9999 by mela 3,6 milionu dnu a
zastavila server, stejne jako obycejny preklep v roce (2062 misto 2026).
"""

from __future__ import annotations

import datetime as dt

# Nejdelsi akce, ktera se v appce dava zadat. Cislo odsouhlasil Honza.
MAX_DNU_AKCE = 62
ROK_NEJDRIV = 2000
ROK_NEJPOZDEJI = 2100


def over_rok(datum: dt.date | None) -> str | None:
    """Hlaska, kdyz je rok data mimo rozumne meze; jinak None."""
    if datum is not None and not ROK_NEJDRIV <= datum.year <= ROK_NEJPOZDEJI:
        return f"Rok musí být mezi {ROK_NEJDRIV} a {ROK_NEJPOZDEJI}."
    return None


def over_termin(od: dt.date | None, do: dt.date | None) -> str | None:
    """Vrati cesky text chyby, nebo None, kdyz je termin v poradku.

    Termin je bud vyplneny cely, nebo vubec - jeden konec sam o sobe by
    neumel ani mrizku dochazky, ani hlidani dat uctenek.
    """
    if od is None or do is None:
        if od is None and do is None:
            return None
        return "Termín akce vyplň celý — oba dny, nebo ani jeden."
    if chyba := over_rok(od) or over_rok(do):
        return chyba
    if do < od:
        return "Konec akce nemůže být dřív než její začátek."
    if (do - od).days + 1 > MAX_DNU_AKCE:
        return f"Akce může trvat nejvýš {MAX_DNU_AKCE} dní."
    return None
