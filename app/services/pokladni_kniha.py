"""Sestaveni pokladni knihy.

Cisty vypocet - zadne HTTP, zadny openpyxl. Stoji na nem obrazovka i export,
aby kniha nikdy nevysla na dvou mistech ruzne.

Tri pravidla, ktera se daji lehko rozbit:

1. **Radek je jedna uctenka a radi se sam za sebe podle sveho data.** Uctenky
   jedne slozky v knize nemusi lezet vedle sebe - mezi ne se vklini uctenka
   z jine slozky, kdyz to podle data vychazi. (Drive tvorila slozka souvisly
   blok; Honza to 30. 8. 2026 zmenil, protoze kniha ma jit chronologicky.)
2. **Cislo dokladu patri dokladu, ne mistu v knize.** Slozka ho dostane jednou
   - podle sveho data, tedy podle sve nejmladsi uctenky - a uz si ho nese, at je
   jeji uctenka kdekoli. Cisla tak jdou po stejne casove ose jako kolonka
   "Ze dne" na tiskopisech: papiry serazene podle cisel jsou serazene i podle
   data. Danou cenou je, ze v knize se `VPD 01` muze objevit dvakrat a mezi tim
   jednou `VPD 02`, a ze prvni radek knihy nemusi patrit prvnimu dokladu; neni
   to chyba. Prijmy a vydaje maji navic kazdy vlastni radu (`PPD` a `VPD`),
   aby sly doklady od sebe rozeznat.
3. **Zustatek zacina na nule** a je bezici soucet v tom poradi, v jakem radky
   opravdu lezi. Castky jsou presne vcetne haleru; zaokrouhluje se az v Excelu.

Rucni poradi ze sipek (`presun`) prebiji datum. Dokud se nicim nehnulo, je
`poradi_v_knize` u vsech radku prazdne a kniha se radi ciste chronologicky.

4. **Posledni radek knihy je vzdy prevod zustatku a nejde smazat ani posunout.**
   Nevznika v databazi - dopocita se z castek vsech ostatnich dokladu pri
   kazdem sestaveni knihy, aby akce skoncila na nule (`_doklad_prevodu`). Kdyz
   kniha zadny jiny radek nema, neni co prevadet a radek se nevygeneruje.
   Cislo dokladu dostane az jako posledni ve sve rade (`doklady`) a v knize
   se vzdy pripoji za vsechny ostatni radky (`sestav`) - obojí bez ohledu na
   datum, protoze radek bez data by jinak "vyhral" i nad prevodem.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from app.models import SMER_PRIJEM, SMER_VYDEJ, Akce, PokladniRadek, Prijem, Sken, Slozka

NULA = Decimal("0.00")

ZDROJ_SKEN = "sken"
ZDROJ_PRIJEM = "prijem"
ZDROJ_RUCNI = "rucni"
# Jen pro `Doklad.zdroj`: radek knihy ze slozky nikdy nevznika, radky delaji
# jeji jednotlive uctenky. Doklad ale slozkou je - a tiskopis se tiskne za ni.
ZDROJ_SLOZKA = "slozka"
# Posledni radek knihy - prevod zustatku do pokladny pristavu. Nevznika v
# databazi, viz `_doklad_prevodu`.
ZDROJ_PREVOD = "prevod"

# Poradi dokladu, ktere maji stejne datum i stejne `poradi`: prijmy, pak slozky,
# nezarazene skeny, rucni radky a nakonec prevod zustatku. Z tohohle poradi se
# odvozuji cisla dokladu na papire, proto se nesmi zmenit (testy to hlidaji).
# Poradi je historicke (zadny hlubsi duvod za nim neni) a nemeni se kvuli cislum
# dokladu na papirech.
PORADI_ZDROJU = {
    ZDROJ_PRIJEM: 0,
    ZDROJ_SLOZKA: 1,
    ZDROJ_SKEN: 2,
    ZDROJ_RUCNI: 3,
    ZDROJ_PREVOD: 4,
}

UCEL_PREVODU = "Převod zůstatku do pokladny přístavu."

# Kolik mist ma poradove cislo dokladu: "VPD 01". Nad devadesat devet dokladu
# se cislo samo roztahne na tri mista, jen se prestane zarovnavat.
MIST_CISLA = 2

# Rada cisla dokladu: prijmovy (PPD) a vydajovy (VPD) pokladni doklad.
RADA_PRIJMOVA = "PPD"
RADA_VYDAJOVA = "VPD"


@dataclass
class CastRozpisu:
    """Jedna kategorie na rozdelene uctence, tak jak se vypise na papire.

    Do zadne castky nevstupuje - `Sken.castka` zustava castkou uctenky. Je to
    jen rozpis toho stejneho, aby bylo videt, ze na nakupu jidla byl i material.
    """

    kategorie: str
    castka: Decimal
    popis: str | None = None


@dataclass
class Radek:
    """Jeden radek pokladni knihy tak, jak se ukaze na obrazovce i v Excelu."""

    cislo: int  # sloupec "C." - poradove cislo radku, meni se s poradim
    datum: dt.date | None
    doklad: str  # sloupec "Dokl." - u uctenek jedne slozky stejny
    ucel: str
    prijem: Decimal | None
    vydej: Decimal | None
    zustatek: Decimal
    zdroj: str  # ZDROJ_SKEN | ZDROJ_PRIJEM | ZDROJ_RUCNI
    zdroj_id: int
    smer: str | None = None  # jen u rucnich radku, kvuli prepinaci ve formulari
    bez_data: bool = False
    mimo_slozku: bool = False
    # Vyplnene u rucniho radku, ktery vznikl z cestaku. Kniha ho pak ukaze jen
    # ke cteni s odkazem na cestak - castka je soucet jizd, ne rucne psane cislo.
    cestak_id: int | None = None
    # Rozpis rozdelene uctenky po kategoriich. Radek zustava jeden a nese celou
    # castku - casti se jen vypisou pod nim, takze zustatek se o ne nezmeni.
    casti: list[CastRozpisu] = field(default_factory=list)


def _klic_data(datum: dt.date | None) -> tuple:
    """Radek bez data je v razeni vzdy az za temi s datem."""
    return (datum is None, datum or dt.date.min)


@dataclass
class _Polozka:
    """Radek jeste bez cisla, cisla dokladu a zustatku."""

    datum: dt.date | None
    ucel: str
    prijem: Decimal | None
    vydej: Decimal | None
    zdroj: str
    zdroj_id: int
    poradi_v_knize: int | None = None
    smer: str | None = None
    mimo_slozku: bool = False
    cestak_id: int | None = None
    # Jen pro rozpis na tiskopisu PPD/VPD - kniha kategorii nezobrazuje.
    kategorie: str | None = None
    # Nazvy priloh nalepenych k teto uctence. Taky jen pro tiskopis; do zadne
    # castky nevstupuji, priloha se nikde nepocita.
    prilohy: list[str] = field(default_factory=list)
    # Rozpis rozdelene uctenky po kategoriich - taky jen na papir. Castka
    # polozky zustava cela, casti ji jen rozepisuji.
    casti: list[CastRozpisu] = field(default_factory=list)

    # Doplni se az pri sestaveni knihy, kdyz uz je jasne, ke kteremu dokladu
    # polozka patri a jake cislo ten doklad dostal.
    doklad: str = ""
    klic_dokladu: tuple = ()
    poradi_v_dokladu: int = 0

    @property
    def chronologicky(self) -> tuple:
        """Vychozi poradi: podle data, pri shode drzi uctenky jedne slozky u sebe."""
        return (*_klic_data(self.datum), self.klic_dokladu, self.poradi_v_dokladu)


@dataclass
class Doklad:
    """Skupina polozek, ktera dostane jedno spolecne cislo dokladu.

    Krom knihy z toho zije i tiskopis PPD/VPD (app/services/doklady.py) -
    proto tu jsou i udaje, ktere sama kniha nepotrebuje: `osoba` a `pocet_priloh`.
    """

    # Den, kdy hotovost pohnula: u slozky datum jeji nejmladsi uctenky, jinde
    # datum jedine polozky. Je to zaroven kolonka "Ze dne" na tiskopisu - jedno
    # datum s jednim vyznamem, aby cislo dokladu a datum na papire nesly proti
    # sobe. Kdyby se tohle rozdelilo na dve, vrati se chyba, kvuli ktere to
    # vzniklo: `VPD 01` ze dne 31. 7. a `VPD 02` z 20. 7.
    datum: dt.date | None
    poradi: int
    id: int
    smer: str  # rozhoduje, jestli doklad dostane radu V nebo P
    polozky: list[_Polozka] = field(default_factory=list)
    # Cislo dokladu s prefixem akce ("VPD 2026/01 01"). Jen pro tiskopis PPD/VPD.
    oznaceni: str = ""
    # Tentyz doklad bez prefixu ("VPD 01"). Pod nim vystupuje vsude jinde.
    cislo: str = ""
    zdroj: str = ""  # ZDROJ_* - odkud doklad pochazi, spolu s `id` adresa jednoho dokladu
    # Co se natiskne do kolonky "Ucel platby". U slozky to neni jeji nazev -
    # ten je jen nazvem prihradky na disku a na dokladu stoji zvlast.
    ucel: str = ""
    nazev_slozky: str = ""  # prazdne u vseho, co slozka neni
    osoba: str | None = None  # komu se vyplaci / od koho se prijima
    pocet_priloh: int = 0  # kolik papiru se na doklad nalepi
    # Prilohy, ktere nepatri k zadne zdejsi uctence. Nalepi se stejne jako
    # ostatni, jen se na dokladu vypisou zvlast, ne pod nekterym radkem.
    volne_prilohy: list[str] = field(default_factory=list)

    @property
    def klic(self) -> tuple:
        """Poradi, ve kterem se dokladum prideluji cisla - podle data dokladu."""
        return (*_klic_data(self.datum), self.poradi, PORADI_ZDROJU[self.zdroj], self.id)


def _nejmladsi(datumy) -> dt.date | None:
    skutecne = [d for d in datumy if d is not None]
    return max(skutecne) if skutecne else None


def _ze_skenu(sken: Sken, mimo_slozku: bool = False) -> _Polozka:
    return _Polozka(
        datum=sken.datum,
        ucel=sken.nazev or sken.puvodni_nazev,
        prijem=None,
        vydej=sken.castka,
        zdroj=ZDROJ_SKEN,
        zdroj_id=sken.id,
        poradi_v_knize=sken.poradi_v_knize,
        mimo_slozku=mimo_slozku,
        kategorie=sken.kategorie.nazev if sken.kategorie else None,
        prilohy=[p.nazev or p.puvodni_nazev for p in sken.nalepene_prilohy],
        casti=[
            CastRozpisu(
                kategorie=c.nazev_kategorie,
                castka=c.castka or NULA,
                popis=c.popis,
            )
            for c in sken.casti
        ]
        if sken.je_rozdelena
        else [],
    )


def _z_prijmu(prijem: Prijem) -> _Polozka:
    return _Polozka(
        datum=prijem.datum,
        ucel=prijem.popis,
        prijem=prijem.castka,
        vydej=None,
        zdroj=ZDROJ_PRIJEM,
        zdroj_id=prijem.id,
        poradi_v_knize=prijem.poradi_v_knize,
    )


def _z_rucniho(radek: PokladniRadek) -> _Polozka:
    return _Polozka(
        datum=radek.datum,
        ucel=radek.ucel,
        prijem=radek.castka if radek.je_prijem else None,
        vydej=None if radek.je_prijem else radek.castka,
        zdroj=ZDROJ_RUCNI,
        zdroj_id=radek.id,
        poradi_v_knize=radek.poradi_v_knize,
        smer=radek.smer,
        cestak_id=radek.cestak_id,
    )


def _doklad_ze_slozky(slozka: Slozka) -> Doklad:
    skeny = slozka.skeny_s_radkem
    return Doklad(
        datum=_nejmladsi(s.datum for s in skeny),
        poradi=slozka.poradi,
        id=slozka.id or 0,
        smer=SMER_VYDEJ,
        polozky=[_ze_skenu(s) for s in skeny],
        zdroj=ZDROJ_SLOZKA,
        ucel=slozka.ucel_platby_vysledny,
        nazev_slozky=slozka.nazev,
        osoba=slozka.kdo_platil,
        # Prilohy maji svuj papir taky - nalepi se na doklad stejne jako uctenky,
        # jen nemaji radek. Proto se pocitaji vsechny skeny slozky.
        pocet_priloh=len(slozka.skeny),
        volne_prilohy=[p.nazev or p.puvodni_nazev for p in slozka.prilohy_bez_uctenky],
    )


def _doklady_prijmu(akce: Akce) -> list[Doklad]:
    return [
        Doklad(
            datum=p.datum,
            poradi=p.poradi,
            id=p.id or 0,
            smer=SMER_PRIJEM,
            polozky=[_z_prijmu(p)],
            zdroj=ZDROJ_PRIJEM,
            ucel=p.popis,
            osoba=p.kdo_vybral,
        )
        for p in akce.prijmy
    ]


def _doklady_nezarazenych_skenu(akce: Akce) -> list[Doklad]:
    """Nezarazeny sken je vlastni doklad. Nechavame ho v knize videt (oznaceny),
    aby se na nej neslo zapomenout - proto ho nefiltrujeme pryc."""
    return [
        Doklad(
            datum=s.datum,
            poradi=s.poradi,
            id=s.id or 0,
            smer=SMER_VYDEJ,
            polozky=[_ze_skenu(s, mimo_slozku=True)],
            zdroj=ZDROJ_SKEN,
            ucel=s.nazev or s.puvodni_nazev,
            osoba=s.kdo_platil,
            pocet_priloh=1 + len(s.nalepene_prilohy),
        )
        for s in akce.skeny
        if s.slozka_id is None and not s.je_priloha
    ]


def _doklady_rucnich_radku(akce: Akce) -> list[Doklad]:
    return [
        Doklad(
            datum=r.datum,
            poradi=r.poradi,
            id=r.id or 0,
            smer=r.smer,
            polozky=[_z_rucniho(r)],
            zdroj=ZDROJ_RUCNI,
            ucel=r.ucel,
            # Rucni radek obvykle nic z tohohle nema. Vyjimka je cestak:
            # na jeho VPD patri jmeno cestujiciho a vypis palivove uctenky,
            # ktera je k nemu nalepena.
            osoba=_osoba_rucniho(r),
            pocet_priloh=len(_prilohy_rucniho(r)),
            volne_prilohy=_prilohy_rucniho(r),
        )
        for r in akce.pokladni_radky
    ]


def _doklady(akce: Akce) -> list[Doklad]:
    """Posbira vsechny doklady akce. Prilohy se do knihy nedostanou nikdy -
    `skeny_s_radkem` je odfiltruje stejne jako v Excelu pro hospodare."""
    return [
        *_doklady_prijmu(akce),
        *(_doklad_ze_slozky(s) for s in akce.slozky if s.skeny_s_radkem),
        *_doklady_nezarazenych_skenu(akce),
        *_doklady_rucnich_radku(akce),
    ]


def _osoba_rucniho(radek: PokladniRadek) -> str | None:
    """Komu se vyplaci. U cestaku cestujici, jinak se kolonka nechava prazdna."""
    cestak = radek.cestak
    if cestak is None or cestak.cestujici is None:
        return None
    return cestak.cestujici.jmeno


def _prilohy_rucniho(radek: PokladniRadek) -> list[str]:
    """Papiry nalepene na doklad rucniho radku.

    U cestaku je to palivova uctenka, kterou je dolozena cena za litr.
    Priloha se ani tady nepocita do zadne castky - jen se vypise, aby bylo
    videt, co je k dokladu pripnute.
    """
    cestak = radek.cestak
    if cestak is None or cestak.palivova_uctenka is None:
        return []
    uctenka = cestak.palivova_uctenka
    return [uctenka.nazev or uctenka.puvodni_nazev]


def _doklad_prevodu(doklady: list[Doklad], prefix: str) -> Doklad | None:
    """Posledni radek knihy: prevod toho, co v pokladne zbyva, do pokladny pristavu.

    Cislo nedostava razenim podle data jako ostatni doklady (`_ocisluj`), ale
    proste dalsi volne cislo ve vydajove rade - o jedno vyssi, nez kolik VPD uz
    je. Diky tomu je vzdy posledni, i kdyz by nejaky doklad mel pozdejsi datum
    nebo nemel datum vubec (radek bez data by jinak v razeni podle `Doklad.klic`
    skoncil az za nim, ne pred nim).

    Datum na tiskopisu je informativni - nejmladsi datum ze vsech ostatnich
    dokladu. Kdyz ho nema zadny z nich, zustava prazdne tak jako u nich.
    """
    if not doklady:
        return None

    prijem = sum((p.prijem or NULA for d in doklady for p in d.polozky), NULA)
    vydej = sum((p.vydej or NULA for d in doklady for p in d.polozky), NULA)
    zustatek = prijem - vydej
    datum = _nejmladsi(d.datum for d in doklady)

    pocet_vpd = sum(1 for d in doklady if d.smer == SMER_VYDEJ)
    poradove = f"{pocet_vpd + 1:0{MIST_CISLA}d}"

    return Doklad(
        datum=datum,
        poradi=0,
        id=0,
        smer=SMER_VYDEJ,
        polozky=[
            _Polozka(
                datum=datum,
                ucel=UCEL_PREVODU,
                prijem=None,
                vydej=zustatek,
                zdroj=ZDROJ_PREVOD,
                zdroj_id=0,
            )
        ],
        zdroj=ZDROJ_PREVOD,
        ucel=UCEL_PREVODU,
        cislo=f"{RADA_VYDAJOVA} {poradove}",
        oznaceni=" ".join(b for b in [RADA_VYDAJOVA, prefix.strip(), poradove] if b),
    )


def _ocisluj(doklady: list[Doklad], prefix: str) -> None:
    """Prideli dokladum cislo. Kazdy doklad ho dostane ve dvou podobach.

    `cislo` je holé "VPD 01": rada dokladu (`PPD` prijmovy, `VPD` vydajovy)
    a cisla rada 01, 02, 03. Pod nim doklad vystupuje vsude v aplikaci -
    v knize, ve slozkach i v obou Excelech.

    `oznaceni` ma navic prefix akce uprostred ("VPD 2026/01 01") a tiskne se
    jen na tiskopis PPD/VPD. Prefix je udaj pro hospodarovo ucetnictvi a je
    u vsech dokladu akce stejny, takze v tabulce by se jen opakoval radek co
    radek; do zahlavi obou Excelu se cislo akce pise zvlast. Kdyz prefix
    vyplneny neni, jsou obe podoby stejne.

    Prijmy a vydaje se cisluji zvlast, kazde od jednicky - jinak by prijmovy a
    vydajovy doklad mohly dostat stejne cislo a nesly by od sebe rozeznat.
    """
    citace = {SMER_PRIJEM: 0, SMER_VYDEJ: 0}
    for doklad in sorted(doklady, key=lambda d: d.klic):
        rada = SMER_PRIJEM if doklad.smer == SMER_PRIJEM else SMER_VYDEJ
        citace[rada] += 1
        rada_dokladu = RADA_PRIJMOVA if rada == SMER_PRIJEM else RADA_VYDAJOVA
        poradove = f"{citace[rada]:0{MIST_CISLA}d}"
        doklad.cislo = f"{rada_dokladu} {poradove}"
        doklad.oznaceni = " ".join(b for b in [rada_dokladu, prefix.strip(), poradove] if b)


def doklady(akce: Akce) -> list[Doklad]:
    """Doklady akce v poradi, v jakem dostaly cisla.

    Jedina cesta k cislovani - kniha i tiskopisy PPD/VPD berou cisla odsud,
    aby `VPD 01` na papire bylo tez `VPD 01`, ktere je v knize a v Excelu.

    Prevod zustatku (`_doklad_prevodu`) se pripoji az na konec, mimo razeni
    podle data - je to vzdy posledni doklad vydajove rady.
    """
    vsechny = _doklady(akce)
    _ocisluj(vsechny, akce.prefix_dokladu or "")
    serazene = sorted(vsechny, key=lambda d: d.klic)

    prevod = _doklad_prevodu(vsechny, akce.prefix_dokladu or "")
    if prevod is not None:
        serazene.append(prevod)
    return serazene


def _polozky(akce: Akce) -> list[_Polozka]:
    """Vsechny radky knihy, jeste neserazene, uz ale s cislem dokladu."""
    polozky: list[_Polozka] = []
    for doklad in doklady(akce):
        for poradi, polozka in enumerate(doklad.polozky):
            polozka.doklad = doklad.cislo
            polozka.klic_dokladu = doklad.klic
            polozka.poradi_v_dokladu = poradi
            polozky.append(polozka)
    return polozky


def _serad(polozky: list[_Polozka]) -> list[_Polozka]:
    """Poradi radku v knize. Rucni poradi prebiji datum.

    Uctenka, ktera pribyla az po prehazovani, zadne rucni poradi nema. Zaradi se
    podle data mezi uz srovnane radky - za posledni z nich, ktery neni mladsi.
    Bez toho by nova uctenka skocila na zacatek knihy.
    """
    srovnane = [p for p in polozky if p.poradi_v_knize is not None]
    if not srovnane:
        return sorted(polozky, key=lambda p: p.chronologicky)

    def pozice(polozka: _Polozka) -> tuple:
        if polozka.poradi_v_knize is not None:
            # Druha slozka klice: srovnany radek stoji az za nove zarazenymi,
            # ktere na jeho misto pripadly.
            return (polozka.poradi_v_knize, 1)
        if polozka.datum is None:
            return (len(srovnane), 0)  # bez data na konec, jako v chronologii
        starsi = sum(1 for s in srovnane if _klic_data(s.datum) <= _klic_data(polozka.datum))
        return (starsi, 0)

    return sorted(polozky, key=lambda p: (pozice(p), p.chronologicky))


def sestav(akce: Akce) -> list[Radek]:
    """Cela pokladni kniha akce, serazena a ocislovana.

    Prevod zustatku se do razeni podle data vubec nepusti (`_serad` by ho u
    knihy bez data zaradila pred nej) - pripoji se az za vsechny ostatni,
    proste jako posledni polozka seznamu.
    """
    polozky = _polozky(akce)
    ostatni = [p for p in polozky if p.zdroj != ZDROJ_PREVOD]
    prevod = [p for p in polozky if p.zdroj == ZDROJ_PREVOD]

    radky: list[Radek] = []
    zustatek = NULA

    for cislo, polozka in enumerate(_serad(ostatni) + prevod, 1):
        zustatek += (polozka.prijem or NULA) - (polozka.vydej or NULA)
        radky.append(
            Radek(
                cislo=cislo,
                datum=polozka.datum,
                doklad=polozka.doklad,
                ucel=polozka.ucel,
                prijem=polozka.prijem,
                vydej=polozka.vydej,
                zustatek=zustatek,
                zdroj=polozka.zdroj,
                zdroj_id=polozka.zdroj_id,
                smer=polozka.smer,
                bez_data=polozka.datum is None,
                mimo_slozku=polozka.mimo_slozku,
                casti=polozka.casti,
                cestak_id=polozka.cestak_id,
            )
        )
    return radky


def _zaznamy(akce: Akce) -> dict:
    """Ke kazdemu radku knihy zaznam, ze ktereho vznikl."""
    zaznamy: dict = {}
    for sken in akce.skeny:
        zaznamy[(ZDROJ_SKEN, sken.id)] = sken
    for prijem in akce.prijmy:
        zaznamy[(ZDROJ_PRIJEM, prijem.id)] = prijem
    for radek in akce.pokladni_radky:
        zaznamy[(ZDROJ_RUCNI, radek.id)] = radek
    return zaznamy


def presun(akce: Akce, zdroj: str, zdroj_id: int, dolu: bool) -> bool:
    """Posune radek o jedno misto nahoru nebo dolu. Vraci, jestli se pohnul.

    Zapisuje se poradi cele knihy, ne jen dvou prohozenych radku: dokud se
    s knihou nehnulo, nema poradi nikdo a radi se podle data. Kdyby se ulozily
    jen ty dva, zbytek knihy by dal visel na datu a poradi by se rozpadlo.

    Ulozeni do databaze uz je na volajicim - tady se jen prepisou zaznamy.
    """
    radky = sestav(akce)
    kde = next(
        (i for i, r in enumerate(radky) if r.zdroj == zdroj and r.zdroj_id == zdroj_id),
        None,
    )
    if kde is None or radky[kde].zdroj == ZDROJ_PREVOD:
        return False  # prevod zustatku se neda posunout, je vzdy posledni

    soused = kde + (1 if dolu else -1)
    if not 0 <= soused < len(radky):
        return False  # krajni radek uz dal nema kam
    if radky[soused].zdroj == ZDROJ_PREVOD:
        return False  # ani s prevodem zustatku se neda prohodit

    radky[kde], radky[soused] = radky[soused], radky[kde]
    zaznamy = _zaznamy(akce)
    for misto, radek in enumerate(radky):
        zaznam = zaznamy.get((radek.zdroj, radek.zdroj_id))
        if zaznam is not None:
            zaznam.poradi_v_knize = misto
    return True


def srovnej_podle_data(akce: Akce) -> None:
    """Zahodi rucni poradi - kniha se zase seradi podle data."""
    for zaznam in _zaznamy(akce).values():
        zaznam.poradi_v_knize = None


def je_srovnana_rucne(akce: Akce) -> bool:
    """Hlida, jestli ma smysl nabizet tlacitko 'Seradit podle data'."""
    return any(z.poradi_v_knize is not None for z in _zaznamy(akce).values())


def souhrn(radky: list[Radek]) -> tuple[Decimal, Decimal, Decimal]:
    """Prijmy, vydaje a konecny zustatek."""
    prijmy = sum((r.prijem for r in radky if r.prijem is not None), NULA)
    vydaje = sum((r.vydej for r in radky if r.vydej is not None), NULA)
    return prijmy, vydaje, prijmy - vydaje


def problemy(radky: list[Radek]) -> tuple[int, int]:
    """Kolik radku nema datum a kolik uctenek neni ve slozce.

    Obojim stoji a pada razeni i cislovani, proto to obrazovka hlasi cervene.
    Datum chybi i prijmum, ne jen uctenkam - a prijem na konci knihy je horsi
    nez uctenka: pokladna se do nej propadne do minusu a pak skoci nahoru.
    """
    bez_data = sum(1 for r in radky if r.bez_data)
    mimo_slozku = sum(1 for r in radky if r.mimo_slozku)
    return bez_data, mimo_slozku


@dataclass(frozen=True)
class CislaDokladu:
    """Cisla dokladu slozek a cestaku jedne akce, zjistena jednim pruchodem."""

    slozky: dict[int, str]  # {id slozky: "VPD 01"}
    cestaky: dict[int, str]  # {id cestaku: "VPD 03"}


def cisla_dokladu(akce: Akce) -> CislaDokladu:
    """Cisla dokladu ke slozkam i cestakum z jedineho zavolani `doklady()`.

    `doklady()` prochazi a radi vsechno, co akce obsahuje; kdo potrebuje obe
    mapy (Excel, ZIP), at zavola tohle a ne `cisla_slozek` s `cisla_cestaku`
    zvlast. Nic se nedrzi mezi pozadavky - data se meni kazdym ulozenim a
    zastarale cislo dokladu na papire je horsi nez o neco pomalejsi stranka.

    Slozka, ve ktere lezi same prilohy, doklad nema a v mape chybi; stejne tak
    cestak, ktery jeste neni v knize.
    """
    podle_radku = {c.pokladni_radek.id: c.id for c in akce.cestaky if c.pokladni_radek is not None}
    slozky: dict[int, str] = {}
    cestaky: dict[int, str] = {}
    for d in doklady(akce):
        if d.zdroj == ZDROJ_SLOZKA:
            slozky[d.id] = d.cislo
        elif d.zdroj == ZDROJ_RUCNI and d.id in podle_radku:
            cestaky[podle_radku[d.id]] = d.cislo
    return CislaDokladu(slozky=slozky, cestaky=cestaky)


def cisla_slozek(akce: Akce) -> dict[int, str]:
    """Cislo dokladu ke kazde slozce: {id slozky: "VPD 01"}.

    Cislo se tu nevymysli, bere se z `doklady()` - stejne jako na tiskopisu.
    """
    return cisla_dokladu(akce).slozky


def cisla_cestaku(akce: Akce) -> dict[int, str]:
    """Cislo dokladu ke kazdemu cestaku ve vydajich: {id cestaku: "VPD 03"}."""
    return cisla_dokladu(akce).cestaky


def nazvy_cestaku(akce: Akce, cisla: CislaDokladu | None = None) -> dict[int, str]:
    """Nazev prihradky cestaku: "VPD 03 - Cesťák - Jan Novák".

    Jedno misto pro ZIP (prihradka na disku) i Excel pro hospodare (radek),
    protoze podle nich hospodar paruje papir se soubory - kdyby si je kazdy
    skladal po svem, rozesly by se. Cestak, ktery jeste neni ve
    vydajich, cislo dokladu nema a jde pod holy nazev.

    `cisla` jsou hotova cisla z `cisla_dokladu()`; kdo je uz ma (Excel, ZIP),
    preda je, aby se kniha nesestavovala znovu.
    """
    podle_cestaku = (cisla or cisla_dokladu(akce)).cestaky
    nazvy = {}
    for cestak in akce.cestaky:
        kdo = cestak.cestujici.jmeno if cestak.cestujici else cestak.ucel_cesty
        nazev = f"Cesťák - {kdo}"
        nazvy[cestak.id] = (
            f"{podle_cestaku[cestak.id]} - {nazev}" if cestak.id in podle_cestaku else nazev
        )
    return nazvy


def nazvy_slozek(akce: Akce, cisla: CislaDokladu | None = None) -> dict[int, str]:
    """Nazev slozky s cislem jejiho dokladu na zacatku: "VPD 01 - Jídlo - Šindelář".

    Co doklad, to slozka - slozka dostane v knize jedno cislo a uz si ho nese.
    Tim padem muze tez cislo stat na prihradce na disku i v Excelu pro hospodare,
    takze se stazena slozka da spojit s papirem, aniz by se cokoli hledalo.

    Cislo se tu nevymysli: bere se z `doklady()`, stejne jako na tiskopisu.
    Slozka, ve ktere lezi same prilohy, zadny doklad nema (nema radek v knize)
    a zustava pod holym nazvem.

    `cisla` jsou hotova cisla z `cisla_dokladu()` - kdo je uz ma, preda je a
    kniha se nesestavuje znovu.
    """
    podle_slozek = (cisla or cisla_dokladu(akce)).slozky
    return {
        slozka.id: (
            f"{podle_slozek[slozka.id]} - {slozka.nazev}"
            if slozka.id in podle_slozek
            else slozka.nazev
        )
        for slozka in akce.slozky
    }
