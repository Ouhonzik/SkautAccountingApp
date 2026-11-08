"""Ciselniky, ktere se edituji v aplikaci, ne v kodu: kategorie, lide a vozidla.

Ani jeden ciselnik nema vychozi sadu - nova databaze zacina prazdna
(rozhodnuto s Honzou 12. 9. 2026). Kategorie si stredisko zaklada samo na
strance /kategorie a AI vybira jen z tech, ktere tam opravdu jsou; sada
napsana v kodu by jen predstirala, ze vi, jak stredisko uctuje. Do te doby,
nez si Honza neco zalozi, AI kategorii nenavrhuje - `pokyny_s_kategoriemi`
s prazdnym seznamem pocita.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Akce, CastSkenu, Cestak, Clovek, Kategorie, Prijem, Sken, Vozidlo
from app.models.konstanty import (
    KLIC_VECNE_DARY,
    PEVNE_KLICE_KATEGORII,
    STARE_NAZVY_PEVNYCH_KATEGORII,
)


def kategorie_daru(sezeni: Session) -> Kategorie | None:
    """Kategorie vecnych daru, nebo None, kdyz ji Honza v ciselniku nema.

    Uctenky, ktere pod ni spadaji, se na obrazovce Obdarovanych rozepisuji na
    jednotlive veci a rozdavaji lidem. Hleda se jen podle klice (`KLIC_VECNE_DARY`,
    bez ohledu na velikost pismen), nikdy podle nazvu: kategorie s timto klicem je
    pevna (`Kategorie.pevna`), takze ji nejde smazat ani prejmenovat a hledani se
    nemuze rozbit. Kdyz se nenajde nic, obrazovka to rekne nahlas - hadat
    kategorii podle niceho je horsi nez mlcet.
    """
    return sezeni.scalar(select(Kategorie).where(func.lower(Kategorie.klic) == KLIC_VECNE_DARY))


def podle_klice(sezeni: Session, klic: str | None) -> Kategorie | None:
    """Najde kategorii podle klice, a kdyz zadny nema, podle nazvu.

    AI vraci to, co ji dal `nabidka_pro_ai` - a to je u kategorie bez klice
    jeji nazev. Proto se hleda oboji.

    Porovnava se az tady v Pythonu, ne v databazi. `lower()` v SQLite umi jen
    anglickou abecedu - z "Údržba" mu zustane velke "Ú", kdezto Python udela
    "údržba". Nazev s ceskym velkym pismenem by se tak nikdy nenasel a uctenka
    by tise zustala bez kategorie.
    """
    if not klic:
        return None
    hledane = klic.strip().lower()
    if not hledane:
        return None
    kategorie = vsechny_kategorie(sezeni)
    for jedna in kategorie:
        if jedna.klic and jedna.klic.strip().lower() == hledane:
            return jedna
    for jedna in kategorie:
        if jedna.nazev.strip().lower() == hledane:
            return jedna
    return None


def nabidka_pro_ai(sezeni: Session) -> list[tuple[str, str]]:
    """Seznam kategorii pro pokyny AI: (oznaceni, nazev).

    Oznaceni je klic, a kdyz kategorie zadny nema, jeji nazev - AI musi mit
    co vratit i u kategorie, kterou si Honza zalozil bez klice. Zpatky se to
    pak najde pres `podle_klice`.
    """
    return [(k.klic or k.nazev, k.nazev) for k in vsechny_kategorie(sezeni)]


def vsechny_kategorie(sezeni: Session) -> list[Kategorie]:
    return list(sezeni.scalars(select(Kategorie).order_by(Kategorie.poradi, Kategorie.nazev)))


def vsichni_lide(sezeni: Session) -> list[Clovek]:
    return list(sezeni.scalars(select(Clovek).order_by(Clovek.poradi, Clovek.jmeno)))


def vsechna_vozidla(sezeni: Session) -> list[Vozidlo]:
    return list(sezeni.scalars(select(Vozidlo).order_by(Vozidlo.poradi, Vozidlo.nazev)))


def dalsi_poradi(poradi: Iterable[int], zacatek: int = 0) -> int:
    """Cislo za nejvyssim z `poradi`; u prazdneho seznamu `zacatek`.

    Nova polozka jde vzdy na konec, takze dira v poradi se nezaplnuje a stavajici
    data se nemeni. Ciselniky (lide, kategorie, vozidla) cisluji od 1, radky
    na akcich od 0 - proto `zacatek`.
    """
    return max(poradi, default=zacatek - 1) + 1


@dataclass(frozen=True)
class PouzitiCloveka:
    """Kolikrat je clovek pouzity na dokladech. Cokoli nenulove brani smazani."""

    uctenky: int = 0
    pokladnik: int = 0
    cestujici: int = 0
    prijmy: int = 0

    @property
    def celkem(self) -> int:
        return self.uctenky + self.pokladnik + self.cestujici + self.prijmy


def _spocitej(sezeni: Session, sloupec) -> dict[int, int]:
    radky = sezeni.execute(
        select(sloupec, func.count()).where(sloupec.is_not(None)).group_by(sloupec)
    ).tuples()
    return {klic: pocet for klic, pocet in radky}


def pouziti_lidi(sezeni: Session) -> dict[int, PouzitiCloveka]:
    """Pro kazdeho pouziteho cloveka, kde vsude jeho jmeno na dokladech visi.

    Pokladnik akce a cestujici na cestaku nejsou uctenky, ale jmeno se z nich
    tiskne na papir - smazat takoveho cloveka nejde, jinak by z dokladu zmizelo.
    Clovek bez jedine vazby ve slovniku neni. Nova vazba na cloveka se pridava
    jen sem.
    """
    uctenky = _spocitej(sezeni, Sken.clovek_id)
    pokladnik = _spocitej(sezeni, Akce.pokladnik_id)
    cestujici = _spocitej(sezeni, Cestak.cestujici_id)
    prijmy = _spocitej(sezeni, Prijem.clovek_id)
    vsichni = uctenky.keys() | pokladnik.keys() | cestujici.keys() | prijmy.keys()
    return {
        clovek_id: PouzitiCloveka(
            uctenky.get(clovek_id, 0),
            pokladnik.get(clovek_id, 0),
            cestujici.get(clovek_id, 0),
            prijmy.get(clovek_id, 0),
        )
        for clovek_id in vsichni
    }


def pouziti_kategorii(sezeni: Session) -> dict[int, int]:
    """Kolik skenu kategorii pouziva - i tam, kde visi jen v rozpisu rozdelene
    uctenky (`CastSkenu`); jinak by slo smazat "Material" schovany na nakupu
    jidla a z rozpisu by tise zmizela castka."""
    pouziti = _spocitej(sezeni, Sken.kategorie_id)
    for kategorie_id, pocet in _spocitej(sezeni, CastSkenu.kategorie_id).items():
        pouziti[kategorie_id] = pouziti.get(kategorie_id, 0) + pocet
    return pouziti


def pouziti_vozidel(sezeni: Session) -> dict[int, int]:
    """Na kolika cestovnich prikazech je vozidlo."""
    return _spocitej(sezeni, Cestak.vozidlo_id)


def proc_nejde_smazat(sezeni: Session, objekt: Clovek | Kategorie | Vozidlo) -> str | None:
    """Ceska hlaska, proc se polozka ciselniku smazat nesmi, nebo None.

    Jedno misto pro pravidlo "nepouzity na zadnem dokladu" - routa mazani i
    tlacitko v prehledu z nej vychazeji, takze se nemuzou rozejit.
    """
    if isinstance(objekt, Clovek):
        pouziti = pouziti_lidi(sezeni).get(objekt.id, PouzitiCloveka())
        if pouziti.uctenky:
            return f"Nemažu — je u {pouziti.uctenky} účtenek. Nejdřív u nich vyber někoho jiného."
        if pouziti.pokladnik:
            return (
                f"Nemažu — je pokladníkem {pouziti.pokladnik} akcí "
                "a jeho jméno se tiskne na doklady."
            )
        if pouziti.cestujici:
            return (
                f"Nemažu — je cestujícím na {pouziti.cestujici} "
                "cestovních příkazech a jeho jméno se tiskne na papír."
            )
        if pouziti.prijmy:
            return (
                f"Nemažu — vybral {pouziti.prijmy} příjmů a jeho jméno se tiskne "
                "na příjmové doklady. Nejdřív u nich vyber někoho jiného."
            )
    elif isinstance(objekt, Kategorie):
        if objekt.pevna:
            return (
                f"Kategorii „{objekt.nazev}“ nemažu — je pevná, patří k základní sadě "
                "kategorií a aplikace na ni spoléhá."
            )
        if pocet := pouziti_kategorii(sezeni).get(objekt.id):
            return (
                f"Kategorii „{objekt.nazev}“ nemažu — používá ji {pocet} skenů. "
                "Nejdřív je přeřaď jinam."
            )
    elif pocet := pouziti_vozidel(sezeni).get(objekt.id):
        return (
            f"„{objekt.nazev}“ nemažu — je na {pocet} cestovních příkazech "
            "a jeho SPZ se tiskne na papír."
        )
    return None


def proc_nejde_prejmenovat(kategorie: Kategorie) -> str | None:
    """Ceska hlaska, proc se kategorie prejmenovat nesmi, nebo None."""
    if kategorie.pevna:
        return f"Kategorii „{kategorie.nazev}“ nepřejmenuju — je pevná, aplikace na ni spoléhá."
    return None


def proc_nejde_zmenit_klic(kategorie: Kategorie) -> str | None:
    """Ceska hlaska, proc se klic kategorie menit nesmi, nebo None. Formular klic
    neupravuje, tohle hlida prime POST."""
    if kategorie.pevna:
        return f"Klíč kategorie „{kategorie.nazev}“ neměním — je pevná, aplikace na ni spoléhá."
    return None


def pevny_klic_podle_stareho_nazvu(nazev: str | None) -> str | None:
    """Klic pevne kategorie, ke ktere patri dosavadni nazev (Jidlo, Material...), nebo None.

    Pro obnovu ze zalohy: stara zaloha nese kategorii "Jídlo", ktera se v databazi
    jmenuje "potraviny a stravné". Tolerantni k velikosti pismen a diakritice; "Věcné
    dary" smi mit v zavorce dodatek, jako ho mela pevna kategorie z K-219."""
    if not nazev:
        return None
    tvar = " ".join(_bez_diakritiky(nazev).split())
    if klic := STARE_NAZVY_PEVNYCH_KATEGORII.get(tvar):
        return klic
    if tvar.startswith("vecne dar"):
        return KLIC_VECNE_DARY
    return None


