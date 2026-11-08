"""Hodiny a casove pasmo appky na jednom miste.

Casy v databazi jsou naivni UTC (`ted`), na obrazovce a ve formularich se ale
pouziva mistni cas (`PRAHA`). Modul nezavisi na zadne jine casti appky, takze
ho smi importovat sluzby i routy.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

# Server na Hetzneru bezi v UTC, Honza pracuje podle prazskeho casu - proto se
# "dnes" a hodiny na obrazovce nikdy nectou z hodin serveru primo.
PRAHA = ZoneInfo("Europe/Prague")


def ted() -> dt.datetime:
    """Naivni UTC - stejna konvence jako `Uzivatel.posledni_prihlaseni`."""
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def dnes() -> dt.date:
    """Dnesni datum podle Prahy (po pulnoci mistniho casu uz je dalsi den)."""
    return dt.datetime.now(PRAHA).date()
