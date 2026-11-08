"""Zaloha akce - jeden ZIP se vsim.

Slouzi ke dvema vecem najednou: jako zaloha pro pripad, ze by selhal server,
a jako archiv uzavrene akce. Krome skenu a Excelu obsahuje i data.json, ze
ktereho jde cela akce znovu OBNOVIT (app/services/obnova_zalohy.py) - proto
`data_akce` uklada syrova data (co je v jake tabulce), ne jen hotovou
pokladni knihu k zobrazeni.

Ucastnici a obdarovani (osobni udaje deti) jsou od verze 2 v zaloze cele,
ne jen jako pocet - jinak by je obnova nemohla vratit. Honza si toho je
vedom (12. 9. 2026) - proto tlacitko stazeni v `akce_detail.html` na to
upozornuje.
"""

from __future__ import annotations

import json
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import IO

from app.models import Akce, Obdarovany, Sken, Slozka, Ucastnik
from app.services import excel, excel_pokladni_kniha, zip_export
from app.services.soubory import bezpecny_nazev

# Verze formatu data.json. Zvysi se pokazde, kdyz obnova (obnova_zalohy.py)
# zacne potrebovat dalsi/jine pole. Zaloha bez tohoto klice (stary format)
# nebo s cislem, ktere obnova nezna (`PODPOROVANE_VERZE_ZALOHY` v
# obnova_zalohy.py), je nekompatibilni - obnova ji rovnou odmitne, misto aby
# zkousela hadat, co v ni chybi. Starsi verze, ktere obnova umi dal cist (nove
# pole je nepovinne), zustavaji v tom seznamu, aby se stare zalohy daly obnovit.
VERZE_ZALOHY = 6
# Verze 5: ucastnik uz nenese `vratka` - je to soucet radku knihy s jeho vazbou
#. Zaloha verze 3 a 4 ji jeste obsahuje; obnova ji ignoruje.
# Verze 6: prijem nese `clovek` (jmeno z ciselniku lidi, K-035). Starsi zalohy maji
# jen volny text `kdo`; obnova ho podle jmena priradi cloveku, ktery uz v ciselniku je.


def _prevod(hodnota):
    if isinstance(hodnota, Decimal):
        return str(hodnota)
    if isinstance(hodnota, (date, datetime)):
        return hodnota.isoformat()
    raise TypeError(f"Nevim, jak ulozit {type(hodnota)}")


def _pouzite_kategorie(akce: Akce) -> list:
    """Kategorie, na ktere akce nekde odkazuje - kategorie je sdileny
    ciselnik pro vsechny akce, do zalohy jedne akce nepatri cely."""
    kategorie = {}
    for slozka in akce.slozky:
        if slozka.kategorie is not None:
            kategorie[slozka.kategorie.id] = slozka.kategorie
    for sken in akce.skeny:
        if sken.kategorie is not None:
            kategorie[sken.kategorie.id] = sken.kategorie
        for cast in sken.casti:
            if cast.kategorie is not None:
                kategorie[cast.kategorie.id] = cast.kategorie
    return list(kategorie.values())


def _pouziti_lide(akce: Akce) -> list:
    """Lide (ciselnik plátcu/pokladniku), na ktere akce odkazuje - stejny
    duvod jako u kategorii."""
    lide = {}
    if akce.pokladnik_clovek is not None:
        lide[akce.pokladnik_clovek.id] = akce.pokladnik_clovek
    for slozka in akce.slozky:
        if slozka.clovek is not None:
            lide[slozka.clovek.id] = slozka.clovek
    for sken in akce.skeny:
        if sken.clovek is not None:
            lide[sken.clovek.id] = sken.clovek
    for cestak in akce.cestaky:
        if cestak.cestujici is not None:
            lide[cestak.cestujici.id] = cestak.cestujici
    for prijem in akce.prijmy:
        if prijem.clovek is not None:
            lide[prijem.clovek.id] = prijem.clovek
    return list(lide.values())


def _pouzita_vozidla(akce: Akce) -> list:
    """Vozidla, na ktera akce odkazuje pres sve cestaky. Stejny duvod jako
    u kategorii - vozidlo je sdileny ciselnik, do zalohy jedne akce nepatri
    cely. Fotka technickeho prukazu se do zalohy akce nedava vubec: je to
    majetek ciselniku, ne akce."""
    vozidla = {}
    for cestak in akce.cestaky:
        if cestak.vozidlo is not None:
            vozidla[cestak.vozidlo.id] = cestak.vozidlo
    return list(vozidla.values())