def _bez_diakritiky(text: str) -> str:
    """Maly text bez carek a hacku: "Věcné dary" -> "vecne dary" (tvar pevneho klice)."""
    rozlozeny = unicodedata.normalize("NFKD", text.strip().casefold())
    return "".join(z for z in rozlozeny if not unicodedata.combining(z))


def _je_pevny_klic(klic: str | None) -> bool:
    return klic is not None and klic.strip().casefold() in PEVNE_KLICE_KATEGORII


def proc_nazev_koliduje(nazev: str, klic: str | None, kategorie: Iterable[Kategorie]) -> str | None:
    """Ceska hlaska u pole Nazev, kdyz uz nazev patri jine kategorii, jinak None.

    Obycejne "uz existuje" nestaci tam, kde jde o pevnou kategorii: clovek, ktery
    chce zalozit spravnou "Vecne dary" s pevnym klicem a narazi na starou obycejnou
    se stejnym nazvem (bez klice, pevna se z ni nedela), nevi, co s tim.
    """
    hledany = klic_nazvu(nazev)
    stejna = next((k for k in kategorie if klic_nazvu(k.nazev) == hledany), None)
    if stejna is None:
        return None
    if stejna.pevna:
        return f"Kategorie „{stejna.nazev}“ už existuje a je pevná — nezakládej ji znovu."
    if _je_pevny_klic(klic):
        return (
            f"Název „{stejna.nazev}“ už má obyčejná kategorie bez pevného klíče a pevnou z ní "
            "udělat nejde. Přejmenuj ji v tabulce níže (třeba na „"
            f"{stejna.nazev} – stará“), pak založ tuhle znovu a účtenky přeřaď do nové."
        )
    return f"Kategorie „{nazev}“ už existuje."


