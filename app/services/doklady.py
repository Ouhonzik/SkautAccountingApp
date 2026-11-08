"""Tiskopisy PPD a VPD - predvyplneny list A4 na kazdy doklad.

Cisla dokladu se tu nevymysleji znovu. Berou se z pokladni knihy
(`pokladni_kniha.doklady`), aby `VPD 01` na papire bylo tez `VPD 01`, ktere je
v knize, ve slozkach i v Excelu pro hospodare - jinak by se doklad nedal dohledat.

Papir je jedine misto, kde se pise **cele oznaceni vcetne prefixu akce**
("VPD 2026/01 01"). Tiskopis odchazi z aplikace sam za sebe, takze na nem musi
byt videt, ke ktere akci patri; v tabulkach stoji cislo akce v zahlavi a v radcich
by se jen opakovalo.

Kolonka "Ze dne" je **den, kdy hotovost pohnula** - kdy prisla do pokladny
(PPD) nebo z ni odesla (VPD). Neni to den, kdy se doklad vyplnuje; klasicky
tiskopis PPD/VPD na den vyhotoveni ani policko nema. U slozky je to datum
posledni uctenky, u prijmu datum prijeti do pokladny. Datum vyuctovani akce
do nej nevstupuje - kdyz zadna polozka datum nema, zustane kolonka prazdna
a `problemy()` doklad nahlasi.

Nepocita se tu znovu: bere se `Doklad.datum` z knihy, tedy tentyz den,
podle ktereho doklad dostal sve cislo. Druha vlastni definice by znamenala, ze
cislo a datum na jednom papire muzou jit proti sobe.

Krom jednotlivych dokladu se tu sklada i **titulni list** (`titulni_list`) -
prvni papir ve slozce, ktery rekne, ci akce to je a co je za nim zalozeno.
Nesaha si na vlastni data: vsechno secte z tiskopisu, ktere se tisknou za nim.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from app.models import SMER_PRIJEM, Akce
from app.services import pokladni_kniha, slovy
from app.services.pokladni_kniha import (
    NULA,
    ZDROJ_SKEN,
    ZDROJ_SLOZKA,
    CastRozpisu,
    Doklad,
)


@dataclass
class Polozka:
    """Jedna uctenka v rozpisu na dokladu."""

    datum: dt.date | None
    ucel: str
    kategorie: str | None
    castka: Decimal
    # Papiry nalepene k teto uctence. Na castku nemaji vliv - jen se vypisou,
    # aby bylo videt, co k uctence patri.
    prilohy: list[str] = field(default_factory=list)
    # Rozpis rozdelene uctenky po kategoriich (jidlo 300, material 120). Na
    # castku taky nema vliv: `castka` zustava celou uctenkou, takze "Celkem"
    # pod tabulkou dal sedi s kolonkou Castka i s pokladni knihou.
    casti: list[CastRozpisu] = field(default_factory=list)


@dataclass
class Tiskopis:
    """Jeden vytisteny pokladni doklad."""

    oznaceni: str  # "VPD 2026/01 01" - tez cislo jako v knize, jen s prefixem akce
    je_prijem: bool  # PPD versus VPD
    datum: dt.date | None
    ucel: str  # co to realne bylo - u slozky jeji "Ucel platby"
    nazev_slozky: str  # kam papir patri; prazdne u prijmu a rucnich radku
    osoba: str | None  # komu se vyplaci, od koho se prijima
    castka: Decimal
    slovy: str
    pocet_priloh: int
    klic: str  # "slozka:5" - adresa pro tisk jednoho dokladu
    ma_rozpis: bool  # slozka muze mit vic uctenek, prijem nikdy
    mimo_slozku: bool  # nezarazeny sken, hlasi se na obrazovce
    polozky: list[Polozka] = field(default_factory=list)
    volne_prilohy: list[str] = field(default_factory=list)  # prilohy bez uctenky

    @property
    def nazev_tiskopisu(self) -> str:
        return "PŘÍJMOVÝ POKLADNÍ DOKLAD" if self.je_prijem else "VÝDAJOVÝ POKLADNÍ DOKLAD"

    @property
    def prilohy_bokem(self) -> list[str]:
        """Prilohy, ktere se vypisou pod rozpisem, ne pod nekterou uctenkou.

        Bud k zadne zdejsi uctence nepatri, nebo doklad rozpis vubec nema
        (prijem, rucni radek, nezarazeny sken). Nalepi se stejne jako ostatni,
        takze na dokladu byt musi.
        """
        if self.ma_rozpis:
            return self.volne_prilohy
        return self.volne_prilohy + [n for p in self.polozky for n in p.prilohy]

    @property
    def popisek_osoby(self) -> str:
        return "Přijato od" if self.je_prijem else "Vyplaceno komu"


def _tiskopis(doklad: Doklad) -> Tiskopis:
    castka = sum(((p.prijem or NULA) + (p.vydej or NULA) for p in doklad.polozky), NULA)
    return Tiskopis(
        oznaceni=doklad.oznaceni,
        je_prijem=doklad.smer == SMER_PRIJEM,
        datum=doklad.datum,
        ucel=doklad.ucel,
        nazev_slozky=doklad.nazev_slozky,
        osoba=doklad.osoba,
        castka=castka,
        slovy=slovy.korunami(castka),
        pocet_priloh=doklad.pocet_priloh,
        klic=f"{doklad.zdroj}:{doklad.id}",
        ma_rozpis=doklad.zdroj == ZDROJ_SLOZKA,
        mimo_slozku=doklad.zdroj == ZDROJ_SKEN,
        volne_prilohy=doklad.volne_prilohy,
        polozky=[
            Polozka(
                datum=p.datum,
                ucel=p.ucel,
                kategorie=p.kategorie,
                castka=(p.prijem or NULA) + (p.vydej or NULA),
                prilohy=p.prilohy,
                casti=p.casti,
            )
            for p in doklad.polozky
        ],
    )


def sestav(akce: Akce) -> list[Tiskopis]:
    """Vsechny doklady akce v poradi, v jakem dostaly cisla."""
    return [_tiskopis(d) for d in pokladni_kniha.doklady(akce)]


def jeden(akce: Akce, klic: str, tiskopisy: list[Tiskopis] | None = None) -> Tiskopis | None:
    """Jeden doklad podle adresy `slozka:5`. Kdyz takovy neni, vraci None.

    Kdo uz tiskopisy akce sestavil (`sestav`), preda je - jinak by se cela
    pokladni kniha prochazela podruhe.
    """
    if tiskopisy is None:
        tiskopisy = sestav(akce)
    return next((t for t in tiskopisy if t.klic == klic), None)


def problemy(tiskopisy: list[Tiskopis]) -> tuple[int, int]:
    """Kolik dokladu nema datum a kolik jich stoji mimo slozku.

    Doklad bez data se nesmi odevzdat a nezarazeny sken ma vlastni cislo
    dokladu misto toho, aby patril ke sve slozce - obojim se stranka ozve.
    """
    bez_data = sum(1 for t in tiskopisy if t.datum is None)
    mimo_slozku = sum(1 for t in tiskopisy if t.mimo_slozku)
    return bez_data, mimo_slozku


# --- Titulni list ----------------------------------------------------------


@dataclass
class TitulniList:
    """Prvni papir ve slozce: co je uvnitr a jak to dopadlo.

    Penize se nepocitaji z akce, ale ze samotnych tiskopisu. Titulni list stoji
    primo nad soupisem dokladu, takze musi sedet s tim, co je pod nim - kdyby
    "Vydaje celkem" vzniklo jinym souctem nez ten, ktery si ctenar precte
    v tabulce, byl by na jednom papire rozpor. Nezarazena uctenka proto do
    souctu vstupuje: papir se tiskne, tak se i pocita. Ze ve slozce neni,
    hlasi obrazovka zvlast.
    """

    nazev_akce: str
    datum_od: dt.date | None
    datum_do: dt.date | None
    datum_vyuctovani: dt.date | None
    prefix: str  # kod akce uprostred cisel dokladu ("2026/01")
    pokladnik: str
    prijmy: Decimal
    vydaje: Decimal
    bilance: Decimal
    pocet_dokladu: int
    pocet_uctenek: int  # papiry s vlastnim radkem, tedy krome priloh
    pocet_priloh: int
    doklady: list[Tiskopis] = field(default_factory=list)

    @property
    def ma_termin(self) -> bool:
        return self.datum_od is not None and self.datum_do is not None

    @property
    def jednodenni(self) -> bool:
        return self.datum_od == self.datum_do

    @property
    def rok(self) -> int | None:
        """Rok do paticky. Termin akce ma prednost pred datem vyuctovani -
        vyuctovani muze prebehnout do dalsiho roku, akce sama ne."""
        for datum in (self.datum_od, self.datum_vyuctovani):
            if datum is not None:
                return datum.year
        return None


def titulni_list(akce: Akce, tiskopisy: list[Tiskopis]) -> TitulniList:
    """Uvodni strana slozky sestavena z dokladu, ktere se tisknou za ni."""
    prijmy = sum((t.castka for t in tiskopisy if t.je_prijem), NULA)
    vydaje = sum((t.castka for t in tiskopisy if not t.je_prijem), NULA)
    return TitulniList(
        nazev_akce=akce.nazev,
        datum_od=akce.datum_od,
        datum_do=akce.datum_do,
        datum_vyuctovani=akce.datum_vyuctovani,
        prefix=akce.prefix_dokladu or "",
        pokladnik=akce.pokladnik or "",
        prijmy=prijmy,
        vydaje=vydaje,
        bilance=prijmy - vydaje,
        pocet_dokladu=len(tiskopisy),
        # Rucni radek pokladni knihy uctenku nema, tak se do poctu nepocita -
        # do slozky se za nej zadny papir nezaklada.
        pocet_uctenek=sum(len(t.polozky) for t in tiskopisy if t.ma_rozpis or t.mimo_slozku),
        pocet_priloh=sum(
            len(t.volne_prilohy) + sum(len(p.prilohy) for p in t.polozky) for t in tiskopisy
        ),
        doklady=tiskopisy,
    )