# Jedna funkce na tabulku. Obnova (obnova_zalohy.py) ma stejne funkce
# `_obnov_*` ve stejnem poradi - nove pole se pridava na obou mistech
# a do modelu `Zaloha*` tamtez, jinak by se pri obnove tise ztratilo.


def _akce(akce: Akce) -> dict:
    return {
        "nazev": akce.nazev,
        "datum_vyuctovani": akce.datum_vyuctovani,
        "datum_od": akce.datum_od,
        "datum_do": akce.datum_do,
        "stav": akce.stav,
        "prefix_dokladu": akce.prefix_dokladu,
        "pokladnik": akce.pokladnik,
    }


def _kategorie(akce: Akce) -> list[dict]:
    return [
        {"nazev": k.nazev, "klic": k.klic, "poradi": k.poradi} for k in _pouzite_kategorie(akce)
    ]


def _lide(akce: Akce) -> list[dict]:
    return [
        {"jmeno": c.jmeno, "poradi": c.poradi, "adresa": c.adresa, "kontakt": c.kontakt}
        for c in _pouziti_lide(akce)
    ]


def _vozidla(akce: Akce) -> list[dict]:
    return [
        {"nazev": v.nazev, "spz": v.spz, "spotreba": v.spotreba, "poradi": v.poradi}
        for v in _pouzita_vozidla(akce)
    ]


def _slozky(slozky: list[Slozka]) -> list[dict]:
    return [
        {
            "nazev": slozka.nazev,
            "kategorie": slozka.kategorie.nazev if slozka.kategorie else None,
            "clovek": slozka.clovek.jmeno if slozka.clovek else None,
            "ucel_platby": slozka.ucel_platby,
            "poradi": slozka.poradi,
        }
        for slozka in slozky
    ]


def _skeny(
    skeny: list[Sken],
    cesty_v_zipu: dict[int, str],
    index_sken: dict[int | None, int],
    index_slozky: dict[int | None, int],
) -> list[dict]:
    return [
        {
            # None u skenu, jehoz soubor uz v dobe zalohovani chybel na
            # disku - `sestav_zalohu` ho pak nema jak do ZIPu zapsat.
            "soubor_v_zipu": cesty_v_zipu.get(sken.id),
            "puvodni_nazev": sken.puvodni_nazev,
            "pocet_stranek": sken.pocet_stranek,
            "nazev": sken.nazev,
            "druh": sken.druh,
            "obchodnik": sken.obchodnik,
            "datum": sken.datum,
            "castka": sken.castka,
            "cena_za_litr": sken.cena_za_litr,
            "clovek": sken.clovek.jmeno if sken.clovek else None,
            "kategorie": sken.kategorie.nazev if sken.kategorie else None,
            "je_priloha": sken.je_priloha,
            "priloha_k": index_sken.get(sken.priloha_k_id),
            "zkontrolovano": sken.zkontrolovano,
            "varovani_ztiseno": sken.varovani_ztiseno,
            "jistota": sken.jistota,
            "poznamka": sken.poznamka,
            "rozpis_json": sken.rozpis_json,
            "poradi": sken.poradi,
            "poradi_v_knize": sken.poradi_v_knize,
            "slozka": index_slozky.get(sken.slozka_id),
            "casti": [
                {
                    "kategorie": cast.kategorie.nazev if cast.kategorie else None,
                    "castka": cast.castka,
                    "popis": cast.popis,
                    "poradi": cast.poradi,
                }
                for cast in sken.casti
            ],
        }
        for sken in skeny
    ]


def _prijmy(akce: Akce) -> list[dict]:
    return [
        {
            "popis": p.popis,
            "castka": p.castka,
            # Jmeno z ciselniku (`lide` v zaloze nese jeho udaje); `kdo` je stary text
            # u prijmu, jehoz jmeno se v ciselniku nenaslo.
            "clovek": p.clovek.jmeno if p.clovek else None,
            "kdo": p.kdo,
            "datum": p.datum,
            "poradi": p.poradi,
            "poradi_v_knize": p.poradi_v_knize,
        }
        for p in akce.prijmy
    ]