def proc_nazev_je_vyhrazen(
    nazev: str, klic: str | None, kategorie: Iterable[Kategorie], *, prejmenovani: bool = False
) -> str | None:
    """Hlaska, kdyz se obycejna kategorie jmenuje jako pevna, ktera jeste neexistuje.

    "Vecne dary" bez pevneho klice by byla obycejna kategorie, ze ktere appka dary
    necte, a zaroven by zabrala nazev, takze by se pevna pod nim uz zalozit nedala.
    Kdyz pevna kategorie uz v ciselniku je (pod jakymkoli nazvem), obycejna se
    stejnym nazvem nic nerozbije a nechava se, kolize nazvu se resi jinde.
    """
    vyhrazeny = _bez_diakritiky(nazev)
    # Jen "vecne dary": ostatni pevne kategorie jsou v ciselniku vzdy (zaklada je migrace).
    if vyhrazeny != KLIC_VECNE_DARY or _je_pevny_klic(klic):
        return None
    if any(k.pevna and (k.klic or "").strip().casefold() == vyhrazeny for k in kategorie):
        return None
    if prejmenovani:
        return (
            f"Na „{nazev}“ kategorii nepřejmenuju — tak se jmenuje pevná kategorie pro věcné "
            "dary, a přejmenováním by pevná nevznikla. Zvol jiný název."
        )
    return (
        f"Název „{nazev}“ patří pevné kategorii, ze které appka čte věcné dary z účtenek. "
        f"Vyplň vedle klíč „{KLIC_VECNE_DARY}“, nebo zvol jiný název — bez klíče by to byla "
        "obyčejná kategorie a pevnou pod tímto názvem by pak nešlo založit."
    )


