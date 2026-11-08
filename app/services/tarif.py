"""Prepinani mezi bezplatnym a placenym klicem ke Gemini.

Pri vyvoji se uctenky ctou bezplatnym klicem z Google AI Studio. Ten ma prisny
limit na pocet dotazu za minutu, takze se cte po malych davkach. U velke akce
(tabor, padesat uctenek) se vyplati prepnout na placeny klic - cte se po
vetsich davkach a nenarazi se na limit.

Prepinac je vedome rucni. Placene cteni stoji penize, takze se na nej aplikace
nikdy neprepne sama. Vyber si pamatuje prihlasovaci cookie, ne databaze -
po odhlaseni se tedy vzdy vraci zpatky na bezplatny klic.

Na serveru (`nastaveni.je_server`) je to jinak: cte se VZDY placenym klicem
a prepinac neexistuje. Skeny nejsou jen paragony, chodi sem i vypisy
z uctu a faktury s adresou, a podminky Gemini API zakazuji bezplatny klic pro
klienta, ktery slouzi uzivatelum v EHP. Bezplatny klic je jen pro vyvoj.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, MutableMapping
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import nastaveni
from app.models import PouzitiAI

ZDARMA = "zdarma"
PLACENY = "placeny"

# Pod timhle jmenem sedi vyber v prihlasovaci cookie.
KLIC_SEZENI = "tarif"

# Kolik skenu precist na jedno stisknuti tlacitka. Zdarma je davka mala kvuli
# limitu dotazu za minutu. Placena je vetsi, ale porad tak, aby stranka stihla
# odpovedet - jedna uctenka trva 5-15 sekund.
DAVKY = {ZDARMA: 5, PLACENY: 10}

# Cenik Gemini v USD za 1 milion tokenu (vstup / vystup). Overeno 2026-09-03
# (gemini-2.5-*) a 2026-09-27 (gemini-3.5-flash) na
# https://ai.google.dev/gemini-api/docs/pricing - pri zmene cen Google
# staci prepsat cisla tady, historie utraty v Logu cinnosti se prepocita
# sama (cena se neuklada, jen tokeny - viz app/models/log.py:PouzitiAI).
# "Premysleni" modelu (tokeny_mysleni) se uctuje jako vystupni tokeny.
# Vychozi model z config.py MUSI mit radek tady - hlida to automaticky test.
CENIK_USD_ZA_1M = {
    "gemini-3.5-flash": {"vstup": Decimal("1.50"), "vystup": Decimal("9.00")},
    "gemini-2.5-flash": {"vstup": Decimal("0.30"), "vystup": Decimal("2.50")},
    "gemini-2.5-flash-lite": {"vstup": Decimal("0.10"), "vystup": Decimal("0.40")},
    "gemini-2.5-pro": {"vstup": Decimal("1.25"), "vystup": Decimal("10.00")},
}


def placeny_je_nastaveny() -> bool:
    """Je v .env vyplneny placeny klic? Bez nej se prepinac vubec neukazuje."""
    return bool(nastaveni.google_api_key_placeny)


def prepinac_se_nabizi() -> bool:
    """Ma se ukazat prepinac klice? Jen pri vyvoji a s vyplnenym placenym klicem -
    na serveru neni mezi cim prepinat."""
    return placeny_je_nastaveny() and not nastaveni.je_server


def cteni_je_povolene() -> bool:
    """Smi se vubec cist pres AI? Na serveru jen s placenym klicem; bezplatny
    se tam nepouzije ani jako zaloha."""
    return placeny_je_nastaveny() or not nastaveni.je_server


def aktualni(sezeni: Mapping) -> str:
    """Ktery tarif je prave vybrany (`sezeni` je `request.session`).

    Na serveru vzdycky placeny; pri vyvoji bez placeneho klice vzdycky bezplatny.
    """
    if nastaveni.je_server:
        return PLACENY
    if not placeny_je_nastaveny():
        return ZDARMA
    return PLACENY if sezeni.get(KLIC_SEZENI) == PLACENY else ZDARMA


def nastav(sezeni: MutableMapping, tarif: str | None) -> str:
    """Ulozi vyber do sezeni (`request.session`) a vrati, co se doopravdy nastavilo.

    Cokoli jineho nez vyslovne "placeny" (a s vyplnenym klicem) znamena zdarma -
    at uz prijde z formulare cokoli, nikdy se necte za penize omylem. Na serveru
    se nic nenastavuje: vzdycky se cte placenym klicem.
    """
    if nastaveni.je_server:
        return PLACENY
    if tarif == PLACENY and placeny_je_nastaveny():
        sezeni[KLIC_SEZENI] = PLACENY
        return PLACENY
    sezeni.pop(KLIC_SEZENI, None)
    return ZDARMA


def api_klic(tarif: str) -> str:
    """Klic, kterym se ma volat Gemini. Na serveru nikdy ne bezplatny."""
    if nastaveni.je_server or (tarif == PLACENY and placeny_je_nastaveny()):
        return nastaveni.google_api_key_placeny
    return nastaveni.google_api_key


def davka(tarif: str) -> int:
    """Kolik skenu precist na jedno kliknuti."""
    return DAVKY.get(tarif, DAVKY[ZDARMA])


def odhad_czk(pouziti) -> Decimal | None:
    """Odhad ceny jednoho cteni v Kc z ulozenych `PouzitiAI`.

    Bezplatny tarif nestoji nic. U placeneho se pocita z tokenu podle
    `CENIK_USD_ZA_1M`; kdyz cenik pouzity model nezna (novy model, jeste
    nedoplneny), vrati se None - stranka pak ukaze "cenik neznamy" misto
    vymysleneho cisla.
    """
    if pouziti.tarif != PLACENY:
        return Decimal(0)
    ceny = CENIK_USD_ZA_1M.get(pouziti.model)
    if ceny is None:
        return None
    usd = (
        Decimal(pouziti.tokeny_vstup) * ceny["vstup"]
        + Decimal(pouziti.tokeny_vystup + pouziti.tokeny_mysleni) * ceny["vystup"]
    ) / Decimal(1_000_000)
    return (usd * nastaveni.kurz_usd_czk).quantize(Decimal("0.01"))


def _souhrn_pouziti(pouziti: list) -> dict:
    # Cena kazdeho cteni se zaokrouhli na halere zvlast a az pak se secte. Tokeny
    # se NESMI nejdriv secist a zaokrouhlit az soucet (dalo by se to udelat jednim
    # `GROUP BY` v SQL, bylo by to rychlejsi): vysel by o halere jiny cislo, nez
    # jaky vidi uzivatel u jednotlivych cteni, a nez co se doted ukazovalo.
    ceny = [odhad_czk(p) for p in pouziti]
    return {
        "pocet": len(pouziti),
        "kc": sum((c for c in ceny if c is not None), Decimal(0)),
        "nezname": any(c is None for c in ceny),
    }


# Sloupce, ze kterych se pocita cena (`odhad_czk`). Zbytek radku `PouzitiAI`
# (ucel, id skenu...) se do utraty nepotrebuje, tak se z databaze necte.
_SLOUPCE_CENY = (
    PouzitiAI.tarif,
    PouzitiAI.model,
    PouzitiAI.tokeny_vstup,
    PouzitiAI.tokeny_vystup,
    PouzitiAI.tokeny_mysleni,
)


def cteni_ai_souhrn(sezeni: Session, hranice: dt.datetime | None = None) -> dict:
    """Souhrn cteni AI za vsechny uzivatele (od `hranice`, bez ni za vsechno) -
    dlazdice na /log. Cte jen sloupce k cene; cena se pocita a zaokrouhluje po
    jednotlivych ctenich (viz `_souhrn_pouziti`), ne ze souctu tokenu."""
    dotaz = select(*_SLOUPCE_CENY)
    if hranice is not None:
        dotaz = dotaz.where(PouzitiAI.kdy >= hranice)
    return _souhrn_pouziti(list(sezeni.execute(dotaz)))


def cteni_ai_uzivatele(sezeni: Session, uzivatel_id: int) -> dict:
    """Souhrn cteni AI jednoho cloveka - pro /profil."""
    return cteni_ai_uzivatelu(sezeni, [uzivatel_id])[uzivatel_id]


def cteni_ai_uzivatelu(sezeni: Session, uzivatel_ids: list[int]) -> dict[int, dict]:
    """Souhrn cteni AI pro vic lidi najednou (jeden dotaz) - pro /uzivatele.
    Clovek bez jedineho cteni dostane souhrn s nulami."""
    pouziti: dict[int, list] = {uzivatel_id: [] for uzivatel_id in uzivatel_ids}
    if uzivatel_ids:
        dotaz = select(PouzitiAI.uzivatel_id, *_SLOUPCE_CENY).where(
            PouzitiAI.uzivatel_id.in_(uzivatel_ids)
        )
        for radek in sezeni.execute(dotaz):
            pouziti[radek.uzivatel_id].append(radek)
    return {uzivatel_id: _souhrn_pouziti(radky) for uzivatel_id, radky in pouziti.items()}
