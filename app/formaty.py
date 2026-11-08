"""Formatovani cisel, cen, dat a casu pro obrazovku, tiskopisy a text logu.

Cista pravidla bez zavislosti na HTML strankach - proto je smi pouzivat i
sluzby (text zaznamu v logu cinnosti, hlasky). Sablony si je registruji jako
filtry v `app/sablony.py`.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from app.cas import PRAHA

DNY_V_TYDNU = ("Pondělí", "Úterý", "Středa", "Čtvrtek", "Pátek", "Sobota", "Neděle")


def koruny(hodnota: Decimal | float | None) -> str:
    """1234.5 -> '1 234,50 Kč'. Mezera je pevna, aby se cislo nezalomilo."""
    if hodnota is None:
        return ""
    cislo = f"{Decimal(hodnota):,.2f}".replace(",", " ").replace(".", ",")
    return f"{cislo} Kč"


def desetinne(hodnota: Decimal | float | None) -> str:
    """6.00 -> '6,00'. Pro cisla, ktera nejsou penize - treba spotrebu v litrech.

    Decimal se do sablony vypisuje s tecko, cesky se ale pise carka; `koruny`
    se na to pouzit neda, pripsal by " Kc".
    """
    if hodnota is None:
        return ""
    return str(hodnota).replace(".", ",")


def km(hodnota: Decimal | float | None) -> str:
    """12.4 -> '12,4', 7.0 -> '7'. Kilometry jizd (jedno desetinne misto), cesky.

    Cele hodnoty bez ",0", at se "7 km" nepise jako "7,0 km"; mezery po tisicich
    ne - kilometry se uvadeji i v poli, ktere se cte zpet jako cislo.
    """
    if hodnota is None:
        return ""
    text = str(Decimal(str(hodnota)).quantize(Decimal("0.1")))
    return text.removesuffix(".0").replace(".", ",")


def cele_koruny(hodnota: Decimal | float | None) -> str:
    if hodnota is None:
        return ""
    return f"{int(hodnota):,}".replace(",", " ")


def datum(hodnota) -> str:
    return hodnota.strftime("%d. %m. %Y") if hodnota else ""


def cas(hodnota: dt.datetime | None) -> str:
    """Naivni UTC z databaze -> 'HH:MM' mistniho casu."""
    if hodnota is None:
        return ""
    mistni = hodnota.replace(tzinfo=dt.UTC).astimezone(PRAHA)
    return mistni.strftime("%H:%M")


def den_slovy(den: dt.date | None) -> str:
    if den is None:
        return ""
    return f"{DNY_V_TYDNU[den.weekday()]} {den.day}. {den.month}. {den.year}"


def z_json(text: str | None) -> dict:
    """detail_json ze ZaznamCinnosti -> dict poli, ktera se v uprave zmenila."""
    if not text:
        return {}
    try:
        return json.loads(text)
    except ValueError:
        return {}


def inicialy(jmeno: str | None) -> str:
    """'Jan Kratochvíl' -> 'JK'. Pro avatar v Logu cinnosti."""
    if not jmeno:
        return "?"
    slova = jmeno.split()
    if len(slova) >= 2:
        return (slova[0][0] + slova[1][0]).upper()
    return jmeno[:2].upper()


def mnozne(pocet: int, jedna: str, dve: str, pet: str) -> str:
    """Sklonovani podstatneho jmena po cisle: 1 polozka, 2 polozky, 5 polozek.

    Jedno pravidlo pro cely text appky: tvar se ridi CELYM cislem (1 / 2-4 /
    ostatni), takze jednotne "22 dní", "21 účtenek". Pravidlo podle posledni
    cislice ("22 dny") se zamerne nepouziva, aby appka nepsala jednou "22 dní"
    a jinde "22 dny". Kopie v prohlizeci: `app/static/js/tabulky.js` (odznacek
    poctu) - musi s touto funkci sedet.
    """
    if pocet == 1:
        tvar = jedna
    elif 2 <= pocet <= 4:
        tvar = dve
    else:
        tvar = pet
    return f"{pocet} {tvar}"
