"""Zapis do logu cinnosti: automaticky hook po ulozeni a rucni `zapis()`.

Vetsinu radku vyrabi automaticky - napojuje se na SQLAlchemy udalosti
`after_flush` a `after_flush_postexec`, ktere bezi pri kazdem ulozeni
(`Session.commit()`), a to na VSECH databazovych sezenich v procesu vcetne
testovacich (posluchace se registruji na tridu `Session`, ne na konkretni
instanci). Diky tomu neni treba psat volani do kazde z desitek POST rout -
staci, ze routa zmeni ORM objekty a zavola `sezeni.commit()`.

Bez aktera se neloguje nic. Aktera nastavuje `app/auth.py:nacti_uzivatele`
do `Session.info["aktor"]` u kazdeho HTTP pozadavku; migrace, `create_all`
v testech ani primy pristup k `sezeni` v testech aktera nemaji, takze
zustavaji tise. CLI prikaz `uv run spravce` si aktera nastavuje sam.

Rucne pres `zapis()` se zapisuje to, co automaticky log nevidi (nemeni se zadny
radek sledovane tabulky, nebo je tabulka z logu vyjmuta): prihlaseni, odhlaseni a neuspesne
pokusy, registrace a obnova hesla (zadost i dokonceni), odeslani pozvanky,
obnova ze zalohy, stazeni exportu a omezeni e-mailu. Presny vycet jsou
konstanty `CINNOST_*` v `app/models/konstanty.py`.

Bezpecnost logu samotneho: hook bezi v `try/except` a chybu jen zaloguje do
konzole (`logging.exception`) - nikdy nesmi shodit ulozeni dat, ktere se
zrovna deje. V testech se `PRISNE` prepne na True, aby chyby vybublaly a
nezustaly skryte.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.cas import ted
from app.formaty import mnozne
from app.models import (
    CINNOST_PRIDANI,
    CINNOST_SMAZANI,
    CINNOST_UPRAVA,
    Akce,
    ZaznamCinnosti,
)
from app.services.log_cinnosti.popisky import (
    IGNOROVANA_POLE,
    NAZVY_OBJEKTU,
    POPISKY_POLI,
    SKRYTA_POLE,
    akce_id_objektu,
    formatuj_hodnotu,
    je_osobni,
    loguje_se,
    nazev_akce,
    nazev_v_uvozovkach,
)

log = logging.getLogger(__name__)

# V testech se prepina na True, aby chyba v logu neprosla tise - jinak by se
# rozbity hook mohl schovat za "vsechno zelene", protoze data se stejne
# ulozi (viz docstring modulu).
PRISNE = False

# Dve pruzebne ulozeni te same zmeny do OKNA se slouci do jednoho zaznamu -
# jinak by kazde psani do policka po 700 ms (viz app/static/js/prubezne.js) vyrobilo
# vlastni radek v logu.
OKNO_SLOUCENI = dt.timedelta(minutes=10)


@contextmanager
def bez_automatickeho_zapisu(sezeni: Session) -> Iterator[Any]:
    """Po dobu bloku aktera ze `sezeni` vyjme, takze se nezapisuji automaticke
    radky logu (napr. pri obnove ze zalohy by kazdy sken, slozka a ucastnik
    udelal vlastni radek). Aktera vrati VZDY - i pri vyjimce - jinak by dalsi
    zapis v tomhle sezeni prisel o autora. Vraci vyjmuteho aktera, aby ho
    volajici mohl pouzit pro jeden souhrnny rucni `zapis()` po bloku."""
    aktor = sezeni.info.pop("aktor", None)
    try:
        yield aktor
    finally:
        if aktor is not None:
            sezeni.info["aktor"] = aktor


def zapis(
    sezeni: Session,
    typ_cinnosti: str,
    *,
    popis: str,
    uzivatel_id: int | None = None,
    uzivatel_jmeno: str = "neznámé jméno",
    ip: str | None = None,
    akce_id: int | None = None,
    akce_nazev: str | None = None,
) -> None:
    """Rucni zaznam pro veci, ktere nejsou (jen) zmenou radku v databazi -
    prihlaseni, odhlaseni, neuspesny pokus, registrace, reset hesla, pozvanka,
    obnova ze zalohy, stazeni exportu. Volat pred
    `sezeni.commit()`.

    `akce_id`/`akce_nazev` jsou volitelne - hodi se u obnovy ze zalohy, aby
    jeden souhrnny radek sel dohledat i ve filtru na strance /log podle akce,
    presne jako zaznamy z automatickeho logovani nize."""
    cas = ted()
    sezeni.add(
        ZaznamCinnosti(
            uzivatel_id=uzivatel_id,
            uzivatel_jmeno=uzivatel_jmeno,
            typ_cinnosti=typ_cinnosti,
            popis=popis,
            od=cas,
            do=cas,
            ip=ip,
            akce_id=akce_id,
            akce_nazev=akce_nazev,
        )
    )


# --------------------------------------------------------------------------
# Automaticka detekce zmen - napojeni na SQLAlchemy udalosti.
# --------------------------------------------------------------------------


def _zmenena_pole(obj) -> dict[str, tuple]:
    """Vraci {nazev_pole: (pred, po)} pro sloupce, ktere se opravdu zmenily."""
    mapper = sa.inspect(obj).mapper
    stav = sa.inspect(obj)
    pk_sloupce = {c.name for c in mapper.primary_key}
    vysledek: dict[str, tuple] = {}
    for vlastnost in mapper.column_attrs:
        nazev = vlastnost.key
        if nazev in pk_sloupce or nazev in IGNOROVANA_POLE:
            continue
        historie = stav.attrs[nazev].history
        if not historie.has_changes():
            continue
        pred = historie.deleted[0] if historie.deleted else None
        po = historie.added[0] if historie.added else getattr(obj, nazev, None)
        if pred == po:
            continue
        vysledek[nazev] = (pred, po)
    return vysledek


def _vykresli_casti(detail: dict) -> str:
    """Slozi cast popisu 'pole pred → po, jine pole: zmeneno' z detailu.

    Sdileno mezi prvnim vznikem zaznamu a slucovanim (`_sluc`), aby popis po
    sloucení vypadal stejne, jako kdyby se rovnou napsal spravne."""
    casti = []
    for nazev_pole, zmena in detail.items():
        popisek = POPISKY_POLI.get(nazev_pole, nazev_pole)
        if zmena.get("skryto"):
            casti.append(f"{popisek}: změněno")
        else:
            casti.append(f"{popisek} {zmena['pred']} → {zmena['po']}")
    return ", ".join(casti)


def _popis_zmeny(sezeni: Session, obj) -> tuple[str | None, str | None, dict | None]:
    """Vraci (zaklad, popis, detail). `zaklad` je jen "typ „jméno“" bez
    vypisu zmen - `_sluc` ho potrebuje zvlast, aby po sloucení slo popis
    poskladat znovu z aktualniho (sloucenho) detailu."""
    pole = _zmenena_pole(obj)
    if not pole:
        return None, None, None

    tabulka: str = obj.__tablename__
    zaklad = NAZVY_OBJEKTU.get(tabulka, tabulka) + nazev_v_uvozovkach(obj)

    if je_osobni(obj):
        seznam_poli = ", ".join(POPISKY_POLI.get(p, p) for p in sorted(pole))
        zmeneno = mnozne(len(pole), "změněné pole", "změněná pole", "změněných polí")
        # Osobni udaje se do detailu neukladaji vubec - u slouceni proto
        # popis jen ukaze posledni stav zmen, ne kumulativni pocet poli.
        return zaklad, f"{zaklad}: {zmeneno} ({seznam_poli})", None

    detail = {}
    for nazev_pole, (pred, po) in pole.items():
        if nazev_pole in SKRYTA_POLE:
            detail[nazev_pole] = {"skryto": True}
        else:
            detail[nazev_pole] = {
                "pred": formatuj_hodnotu(sezeni, nazev_pole, pred),
                "po": formatuj_hodnotu(sezeni, nazev_pole, po),
            }
    return zaklad, f"{zaklad}: {_vykresli_casti(detail)}", detail


def _popis_smazane_akce(sezeni: Session, akce: Akce) -> str:
    pocty: dict[str, int] = {}
    for obj in sezeni.deleted:
        if obj is akce or not loguje_se(obj):
            continue
        if akce_id_objektu(obj) != akce.id:
            continue
        tabulka: str = obj.__tablename__
        nazev = NAZVY_OBJEKTU.get(tabulka, tabulka)
        pocty[nazev] = pocty.get(nazev, 0) + 1
    zaklad = f"smazána akce „{akce.nazev}“"
    if not pocty:
        return zaklad
    casti = ", ".join(f"{pocet} {nazev}" for nazev, pocet in sorted(pocty.items()))
    return f"{zaklad} ({casti})"


@dataclass
class NovyZaznam:
    """Jeden radek logu, jak ho sestavil sber zmen - jeste neulozeny.

    Pojmenovana pole misto slovniku s textovymi klici: prohozene `objekt_id`
    a `akce_id` ani preklep v nazvu pole by u slovniku nikdo nezachytil
    (zaznam by se tise prirazil k jine akci), tady je zachyti pyright."""

    typ_cinnosti: str
    typ_objektu: str
    objekt_id: int | None
    akce_id: int | None
    popis: str
    detail: dict | None = None
    pocet: int = 1
    akce_nazev: str | None = None
    # "typ „jmeno“" bez vypisu zmen - `_sluc` z nej po sloucení sklada popis znovu.
    zaklad: str | None = None


def _zaznam_hromadny(typ_cinnosti: str, tabulka: str, akce_id: int | None, objekty: list):
    nazev_zakladni = NAZVY_OBJEKTU.get(tabulka, tabulka)
    if len(objekty) == 1:
        obj = objekty[0]
        popis = nazev_zakladni + nazev_v_uvozovkach(obj)
        return NovyZaznam(typ_cinnosti, tabulka, getattr(obj, "id", None), akce_id, popis)
    popis = f"{len(objekty)} × {nazev_zakladni}"
    return NovyZaznam(typ_cinnosti, tabulka, None, akce_id, popis, pocet=len(objekty))


def _skupiny_podle_tabulky_a_akce(objekty) -> dict[tuple, list]:
    """Seskupi objekty podle (tabulka, akce) - jeden zaznam logu na skupinu."""
    skupiny: dict[tuple, list] = {}
    for obj in objekty:
        skupiny.setdefault((obj.__tablename__, akce_id_objektu(obj)), []).append(obj)
    return skupiny


def _zaznamy_pridani(sezeni: Session) -> list[NovyZaznam]:
    """Pridani, seskupene po typu objektu a akci."""
    skupiny = _skupiny_podle_tabulky_a_akce(o for o in sezeni.new if loguje_se(o))
    return [
        _zaznam_hromadny(CINNOST_PRIDANI, tabulka, akce_id, objekty)
        for (tabulka, akce_id), objekty in skupiny.items()
    ]


def _zaznamy_uprav(sezeni: Session) -> list[NovyZaznam]:
    """Upravy, kazda samostatne s diffem poli."""
    vysledky: list[NovyZaznam] = []
    for obj in sezeni.dirty:
        if not loguje_se(obj) or obj in sezeni.deleted:
            continue
        zaklad, popis, detail = _popis_zmeny(sezeni, obj)
        if popis is None:
            continue
        vysledky.append(
            NovyZaznam(
                CINNOST_UPRAVA,
                obj.__tablename__,
                getattr(obj, "id", None),
                akce_id_objektu(obj),
                popis,
                detail,
                zaklad=zaklad,
            )
        )
    return vysledky


def _zaznamy_smazanych_akci(sezeni: Session) -> list[NovyZaznam]:
    """Smazani cele akce - jeden souhrnny zaznam, potomci se preskoci."""
    return [
        NovyZaznam(
            CINNOST_SMAZANI,
            "akce",
            akce.id,
            akce.id,
            _popis_smazane_akce(sezeni, akce),
            akce_nazev=akce.nazev,
        )
        for akce in (o for o in sezeni.deleted if isinstance(o, Akce))
    ]


def _zaznamy_smazani(sezeni: Session, mazane_akce_id: set) -> list[NovyZaznam]:
    """Ostatni smazani, seskupene; potomci prave mazane akce se preskoci."""
    skupiny = _skupiny_podle_tabulky_a_akce(
        obj
        for obj in sezeni.deleted
        if loguje_se(obj)
        and not isinstance(obj, Akce)
        and akce_id_objektu(obj) not in mazane_akce_id
    )
    return [
        _zaznam_hromadny(CINNOST_SMAZANI, tabulka, akce_id, objekty)
        for (tabulka, akce_id), objekty in skupiny.items()
    ]


def _dopln_nazvy_akci(sezeni: Session, vysledky: list[NovyZaznam]) -> None:
    for zaznam in vysledky:
        if zaznam.akce_id is not None and zaznam.akce_nazev is None:
            zaznam.akce_nazev = nazev_akce(sezeni, zaznam.akce_id)


def _zaznamy_ze_zmen(sezeni: Session) -> list[NovyZaznam]:
    """Sestavi seznam zaznamu k zapsani z toho, co se prave zmenilo
    (`sezeni.new` / `sezeni.dirty` / `sezeni.deleted`). Vola se z
    `after_flush`, kdy uz maji nove objekty prideleno `id`, ale zmeny jeste
    nejsou zapsane do zadneho zaznamu."""
    mazane_akce_id = {obj.id for obj in sezeni.deleted if isinstance(obj, Akce)}
    vysledky = [
        *_zaznamy_pridani(sezeni),
        *_zaznamy_uprav(sezeni),
        *_zaznamy_smazanych_akci(sezeni),
        *_zaznamy_smazani(sezeni, mazane_akce_id),
    ]
    _dopln_nazvy_akci(sezeni, vysledky)
    return vysledky


def _zaznamy_ke_slouceni(
    sezeni: Session, fronta: list[NovyZaznam], hranice: dt.datetime, aktor_id
) -> dict[tuple[str | None, int | None], ZaznamCinnosti]:
    """Nejnovejsi nedavny zaznam o uprave pro kazdy objekt z fronty, jednim dotazem.

    Hromadne ulozeni (vsechny radky Uctenek, roztrideni do slozek) meni desitky
    objektu najednou; dotaz na kazdy z nich zvlast by s kazdym ulozenim prodluzoval
    cekani. Objekt je dvojice (tabulka, id): ucetenka c. 1 a slozka c. 1 jsou ruzne
    veci, proto se zaznamy parují podle obou."""
    hledane = {
        (data.typ_objektu, data.objekt_id)
        for data in fronta
        if data.typ_cinnosti == CINNOST_UPRAVA and data.objekt_id is not None
    }
    if not hledane:
        return {}
    kandidati = sezeni.scalars(
        select(ZaznamCinnosti)
        .where(
            ZaznamCinnosti.uzivatel_id == aktor_id,
            ZaznamCinnosti.typ_cinnosti == CINNOST_UPRAVA,
            ZaznamCinnosti.typ_objektu.in_({typ for typ, _ in hledane}),
            ZaznamCinnosti.objekt_id.in_({objekt_id for _, objekt_id in hledane}),
            ZaznamCinnosti.do >= hranice,
        )
        .order_by(ZaznamCinnosti.do.desc(), ZaznamCinnosti.id.desc())
    )
    nejnovejsi: dict[tuple[str | None, int | None], ZaznamCinnosti] = {}
    for zaznam in kandidati:
        # Zaznamy jdou od nejnovejsiho, takze prvni nalezeny pro objekt vyhrava.
        nejnovejsi.setdefault((zaznam.typ_objektu, zaznam.objekt_id), zaznam)
    return nejnovejsi


def _sluc(existujici: ZaznamCinnosti, data: NovyZaznam, cas: dt.datetime) -> None:
    """Spoji novou zmenu do existujiciho zaznamu.

    U kazdeho pole se drzi PUVODNI "pred" (z prvniho zaznamu v okne) a
    prubezne se prepisuje "po" - jinak by po treti uprave castky zustalo
    videt jen posledni mezikrok, ne skutecny rozdil od zacatku."""
    existujici.do = cas
    existujici.pocet += 1

    if data.detail is None:
        # Osobni udaje (nebo zmena bez detailu, napr. typ objektu ho nema) -
        # detail se neslucuje, popis jen ukaze posledni stav.
        existujici.popis = data.popis
        return

    puvodni_detail: dict = json.loads(existujici.detail_json) if existujici.detail_json else {}
    for pole, zmena in data.detail.items():
        if zmena.get("skryto"):
            puvodni_detail[pole] = {"skryto": True}
            continue
        puvodni = puvodni_detail.get(pole)
        if puvodni and not puvodni.get("skryto"):
            zmena = {"pred": puvodni["pred"], "po": zmena["po"]}
        if zmena["pred"] == zmena["po"]:
            puvodni_detail.pop(pole, None)
        else:
            puvodni_detail[pole] = zmena

    existujici.detail_json = (
        json.dumps(puvodni_detail, ensure_ascii=False) if puvodni_detail else None
    )
    zaklad = data.zaklad or ""
    existujici.popis = f"{zaklad}: {_vykresli_casti(puvodni_detail)}" if puvodni_detail else zaklad


def _zapis_frontu(sezeni: Session, fronta: list[NovyZaznam], aktor) -> None:
    cas = ted()
    hranice = cas - OKNO_SLOUCENI
    ke_slouceni = _zaznamy_ke_slouceni(sezeni, fronta, hranice, aktor.id)
    for data in fronta:
        existujici = ke_slouceni.get((data.typ_objektu, data.objekt_id))
        if data.typ_cinnosti != CINNOST_UPRAVA:
            existujici = None
        if existujici is not None:
            _sluc(existujici, data, cas)
            continue
        sezeni.add(
            ZaznamCinnosti(
                uzivatel_id=aktor.id,
                uzivatel_jmeno=aktor.jmeno,
                typ_cinnosti=data.typ_cinnosti,
                typ_objektu=data.typ_objektu,
                objekt_id=data.objekt_id,
                akce_id=data.akce_id,
                akce_nazev=data.akce_nazev,
                popis=data.popis,
                detail_json=json.dumps(data.detail, ensure_ascii=False) if data.detail else None,
                pocet=data.pocet,
                od=cas,
                do=cas,
            )
        )


@event.listens_for(Session, "after_flush")
def _posbirej(sezeni: Session, _kontext) -> None:
    aktor = sezeni.info.get("aktor")
    if aktor is None:
        return
    try:
        fronta = sezeni.info.setdefault("_log_fronta", [])
        fronta.extend(_zaznamy_ze_zmen(sezeni))
    except Exception:
        log.exception("Log činnosti: chyba při sběru změn")
        if PRISNE:
            raise


@event.listens_for(Session, "after_flush_postexec")
def _zapis_posbirane(sezeni: Session, _kontext) -> None:
    aktor = sezeni.info.get("aktor")
    fronta = sezeni.info.pop("_log_fronta", None)
    if aktor is None or not fronta:
        return
    try:
        _zapis_frontu(sezeni, fronta, aktor)
    except Exception:
        log.exception("Log činnosti: chyba při zápisu")
        if PRISNE:
            raise
