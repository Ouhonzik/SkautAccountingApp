"""Export ZIPu se skeny roztridenymi do slozek.

Struktura odpovida tomu, co ma Honza na disku:

    Lednová víkendovka.zip
    ├── VPD 01 - Jízdné - Lednová víkendovka/
    │   ├── Jízdné tam 28x32=896.pdf
    │   └── Jízdné zpět 896+50=946.pdf
    └── VPD 02 - Cesťák - Lednová víkendovka/
        ├── Cestovní příkaz.pdf
        └── Cesťák globus benzín 500,-.pdf     <- priloha, v Excelu radek nema

Prilohy v ZIPu JSOU, i kdyz v Excelu nemaji radek.

Pred nazvem stoji cislo dokladu - stejne, jake ma slozka na papire i v Excelu
(`pokladni_kniha.nazvy_slozek`). Bez nej je na disku pet slozek, ktere se lisi
jen jmenem platice, a nic je nespojuje s odevzdanymi doklady.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import IO

from app.models import Akce, Sken
from app.services import pokladni_kniha
from app.services.soubory import bezpecny_nazev, zaber_nazev

SLOZKA_NEZARAZENE = "Nezařazené"
SOUBOR_S_PROBLEMY = "CHYBEJICI SOUBORY.txt"


def nazev_v_zipu(sken: Sken) -> str:
    zaklad = bezpecny_nazev(sken.nazev or Path(sken.puvodni_nazev).stem)
    pripona = Path(sken.puvodni_nazev).suffix.lower()
    return f"{zaklad}{pripona}"


def _prihradky_cestaku(
    akce: Akce, cisla: pokladni_kniha.CislaDokladu
) -> list[tuple[str, list[Sken]]]:
    """Slozka pro kazdy cestak, ktery ma nalepenou palivovou uctenku.

    Palivovka je pripnuta k cestaku, takze uz nelezi v zadne slozce - bez
    tehle prihradky by v ZIPu spadla mezi nezarazene, tedy jinam nez papir,
    na ktery se lepi. Cestak, ktery jeste neni ve vydajich, cislo dokladu nema
    a jde pod holy nazev.
    """
    nazvy = pokladni_kniha.nazvy_cestaku(akce, cisla)
    prihradky = []
    for cestak in akce.cestaky:
        uctenka = cestak.palivova_uctenka
        if uctenka is None or uctenka.akce_id != akce.id:
            continue
        prihradky.append((bezpecny_nazev(nazvy[cestak.id]), [uctenka]))
    return prihradky


def skeny_po_slozkach(akce: Akce) -> list[tuple[str, list[Sken]]]:
    """Seskupi skeny podle slozek. Nezarazene skeny jdou do vlastni slozky."""
    cisla = pokladni_kniha.cisla_dokladu(akce)  # kniha se sestavuje jednou
    nazvy = pokladni_kniha.nazvy_slozek(akce, cisla)
    skupiny: list[tuple[str, list[Sken]]] = [
        (bezpecny_nazev(nazvy[slozka.id]), list(slozka.skeny))
        for slozka in akce.slozky
        if slozka.skeny
    ]

    prihradky_cestaku = _prihradky_cestaku(akce, cisla)
    skupiny += prihradky_cestaku

    # Uctenka uz lezi v prihradce sveho cestaku - mezi nezarazene nepatri,
    # jinak by byla v ZIPu dvakrat.
    u_cestaku = {s.id for _, skeny in prihradky_cestaku for s in skeny}
    nezarazene = [s for s in akce.skeny if s.slozka_id is None and s.id not in u_cestaku]
    if nezarazene:
        skupiny.append((SLOZKA_NEZARAZENE, nezarazene))

    # Dve prihradky se stejnym nazvem (dva cestaky jednoho cloveka mimo vydaje,
    # slozky jen s prilohami, slozka pojmenovana "Nezarazene") by se v ZIPu
    # slily do jedne - a na Windows i ty, co se lisi jen velikosti pisma.
    obsazene: set[str] = set()
    return [(zaber_nazev(nazev, obsazene, pripona=False), skeny) for nazev, skeny in skupiny]


def zapis_skeny(
    archiv: zipfile.ZipFile, akce: Akce, slozka_uploadu: Path
) -> tuple[dict[int, str], list[str]]:
    """Zapise skeny akce do ZIPu po slozkach. Vraci ({id skenu: cesta v archivu},
    cesty skenu, ktere na disku chybely).

    Spolecne pro export i zalohu, aby meli stejne cesty. Cislo " (2)" v nazvu
    dostane i sken, ktery na disku chybi - jeho cesta se tim po vraceni souboru
    nezmeni a zaloha sedi s exportem. Skeny jsou fotky a PDF, uz
    komprimovane; znovu je pakovat (DEFLATED) jen pali procesor.
    """
    cesty: dict[int, str] = {}
    chybejici: list[str] = []
    for nazev_slozky, skeny in skeny_po_slozkach(akce):
        obsazene: set[str] = set()
        for sken in skeny:
            cesta = f"{nazev_slozky}/{zaber_nazev(nazev_v_zipu(sken), obsazene)}"
            zdroj = slozka_uploadu / sken.soubor
            if not zdroj.is_file():
                chybejici.append(cesta)
                continue
            archiv.write(zdroj, cesta, compress_type=zipfile.ZIP_STORED)
            cesty[sken.id] = cesta
    return cesty, chybejici


def zapis_seznam_chybejicich(archiv: zipfile.ZipFile, chybejici: list[str]) -> None:
    """Kdyz nejaky sken na disku chybi, pribali se do ZIPu textovy seznam."""
    if chybejici:
        archiv.writestr(
            SOUBOR_S_PROBLEMY,
            "Tyto skeny se nepodařilo najít na disku:\n\n"
            + "\n".join(f"  {c}" for c in chybejici)
            + "\n",
        )


def sestav_zip(akce: Akce, slozka_uploadu: Path, cil: str | Path | IO[bytes]) -> list[str]:
    """Vyrobi ZIP rovnou do `cil` (soubor na disku, ne pamet - B-041, K-127).
    Vraci seznam skenu, ktere chybely na disku.

    Kdyz nejaky sken na disku chybi, ZIP se presto vyrobi - jen se do nej prida
    textovy soubor se seznamem toho, co chybelo. Lepsi nez export odmitnout.
    """
    with zipfile.ZipFile(cil, "w", zipfile.ZIP_DEFLATED) as archiv:
        _, chybejici = zapis_skeny(archiv, akce, slozka_uploadu)
        zapis_seznam_chybejicich(archiv, chybejici)
    return chybejici


def nazev_souboru(akce: Akce) -> str:
    return f"{bezpecny_nazev(akce.nazev)}.zip"
