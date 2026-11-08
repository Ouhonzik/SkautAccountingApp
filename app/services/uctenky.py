"""Pravidla uctenek (skenu): souhrn po kategoriich, rozpis uctenky, parovani
priloh a prevzeti vysledku cteni z AI.

Jsou to ucetni pravidla, ktera musi sedet s pokladni knihou a s tiskopisy
(PPD/VPD), proto nepatri do HTTP routy. Routa `app/routes/skeny.py` jen prevede
formular na hodnoty a zavola tuhle sluzbu - z jedne cesty (formular) i z druhe
(AI) tak plati tatez pravidla.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import DRUH_PALIVO, MIN_CASTI_ROZDELENI, CastSkenu, Sken
from app.services import cestaky, ocr
from app.services.ciselniky import podle_klice

# Jedna cast rozpisu: (id kategorie, castka, popis).
CastRozpisu = tuple[int | None, Decimal, str | None]


def statistika(skeny: list[Sken]) -> dict:
    """Cisla do prehledoveho bloku nad tabulkou uctenek.

    Prilohy se do castek nepocitaji - stejne jako je nepocita slozka.
    Palivova uctenka u cestaku jen dokladá cenu za litr, vydaj to neni.

    Rozdelena uctenka pridava kazde sve kategorii jen jeji dil (`Sken.podily`).
    Kdyby se cela pripsala hlavni kategorii, ukazoval by souhrn na obrazovce
    jina cisla nez rozpis na tiskopisu VPD.
    """
    s_radkem = [s for s in skeny if not s.je_priloha]
    podle_kategorie: dict[str, dict] = defaultdict(lambda: {"pocet": 0, "soucet": Decimal("0.00")})
    for sken in s_radkem:
        for kategorie, castka in sken.podily:
            nazev = kategorie.nazev if kategorie else "Bez kategorie"
            podle_kategorie[nazev]["pocet"] += 1
            podle_kategorie[nazev]["soucet"] += castka

    return {
        "celkem": len(skeny),
        "prilohy": len(skeny) - len(s_radkem),
        "castka": sum((s.castka for s in s_radkem if s.castka is not None), Decimal("0.00")),
        "bez_castky": len([s for s in s_radkem if s.castka is None]),
        "rozpad_nesedi": len([s for s in s_radkem if not s.rozdeleni_sedi]),
        "zkontrolovano": len([s for s in skeny if s.zkontrolovano]),
        "kategorie": sorted(
            ({"nazev": k, **v} for k, v in podle_kategorie.items()),
            key=lambda r: (-r["soucet"], r["nazev"]),
        ),
    }


def nastav_rozdeleni(sken: Sken, casti: Sequence[CastRozpisu]) -> None:
    """Prepise rozpis uctenky po kategoriich zadanymi castmi.

    Spolecne pro rozpis od AI i pro formular. Stary rozpis se vzdycky zahodi -
    opakovane cteni tehoz skenu by jinak casti zdvojilo. Uklada se az od
    `MIN_CASTI_ROZDELENI` casti: jedina cast je tataz informace jako kategorie
    uctenky, jen zapsana podruhe.

    Soucet se schvalne nedorovnava. Kdyz se castky nesecou s uctenkou, radek se
    na obrazovce ohlasi a opravi ho clovek - dopocitat rozdil za nej by znamenalo
    tise menit ucetni cislo.
    """
    sken.casti.clear()
    if len(casti) < MIN_CASTI_ROZDELENI:
        return
    for poradi, (kategorie_id, castka, popis) in enumerate(casti):
        sken.casti.append(
            CastSkenu(
                kategorie_id=kategorie_id,
                castka=castka,
                popis=(popis or "").strip() or None,
                poradi=poradi,
            )
        )


def sparuj_prilohy(podle_id: dict[int, Sken], parovani: dict[int, str]) -> int:
    """Priradi prilohy k uctenkam, ke kterym patri. Vraci, kolik jich pritom
    prebehlo do jine slozky.

    Deje se to az po ulozeni vsech radku: jedno odeslani muze prepnout i tu
    uctenku, na kterou se priloha vaze, takze rozhodnout smi az vysledny stav.
    """
    presunuto = 0
    for sken_id, hodnota in parovani.items():
        sken = podle_id[sken_id]
        cil = podle_id.get(int(hodnota)) if hodnota.isascii() and hodnota.isdigit() else None
        # Vazba dava smysl jen u prilohy a jen na uctenku s vlastnim radkem.
        # Priloha na priloze ani sama na sobe by byla kruh bez dokladu.
        if not sken.je_priloha or cil is None or cil is sken or cil.je_priloha:
            sken.priloha_k_id = None
            continue
        sken.priloha_k_id = cil.id
        # Priloha se lepi na tentyz papir jako jeji uctenka, takze patri i do
        # teze slozky - jinak by skoncila v jine prihradce ZIPu.
        if sken.slozka_id != cil.slozka_id:
            sken.slozka_id = cil.slozka_id
            presunuto += 1
    return presunuto


def _castka(hodnota: float) -> Decimal:
    """Castka od AI jako Decimal; `float` se prevadi pres text, aby z 39.9
    nebylo 39.89999..."""
    return Decimal(str(hodnota))


def _datum(text: str | None) -> dt.date | None:
    """Datum od AI (RRRR-MM-DD). Nesmysl je stejne jako prazdne pole 'nevyplneno'."""
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def prevezmi_vysledek_ai(sezeni: Session, sken: Sken, vysledek: ocr.VysledekCteni) -> bool:
    """Zapise do skenu, co precetla AI. Vraci True, kdyz jde o palivovou uctenku.

    Palivova uctenka u cestaku jen dokladá cenu za litr - vydaj to neni, proto
    se rovnou oznaci jako priloha. Volajici to ohlasi cloveku, protoze
    priloha se nikde nepocita a tichou chybu AI by vydaj vypadl z knihy.

    Cena za litr z prectene uctenky se promita do cestaku, ke kteremu je uctenka
    pripnuta - volajici proto musi mit pred volanim `db.zamkni_zapis`
    a po volani dela `commit`.
    """
    puvodni_cena_za_litr = sken.cena_za_litr
    sken.druh = vysledek.druh
    sken.obchodnik = vysledek.obchodnik
    sken.datum = _datum(vysledek.datum)
    sken.castka = _castka(vysledek.castka_celkem) if vysledek.castka_celkem else None
    sken.cena_za_litr = _castka(vysledek.cena_za_litr) if vysledek.cena_za_litr else None
    sken.nazev = vysledek.navrh_nazvu or sken.nazev
    sken.poznamka = vysledek.poznamka
    sken.jistota = vysledek.jistota
    sken.rozpis_json = (
        json.dumps([p.model_dump() for p in vysledek.rozpis], ensure_ascii=False)
        if vysledek.rozpis
        else None
    )
    kategorie = podle_klice(sezeni, vysledek.navrh_kategorie)
    if kategorie:
        sken.kategorie_id = kategorie.id
    nastav_rozdeleni(
        sken,
        [
            (
                # Neznamy klic nechavame bez kategorie: radeji viditelne chybejici
                # nez tise zahozena castka.
                kat.id if (kat := podle_klice(sezeni, cast.kategorie)) else None,
                _castka(cast.castka),
                cast.co,
            )
            for cast in vysledek.rozdeleni
        ],
    )
    # AI cenu nepřečetla -> cesťáku zůstane opsaná hodnota, jinak by mu tiše zmizela.
    if sken.cena_za_litr is not None:
        cestaky.promit_cenu_za_litr_z_uctenky(sezeni, sken, puvodni_cena_za_litr)
    if vysledek.druh == DRUH_PALIVO:
        sken.je_priloha = True
        return True
    return False
