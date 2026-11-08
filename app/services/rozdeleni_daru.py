"""Spravedlive rozhozeni veci mezi obdarovane.

Jedno kliknuti misto dvaceti pretazeni mysi. Pravidla jsou dve:

1. **Nikdo nema o dve veci vic nez druhy.** Vec vzdycky dostane ten, kdo jich
   ma zatim nejmin. Padesat dve veci mezi dvacet lidi tedy znamena, ze dvanact
   lidi dostane tri a osm dva - nic nezustane nerozdane.

2. **Stejne veci se rozhazuji co nejdal od sebe.** Kdyby se rozdavaly po rade,
   jak lezi na uctence, dostal by prvni clovek tri stejne micky a na posledniho
   by zbyly tri stejne odznaky. Proto se seznam nejdriv prolozi.

Co uz lidi maji, se pocita s sebou: kdyz Honza jednomu rucne pretahl tri veci
a pak da rozhodit zbytek, dalsi veci dostanou nejdriv ostatni.

Rozhozeni nic neuklada - vraci dvojice (vec, clovek) a zapis uz je na route.
"""

from __future__ import annotations

from app.models import Dar, Obdarovany


def _prolozene(veci: list[Dar]) -> list[Dar]:
    """Serazene tak, aby po sobe pokud mozno nesly dve stejne veci.

    Veci se rozdeli do hromadek podle nazvu a pak se bere po jedne z kazde
    hromadky dokola. Zacina se od nejvetsi hromadky - jinak by na konci
    zbyla hrstka stejnych veci a ta by skoncila u jednoho cloveka.
    """
    hromadky: dict[str, list[Dar]] = {}
    for vec in veci:
        hromadky.setdefault(vec.nazev.strip().lower(), []).append(vec)

    fronty = sorted(hromadky.values(), key=len, reverse=True)
    poradi: list[Dar] = []
    while fronty:
        for fronta in fronty:
            poradi.append(fronta.pop(0))
        fronty = [fronta for fronta in fronty if fronta]
    return poradi


def rozhod(veci: list[Dar], lide: list[Obdarovany]) -> list[tuple[Dar, Obdarovany]]:
    """Komu ktera vec pripadne. Nic neuklada, jen vrati dvojice.

    Bez lidi v seznamu neni komu rozdavat a vrati se prazdno - vec radeji
    zustane v zasobniku, nez aby se ztratila.
    """
    if not lide or not veci:
        return []

    # Pocitadlo vedle sebe, ne v databazi: behem rozhazovani se porad meni
    # a zapisovat mezistavy by znamenalo ukladat rozdeleni, ktere jeste neplati.
    pocty = [len(clovek.dary) for clovek in lide]

    dvojice: list[tuple[Dar, Obdarovany]] = []
    for vec in _prolozene(veci):
        # Pri shode poctu vyhrava ten, kdo je v seznamu vys - diky tomu vyjde
        # rozhozeni pri stejnem zadani pokazde stejne.
        kdo = min(range(len(lide)), key=lambda i: (pocty[i], i))
        dvojice.append((vec, lide[kdo]))
        pocty[kdo] += 1
    return dvojice
