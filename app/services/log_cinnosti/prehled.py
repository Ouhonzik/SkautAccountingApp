"""Dotazy pro stranku /log (app/routes/log.py): filtry, casova osa, dlazdice
a graf. Jen cte - nic nezapisuje."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cas import PRAHA, ted
from app.models import (
    CINNOST_EMAIL_OMEZEN,
    CINNOST_ODHLASENI,
    CINNOST_POZVANKA,
    CINNOST_PRIDANI,
    CINNOST_PRIHLASENI,
    CINNOST_PRIHLASENI_NEUSPESNE,
    CINNOST_REGISTRACE,
    CINNOST_RESET_HESLA,
    CINNOST_RESET_HESLA_ZADOST,
    CINNOST_SMAZANI,
    CINNOST_UPRAVA,
    Uzivatel,
    ZaznamCinnosti,
)
from app.services import tarif

# Dny, ktere filtr "obdobi" umi; "vse" tu neni (bez hranice).
OBDOBI_DNY = {"7": 7, "30": 30, "90": 90}
OBDOBI_VOLBY = {"7": "7 dní", "30": "30 dní", "90": "90 dní", "vse": "vše"}

# Typy cinnosti seskupene do filtru na strance - "zmeny" pokryva pridani
# i upravu, "prihlaseni" i neuspesne pokusy a odhlaseni.
_TYPY_FILTRU = {
    "prihlaseni": (CINNOST_PRIHLASENI, CINNOST_PRIHLASENI_NEUSPESNE, CINNOST_ODHLASENI),
    "zmeny": (CINNOST_PRIDANI, CINNOST_UPRAVA),
    "smazani": (CINNOST_SMAZANI,),
    "ucty": (
        CINNOST_POZVANKA,
        CINNOST_REGISTRACE,
        CINNOST_RESET_HESLA_ZADOST,
        CINNOST_RESET_HESLA,
        CINNOST_EMAIL_OMEZEN,
    ),
}


def obdobi_hranice(obdobi: str) -> dt.datetime | None:
    dny = OBDOBI_DNY.get(obdobi)
    return ted() - dt.timedelta(days=dny) if dny else None


def _den_v_praze(kdy: dt.datetime) -> dt.date:
    """Den, do ktereho zaznam patri z pohledu cloveka v Praze. Cas v databazi je
    naivni UTC - zaznam z 00:30 mistniho casu by jinak spadl pod predchozi den
    (stejny prevod jako filtr `cas` v app/formaty.py)."""
    return kdy.replace(tzinfo=dt.UTC).astimezone(PRAHA).date()


def dotaz_zaznamu(
    sezeni: Session,
    *,
    uzivatel_id: int | None = None,
    akce_id: int | None = None,
    typ: str | None = None,
    hranice: dt.datetime | None = None,
    limit: int = 300,
    offset: int = 0,
) -> list[ZaznamCinnosti]:
    dotaz = select(ZaznamCinnosti).order_by(ZaznamCinnosti.do.desc(), ZaznamCinnosti.id.desc())
    if uzivatel_id is not None:
        dotaz = dotaz.where(ZaznamCinnosti.uzivatel_id == uzivatel_id)
    if akce_id is not None:
        dotaz = dotaz.where(ZaznamCinnosti.akce_id == akce_id)
    if typ in _TYPY_FILTRU:
        dotaz = dotaz.where(ZaznamCinnosti.typ_cinnosti.in_(_TYPY_FILTRU[typ]))
    if hranice is not None:
        dotaz = dotaz.where(ZaznamCinnosti.do >= hranice)
    return list(sezeni.scalars(dotaz.limit(limit).offset(offset)))


def seskup_po_dnech(zaznamy: list[ZaznamCinnosti]) -> list[dict]:
    """Seskupi jiz nactene zaznamy (serazene od nejnovejsiho) po dnech -
    pro casovou osu na /log. `zaznamy` musi byt serazene sestupne podle `do`."""
    skupiny: list[dict] = []
    for zaznam in zaznamy:
        den = _den_v_praze(zaznam.do)
        if not skupiny or skupiny[-1]["den"] != den:
            skupiny.append({"den": den, "zaznamy": []})
        skupiny[-1]["zaznamy"].append(zaznam)
    return skupiny


def souhrn(sezeni: Session, hranice: dt.datetime | None) -> dict:
    """Cisla do dlazdic nahore na /log."""

    def pocet_cinnosti(*typy: str) -> int:
        dotaz = select(func.coalesce(func.sum(ZaznamCinnosti.pocet), 0)).where(
            ZaznamCinnosti.typ_cinnosti.in_(typy)
        )
        if hranice is not None:
            dotaz = dotaz.where(ZaznamCinnosti.do >= hranice)
        return sezeni.scalar(dotaz) or 0

    dotaz_lide = select(func.count(func.distinct(ZaznamCinnosti.uzivatel_id))).where(
        ZaznamCinnosti.typ_cinnosti == CINNOST_PRIHLASENI
    )
    if hranice is not None:
        dotaz_lide = dotaz_lide.where(ZaznamCinnosti.do >= hranice)

    ai = tarif.cteni_ai_souhrn(sezeni, hranice)
    return {
        "prihlaseni": pocet_cinnosti(CINNOST_PRIHLASENI),
        "neuspesne": pocet_cinnosti(CINNOST_PRIHLASENI_NEUSPESNE),
        "zmeny": pocet_cinnosti(CINNOST_PRIDANI, CINNOST_UPRAVA),
        "smazani": pocet_cinnosti(CINNOST_SMAZANI),
        "aktivni_lide": sezeni.scalar(dotaz_lide) or 0,
        "cteni_ai": ai["pocet"],
        "cteni_ai_kc": ai["kc"],
        "cteni_ai_nezname": ai["nezname"],
    }


def aktivita_po_dnech(sezeni: Session, hranice: dt.datetime | None) -> list[dict]:
    """Pocty zmen a smazani po dnech pro sloupcovy graf na /log.

    Scita databaze, ne Python: log se nepromazava, takze by "vse" tahalo do
    pameti kazdy zaznam, jen aby z nich udelal par desitek sloupcu. Sdruzuje se
    po hodinach UTC a na prazsky den se prevadi az tady. Hodina je dost jemna,
    protoze prazsky cas se od UTC lisi o cele hodiny (+1 v zime, +2 v lete),
    takze cela hodina UTC vzdy spadne do jednoho prazskeho dne - a pritom je
    radku nejvys 24 na den, ne tolik, kolik je zaznamu."""
    hodina = func.strftime("%Y-%m-%dT%H", ZaznamCinnosti.do)
    dotaz = select(hodina, ZaznamCinnosti.typ_cinnosti, func.sum(ZaznamCinnosti.pocet)).group_by(
        hodina, ZaznamCinnosti.typ_cinnosti
    )
    if hranice is not None:
        dotaz = dotaz.where(ZaznamCinnosti.do >= hranice)
    podle_dne: dict[dt.date, dict[str, int]] = {}
    for hodina_utc, typ, pocet in sezeni.execute(dotaz):
        den = _den_v_praze(dt.datetime.fromisoformat(hodina_utc))
        radek = podle_dne.setdefault(den, {"zmeny": 0, "smazani": 0})
        if typ in (CINNOST_PRIDANI, CINNOST_UPRAVA):
            radek["zmeny"] += pocet
        elif typ == CINNOST_SMAZANI:
            radek["smazani"] += pocet
    return [{"den": den, **hodnoty} for den, hodnoty in sorted(podle_dne.items())]


def seznam_uzivatelu_pro_filtr(sezeni: Session) -> list[Uzivatel]:
    return list(sezeni.scalars(select(Uzivatel).order_by(Uzivatel.jmeno)))


def seznam_akci_v_logu(sezeni: Session) -> list[tuple[int, str]]:
    """Akce pro filtr - i smazane, ty zustavaji jen jako text (bez odkazu)."""
    radky = sezeni.execute(
        select(ZaznamCinnosti.akce_id, ZaznamCinnosti.akce_nazev)
        .where(ZaznamCinnosti.akce_id.is_not(None))
        .distinct()
    ).all()
    unikatni = {(aid, nazev or f"#{aid}") for aid, nazev in radky}
    return sorted(unikatni, key=lambda dvojice: dvojice[1])