def _pokladni_radky(
    akce: Akce, index_ucastnik: dict[int | None, int], index_cestak: dict[int | None, int]
) -> list[dict]:
    """Rucni radky knihy - typicky vratka ucastnikovi. Prevod zustatku do
    pokladny pristavu (posledni radek knihy) se sem NEUKLADA - v databazi
    vubec nevznika, dopocita se sam pri kazdem sestaveni knihy
    (pokladni_kniha._doklad_prevodu), staci mit spravne vsechno ostatni."""
    return [
        {
            "datum": r.datum,
            "ucel": r.ucel,
            "smer": r.smer,
            "castka": r.castka,
            "poradi": r.poradi,
            "poradi_v_knize": r.poradi_v_knize,
            "ucastnik": index_ucastnik.get(r.ucastnik_id),
            "cestak": index_cestak.get(r.cestak_id),
        }
        for r in akce.pokladni_radky
    ]


def _ucastnici(ucastnici: list[Ucastnik]) -> list[dict]:
    return [
        {
            "poradi": u.poradi,
            "jmeno": u.jmeno,
            "prijmeni": u.prijmeni,
            "prezdivka": u.prezdivka,
            "ulice": u.ulice,
            "mesto": u.mesto,
            "psc": u.psc,
            "datum_narozeni": u.datum_narozeni,
            "jednotka": u.jednotka,
            "osobodny": u.osobodny,
            "detodny": u.detodny,
            "dochazka_json": u.dochazka_json,
            "hotove": u.hotove,
            "hotove_doklad": u.hotove_doklad,
            "prevodem": u.prevodem,
            "prevodem_doklad": u.prevodem_doklad,
            "vouchery": u.vouchery,
            "vouchery_doklad": u.vouchery_doklad,
            "vratka_doklad": u.vratka_doklad,
        }
        for u in ucastnici
    ]


def _obdarovani(obdarovani: list[Obdarovany]) -> list[dict]:
    return [
        {
            "poradi": o.poradi,
            "jmeno": o.jmeno,
            "prijmeni": o.prijmeni,
            "prezdivka": o.prezdivka,
            "ulice": o.ulice,
            "mesto": o.mesto,
            "psc": o.psc,
            "datum_narozeni": o.datum_narozeni,
        }
        for o in obdarovani
    ]


def _dary(
    akce: Akce, index_sken: dict[int | None, int], index_obdarovany: dict[int | None, int]
) -> list[dict]:
    return [
        {
            "nazev": d.nazev,
            "poradi": d.poradi,
            "sken": index_sken.get(d.sken_id),
            "obdarovany": index_obdarovany.get(d.obdarovany_id),
        }
        for d in akce.dary
    ]


def _cestaky(
    akce: Akce, index_sken: dict[int | None, int], index_slozky: dict[int | None, int]
) -> list[dict]:
    """Sazba nahrady se neuklada - dopocita se ze trech vstupu niz
    (app/services/cestaky.py). Adresa, kontakt, spotreba a cena za litr
    naopak ano: na cestaku uz to nejsou odkazy do ciselniku, ale otisky,
    ktere sly na papir."""
    return [
        {
            "ucel_cesty": c.ucel_cesty,
            "misto": c.misto,
            "spolucestujici": c.spolucestujici,
            "poznamka": c.poznamka,
            "jednotka": c.jednotka,
            "cestujici": c.cestujici.jmeno if c.cestujici else None,
            "adresa": c.adresa,
            "kontakt": c.kontakt,
            "vozidlo": c.vozidlo.nazev if c.vozidlo else None,
            "spotreba": c.spotreba,
            "palivova_uctenka": index_sken.get(c.palivova_uctenka_id),
            "puvodni_slozka_palivovky": index_slozky.get(c.puvodni_slozka_palivovky_id),
            "cena_za_litr": c.cena_za_litr,
            "opotrebeni": c.opotrebeni,
            "poradi": c.poradi,
            "jizdy": [
                {
                    "datum": j.datum,
                    "odkud": j.odkud,
                    "kam": j.kam,
                    "prostredek": j.prostredek,
                    "km": j.km,
                    "jizdne": j.jizdne,
                    "z_lat": j.z_lat,
                    "z_lon": j.z_lon,
                    "do_lat": j.do_lat,
                    "do_lon": j.do_lon,
                    "poradi": j.poradi,
                }
                for j in c.jizdy
            ],
        }
        for c in akce.cestaky
    ]