def klic_nazvu(nazev: str) -> str:
    """Tvar nazvu pro porovnani duplicit: bez okolnich mezer a bez rozdilu velkych
    a malych pismen. Porovnava se v Pythonu, ne v databazi - `lower()` v SQLite
    umi jen anglickou abecedu (viz `podle_klice`)."""
    return nazev.strip().casefold()


def clovek_podle_jmena(lide: Iterable[Clovek], jmeno: str) -> Clovek | None:
    """Clovek z ciselniku, jehoz jmeno se shoduje s rucne psanym textem, nebo None.

    Bez rozdilu velkych a malych pismen a mezer (i uvnitr). Shoduji-li se dva
    lide, text je dvojznacny a nevybere se zadny - radsi nechat text, nez hadat.
    Stejne pravidlo pouziva migrace, ktera prevedla stare "kdo vybral" na cloveka.
    """
    hledane = " ".join(jmeno.split()).casefold()
    shody = [c for c in lide if " ".join(c.jmeno.split()).casefold() == hledane]
    return shody[0] if len(shody) == 1 else None


def uz_existuje(nazev: str, existujici: Iterable[str | None]) -> bool:
    """Je `nazev` mezi `existujici`, bez ohledu na velikost pismen?"""
    hledany = klic_nazvu(nazev)
    return any(o is not None and klic_nazvu(o) == hledany for o in existujici)


def prejmenuj_bez_kolizi(polozky: Iterable, nove: dict[int, str], atribut: str) -> list[str]:
    """Prejmenuje polozky podle `nove` (id -> novy nazev) a vrati nazvy, ktere
    se prejmenovat nesmely, protoze uz patri jine polozce.

    Hromadne ulozeni tabulky by na unikatnim sloupci spadlo na chybu 500 a
    zahodilo by i vsechny ostatni upravy z formulare. Kolidujici radek proto
    zustane pod starym nazvem a zbytek se ulozi. Kolize je s kazdym jinym
    radkem, jak je v databazi (i kdyby ten radek prave dostaval jiny nazev -
    prohazeni dvou nazvu se tak nikdy nezkousi, protoze poradi UPDATE
    v databazi nema zaruku) i s radkem, ktery uz v tomhle formulari
    dostal stejny novy nazev.
    """
    polozky = list(polozky)
    puvodni = {p.id: klic_nazvu(getattr(p, atribut)) for p in polozky}
    zabrane_novymi: set[str] = set()
    odmitnute: list[str] = []
    for polozka in polozky:
        novy = nove.get(polozka.id, "").strip()
        if not novy or novy == getattr(polozka, atribut):
            continue
        klic = klic_nazvu(novy)
        drzi_jiny = any(k == klic for id_, k in puvodni.items() if id_ != polozka.id)
        if drzi_jiny or klic in zabrane_novymi:
            odmitnute.append(novy)
            continue
        zabrane_novymi.add(klic)
        setattr(polozka, atribut, novy)
    return odmitnute
