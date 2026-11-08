"""Hlidani data uctenky proti terminu akce.

Uctenka, ktera do terminu akce nespada, je bud preklep pri cteni skenu, nebo
omylem nahrany doklad z jine akce. Cim dal od akce, tim hlasitejsi odznacek.

Po akci se hlida prisneji nez pred ni: veci se nakupuji **na akci, ne po ni**,
takze nakup den predem je bezny, ale nakup den po ni uz je podezrely.

Uctenky mimo termin se ale realne objevuji a normalne se uctuji - proto jde
varovani ztisit (`Sken.varovani_ztiseno`). Ztisene varovani zustane videt,
jen prestane kricet; tenhle soubor o ztiseni nevi, resi ho sablona.
"""

from __future__ import annotations

import datetime as dt
from typing import NamedTuple

from app.formaty import mnozne

# Stupne od nejmirnejsiho. Nazvy odpovidaji tridam odznacku v app.css.
POZOR = "pozor"  # zluta
VAROVANI = "varovani"  # oranzova
CHYBA = "chyba"  # cervena

# Hranice ve dnech od nejblizsiho konce terminu akce.
BLIZKO = 3  # do tri dnu vcetne
TYDEN = 7  # do tydne vcetne; nad nim uz cervena


def odchylka(datum: dt.date, datum_od: dt.date, datum_do: dt.date) -> int:
    """O kolik dni je datum mimo termin akce.

    Zaporne = tolik dni pred zacatkem, kladne = tolik dni po konci,
    nula = uvnitr terminu.
    """
    if datum < datum_od:
        return (datum - datum_od).days
    if datum > datum_do:
        return (datum - datum_do).days
    return 0


def stupen(dni: int) -> str | None:
    """Jak hlasite varovat pri dane odchylce. None = nevarovat vubec."""
    if dni == 0:
        return None
    if abs(dni) > TYDEN:
        return CHYBA
    if dni > 0:
        # Po akci: i jeden den uz je oranzovy.
        return VAROVANI
    return POZOR if abs(dni) <= BLIZKO else VAROVANI


def znacka(dni: int) -> str:
    """Kratky text do odznacku. Sloupec s datem je uzky, cela veta by se v nem
    zlamala do tri radku - ta patri do bubliny po najeti mysi."""
    pocet = abs(dni)
    return f"{mnozne(pocet, 'den', 'dny', 'dní')} {'po akci' if dni > 0 else 'před akcí'}"


def popis(dni: int) -> str:
    """Cela veta do bubliny - rekne, co je s datem spatne."""
    pocet = abs(dni)
    kdy = "po konci akce" if dni > 0 else "před začátkem akce"
    return f"Účtenka je {mnozne(pocet, 'den', 'dny', 'dní')} {kdy}."


class Varovani(NamedTuple):
    stupen: str  # nazev tridy odznacku: pozor / varovani / chyba
    znacka: str  # kratky text primo v odznacku
    popis: str  # cela veta do bubliny


def zkontroluj(datum: dt.date | None, akce) -> Varovani | None:
    """Varovani pro jednu uctenku, nebo None, kdyz je vsechno v poradku.

    Bez data uctenky nebo bez terminu akce se nehlida nic - na chybejici datum
    upozornuje pokladni kniha zvlast a stare akce termin vyplneny nemaji.
    """
    if datum is None or not akce.ma_termin:
        return None
    dni = odchylka(datum, akce.datum_od, akce.datum_do)
    uroven = stupen(dni)
    return Varovani(uroven, znacka(dni), popis(dni)) if uroven else None