def data_akce(akce: Akce, cesty_v_zipu: dict[int, str]) -> dict:
    """Cela akce jako obycejny slovnik pro data.json.

    `cesty_v_zipu` je {id skenu: cesta souboru v archivu} - sestavuje ji
    `sestav_zalohu` soucasne s tim, jak skeny do ZIPu opravdu zapisuje, aby
    cesta v data.json vzdy sedela s tim, co je v archivu doopravdy.

    Vazby mezi zaznamy (slozka uctenky, priloha k uctence, ucastnik u vratky
    v knize...) se neukladaji jako id z databaze - ta po obnove budou jina -
    ale jako POZICE v prislusnem seznamu tehle same struktury. Ciselniky
    (kategorie, lide) jsou naopak sdilene napric akcemi, takze se hledaji
    podle jmena, ne podle pozice.
    """
    slozky_serazene = list(akce.slozky)
    # `Akce.skeny` nema vlastni razeni (na rozdil od slozek/ucastniku/...) -
    # bez vlastniho serazeni by poradi v seznamu nebylo stabilni a indexy
    # (priloha_k, slozka) by se rozjely.
    skeny_serazene = sorted(akce.skeny, key=lambda s: s.id)
    ucastnici_serazeni = list(akce.ucastnici)
    obdarovani_serazeni = list(akce.obdarovani)

    index_slozky: dict[int | None, int] = {s.id: i for i, s in enumerate(slozky_serazene)}
    index_sken: dict[int | None, int] = {s.id: i for i, s in enumerate(skeny_serazene)}
    index_ucastnik: dict[int | None, int] = {u.id: i for i, u in enumerate(ucastnici_serazeni)}
    index_obdarovany: dict[int | None, int] = {o.id: i for i, o in enumerate(obdarovani_serazeni)}
    index_cestak: dict[int | None, int] = {c.id: i for i, c in enumerate(akce.cestaky)}

    return {
        "verze_zalohy": VERZE_ZALOHY,
        "akce": _akce(akce),
        "kategorie": _kategorie(akce),
        "lide": _lide(akce),
        "vozidla": _vozidla(akce),
        "slozky": _slozky(slozky_serazene),
        "skeny": _skeny(skeny_serazene, cesty_v_zipu, index_sken, index_slozky),
        "prijmy": _prijmy(akce),
        "pokladni_radky": _pokladni_radky(akce, index_ucastnik, index_cestak),
        "ucastnici": _ucastnici(ucastnici_serazeni),
        "obdarovani": _obdarovani(obdarovani_serazeni),
        "dary": _dary(akce, index_sken, index_obdarovany),
        "cestaky": _cestaky(akce, index_sken, index_slozky),
    }


def sestav_zalohu(akce: Akce, slozka_uploadu: Path, cil: str | Path | IO[bytes]) -> None:
    """ZIP: skeny ve slozkach + Excel pro hospodare + data.json, rovnou do `cil`
    (soubor na disku, ne pamet - B-041)."""
    with zipfile.ZipFile(cil, "w", zipfile.ZIP_DEFLATED) as archiv:
        # Slozky se sazi presne jako v exportu pro Honzu - stejna funkce
        # (`zip_export.zapis_skeny`), vcetne cisla dokladu na zacatku nazvu.
        # Zaloha, ktera by mela jine prihradky nez export, by se s nim po roce
        # nedala porovnat.
        cesty_v_zipu, chybejici = zip_export.zapis_skeny(archiv, akce, slozka_uploadu)

        archiv.writestr(excel.nazev_souboru(akce), excel.do_bajtu(akce))
        archiv.writestr(
            excel_pokladni_kniha.nazev_souboru(akce), excel_pokladni_kniha.do_bajtu(akce)
        )
        archiv.writestr(
            "data.json",
            json.dumps(
                data_akce(akce, cesty_v_zipu), ensure_ascii=False, indent=2, default=_prevod
            ),
        )
        zip_export.zapis_seznam_chybejicich(archiv, chybejici)


def nazev_souboru(akce: Akce) -> str:
    return f"zaloha_{bezpecny_nazev(akce.nazev)}.zip"
