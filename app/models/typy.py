"""Vlastni typy sloupcu: penize v celych halerich, setiny a desetiny."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Integer, TypeDecorator


class Castka(TypeDecorator):
    """Penize: v databazi cele halere, v Pythonu Decimal.

    Ukladat penize jako desetinne cislo je klasicka chyba - 905,50 se v binarni
    plovouci carce presne ulozit neda a soucty pak o halere nesedi. Cele halere
    tenhle problem nemaji vubec.
    """

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        halere = Decimal(str(value)) * 100
        return int(halere.quantize(Decimal(1), rounding=ROUND_HALF_UP))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return (Decimal(value) / 100).quantize(Decimal("0.01"))


class Setiny(TypeDecorator):
    """Desetinne cislo na dve mista, v databazi cele setiny.

    Stejny trik jako Castka, jen to nejsou penize - a prave proto vlastni typ.
    Log cinnosti i sablony formatuji kazdy Decimal jako koruny, takze spotreba
    6,00 l/100 km ulozena jako Castka by se vsude hlasila jako "6,00 Kc".
    """

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        setiny = Decimal(str(value)) * 100
        return int(setiny.quantize(Decimal(1), rounding=ROUND_HALF_UP))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return (Decimal(value) / 100).quantize(Decimal("0.01"))


class Desetiny(TypeDecorator):
    """Desetinne cislo na jedno misto, v databazi cele desetiny (12,4 -> 124).

    Pro kilometry jizd: papir i mapa pocitaji s desetinami, ale `Castka`
    by je vsude hlasila jako koruny a `Setiny` ma mista dvě. Zaokrouhluje se
    `ROUND_HALF_UP` stejne jako dnes mapa (12,25 -> 12,3).
    """

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        desetiny = Decimal(str(value)) * 10
        return int(desetiny.quantize(Decimal(1), rounding=ROUND_HALF_UP))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return (Decimal(value) / 10).quantize(Decimal("0.1"))
