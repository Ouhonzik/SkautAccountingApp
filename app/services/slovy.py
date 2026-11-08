"""Castka slovy pro pokladni doklady.

Na prijmovem i vydajovem dokladu se castka opakuje slovy, aby ji nesel dopsat
dalsi nula. Cislo se pise dohromady bez mezer - `ctyritisicesedmset` - presne
z tehoz duvodu: mezi slova uz se nic nevejde.

Cesky se cislovka ridi rodem toho, co pocita, takze `jedna koruna` ale
`jeden halir`, a `dva tisice` ale `dve koruny`. Rod proto chodi az z volajiciho.
"""

from __future__ import annotations

from decimal import Decimal

# Jednicka a dvojka maji tvar podle rodu, od trojky vys uz je cislovka stejna.
RODOVE = {
    1: {"m": "jeden", "z": "jedna"},
    2: {"m": "dva", "z": "dvě"},
}
JEDNOTKY = {3: "tři", 4: "čtyři", 5: "pět", 6: "šest", 7: "sedm", 8: "osm", 9: "devět"}
NACTE = {
    10: "deset",
    11: "jedenáct",
    12: "dvanáct",
    13: "třináct",
    14: "čtrnáct",
    15: "patnáct",
    16: "šestnáct",
    17: "sedmnáct",
    18: "osmnáct",
    19: "devatenáct",
}
DESITKY = {
    2: "dvacet",
    3: "třicet",
    4: "čtyřicet",
    5: "padesát",
    6: "šedesát",
    7: "sedmdesát",
    8: "osmdesát",
    9: "devadesát",
}
STOVKY = {
    1: "sto",
    2: "dvěstě",
    3: "třista",
    4: "čtyřista",
    5: "pětset",
    6: "šestset",
    7: "sedmset",
    8: "osmset",
    9: "devětset",
}

# Rad, jeho tri tvary a rod cislovky pred nim: `dva tisice`, ale `dve miliardy`.
RADY = (
    (10**9, ("miliarda", "miliardy", "miliard"), "z"),
    (10**6, ("milion", "miliony", "milionů"), "m"),
    (10**3, ("tisíc", "tisíce", "tisíc"), "m"),
)

KORUNY = ("koruna česká", "koruny české", "korun českých")
HALERE = ("haléř", "haléře", "haléřů")


def _tvar(pocet: int, tvary: tuple[str, str, str]) -> str:
    """Sklonovani po cisle. Ridi se posledni cislici, ne celym cislem.

    Tohle pravidlo je zamerne jine nez `formaty.mnozne` (texty na obrazovce,
    "22 dní"): na dokladu stoji spisovne "dvacet dva koruny" a jeho tvar musi
    sedet na slovo, kdyz se pise cislovka slovy.

    Proto `dvacet dva tisice`, ale `dvacet pet tisic` - a `dvanact tisic`,
    protoze nactova cisla maji vlastni pravidlo.
    """
    if 11 <= pocet % 100 <= 19:
        return tvary[2]
    posledni = pocet % 10
    if posledni == 1:
        return tvary[0] if pocet == 1 else tvary[2]
    if 2 <= posledni <= 4:
        return tvary[1]
    return tvary[2]


def _jednotka(cislice: int, rod: str) -> str:
    return RODOVE[cislice][rod] if cislice in RODOVE else JEDNOTKY[cislice]


def _do_tisice(cislo: int, rod: str) -> str:
    """Cislo 1-999 jednim slovem."""
    slova = ""
    stovky, zbytek = divmod(cislo, 100)
    if stovky:
        slova += STOVKY[stovky]
    if zbytek >= 20:
        desitky, jednotky = divmod(zbytek, 10)
        slova += DESITKY[desitky]
        if jednotky:
            slova += _jednotka(jednotky, rod)
    elif zbytek >= 10:
        slova += NACTE[zbytek]
    elif zbytek:
        slova += _jednotka(zbytek, rod)
    return slova


def cislo(hodnota: int, rod: str = "z") -> str:
    """Cele cislo slovy, vsechno dohromady bez mezer."""
    if hodnota == 0:
        return "nula"

    casti: list[str] = []
    zbytek = hodnota
    for velikost, tvary, rod_radu in RADY:
        pocet, zbytek = divmod(zbytek, velikost)
        if pocet:
            casti.append(_do_tisice(pocet, rod_radu) + _tvar(pocet, tvary))
    if zbytek:
        casti.append(_do_tisice(zbytek, rod))
    return "".join(casti)


def korunami(castka: Decimal | float) -> str:
    """1460.50 -> 'jedentisícčtyřistašedesát korun českých padesát haléřů'.

    Halere se pripoji jen kdyz nejsou nulove - na dokladu za 4 700 Kč nema
    'nula haléřů' co delat.
    """
    presne = Decimal(str(castka)).quantize(Decimal("0.01"))
    zaporna = presne < 0
    koruny, halere = divmod(int(abs(presne) * 100), 100)

    text = f"{cislo(koruny, 'z')} {_tvar(koruny, KORUNY)}"
    if halere:
        text += f" {cislo(halere, 'm')} {_tvar(halere, HALERE)}"
    return f"mínus {text}" if zaporna else text
