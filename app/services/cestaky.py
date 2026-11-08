"""Vypocet nahrady na cestovnim prikazu a pravidla, co cestak dela s ostatnimi daty.

Zadne HTTP a zadna sit. Vypocty (castky, sazba) jsou cista matematika; pravidla
dole (zapis do pokladni knihy, opis udaju z ciselniku, palivova uctenka) sahaji
na modely a nekdy na databazi, ale nikdy nevolaji `commit` - to dela routa.
Cte odsud obrazovka cestaku, tisk i castka radku v pokladni knize, aby nevysla
tremi zpusoby ruzne.

Postup je overeny na skutecnem papiru z h.skauting.cz:

    cena PHM za km = spotreba / 100 * cena za litr     6/100 * 30,90 = 1,854
    sazba nahrady  = opotrebeni + cena PHM za km       5,00 + 1,854  = 6,854
    cena jizdy     = km * sazba                        7 * 6,854 = 47,978 -> 47,98
    celkem         = soucet jizd, zaokrouhleno DOLU    1 226,88 -> 1 226,00

Nejdulezitejsi detail: na papire se sazba tiskne zaokrouhlena (6,85 Kc/km),
ale pocita se NEZAOKROUHLENA. Se zaokrouhlenou sazbou by 7 km dalo 47,95
misto spravnych 47,98 - na dvaceti radcich je to rozdil pres korunu. Proto se
sazba nikde neuklada, jen se pokazde spocita ze trech ulozenych vstupu.

Zaokrouhluje se tady, ne az v sablone. Filtr `koruny` pouziva format(), ktery
zaokrouhluje na sudou cislici (ROUND_HALF_EVEN) - obrazovka a papir by se
u pulhaleru mohly rozejit.
"""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.formulare import MAX_KM
from app.models import (
    SMER_VYDEJ,
    STAV_UZAVRENA,
    Akce,
    Cestak,
    Clovek,
    Jizda,
    PokladniRadek,
    Sken,
    Vozidlo,
)
from app.services import pokladni_kniha
from app.services.ciselniky import dalsi_poradi

NULA = Decimal("0.00")
HALERE = Decimal("0.01")
CELE_KORUNY = Decimal(1)
STO = Decimal(100)

# Kolik radku rozpisu se vejde na jednu A4. Zmereno na hotovem tiskopisu,
# viz komentar u `.doklad.cestak` v app/static/tisk.css. Pri vetsim poctu se
# papir prelije na druhou stranu - nic se neztrati, ale obrazovka to rekne
# dopredu, aby to Honzu u tiskarny neprekvapilo.
JIZD_NA_STRANKU = 14


def na_zobrazeni(hodnota: Decimal) -> Decimal:
    """Dve desetinna mista pro oci. Nikdy se nevraci zpatky do vypoctu."""
    return hodnota.quantize(HALERE, rounding=ROUND_HALF_UP)


def cena_phm_za_km(spotreba: Decimal | None, cena_za_litr: Decimal | None) -> Decimal:
    """Kolik stoji palivo na jeden kilometr. Nezaokrouhlene."""
    if not spotreba or not cena_za_litr:
        return NULA
    return (spotreba / STO) * cena_za_litr


def sazba(
    opotrebeni: Decimal | None,
    spotreba: Decimal | None,
    cena_za_litr: Decimal | None,
) -> Decimal:
    """Nahrada za jeden kilometr. Nezaokrouhlena - viz docstring modulu."""
    return (opotrebeni or NULA) + cena_phm_za_km(spotreba, cena_za_litr)


def sazba_cestaku(cestak: Cestak) -> Decimal:
    return sazba(cestak.opotrebeni, cestak.spotreba, cestak.cena_za_litr)


def cena_jizdy(jizda: Jizda, sazba_nahrady: Decimal) -> Decimal:
    """Nahrada za jeden usek.

    U auta a motocyklu kilometry krat sazba; u vlaku a autobusu proste
    zaplacene jizdne, protoze tam se nic nepocita.
    """
    if not jizda.je_na_km:
        return na_zobrazeni(jizda.jizdne or NULA)
    if not jizda.km:
        return NULA
    return na_zobrazeni(Decimal(jizda.km) * sazba_nahrady)


def ceny_jizd(cestak: Cestak) -> list[tuple[Jizda, Decimal]]:
    """Jizdy i s jejich cenou. Sazba se pocita jednou pro cely cestak."""
    sazba_nahrady = sazba_cestaku(cestak)
    return [(jizda, cena_jizdy(jizda, sazba_nahrady)) for jizda in cestak.jizdy]


def soucet_radku(cestak: Cestak) -> Decimal:
    """Soucet cen vsech jizd, jeste bez zaokrouhleni na cele koruny."""
    return sum((cena for _, cena in ceny_jizd(cestak)), NULA)


def celkem(cestak: Cestak) -> Decimal:
    """Vysledna nahrada. Zaokrouhluje se DOLU na cele koruny.

    ROUND_FLOOR, ne ROUND_DOWN - u kladnych castek delaji totez, ale kdyby
    nekdo zadal zaporne jizdne, FLOOR zaokrouhli dal od nuly a nevznikne
    z toho nahoda ve prospech cestujiciho.
    """
    return soucet_radku(cestak).quantize(CELE_KORUNY, rounding=ROUND_FLOOR)


def pocet_km(cestak: Cestak) -> Decimal:
    """Ujete kilometry. Vlak a autobus se nepocitaji - tam se platilo jizdne."""
    return sum((Decimal(j.km or 0) for j in cestak.jizdy if j.je_na_km), Decimal(0))


def datum_zacatku(cestak: Cestak) -> dt.date | None:
    return _prvni_datum(cestak.jizdy)


def datum_konce(cestak: Cestak) -> dt.date | None:
    return _prvni_datum(reversed(cestak.jizdy))


def misto_zacatku(cestak: Cestak) -> str | None:
    for jizda in cestak.jizdy:
        if jizda.odkud:
            return jizda.odkud
    return None


def misto_konce(cestak: Cestak) -> str | None:
    for jizda in reversed(cestak.jizdy):
        if jizda.kam:
            return jizda.kam
    return None


def ucel_v_knize(cestak: Cestak) -> str:
    """Text, pod kterym cestak vystupuje v pokladni knize a na tiskopisu VPD."""
    return f"Cestovní příkaz – {cestak.ucel_cesty}"


def sesynchronizuj(cestak: Cestak) -> None:
    """Dorovna radek v pokladni knize podle cestaku.

    Vola se po kazde zmene cestaku i jeho jizd. Bez toho by se soucet jizd
    a castka v knize rozesly - a hospodari by nesedelo vyuctovani.

    Datum radku je den posledni jizdy: kolonka "Ze dne" na dokladu ma byt den,
    kdy penize pohnuly, a cestak se proplaci po navratu, ne pred odjezdem.
    """
    radek = cestak.pokladni_radek
    if radek is None:
        return
    radek.castka = celkem(cestak)
    radek.ucel = ucel_v_knize(cestak)
    radek.datum = datum_konce(cestak)


def presun(cestak: Cestak, jizda_id: int, dolu: bool) -> None:
    """Posune jednu jizdu o radek nahoru nebo dolu.

    Prohodi poradi dvou sousedu - stejne jako sipky v pokladni knize.
    Mimo kraj seznamu se nedeje nic, tlacitko je tam stejne vypnute.
    """
    jizdy = list(cestak.jizdy)
    mista = {j.id: poradi for poradi, j in enumerate(jizdy)}
    if jizda_id not in mista:
        return

    kde = mista[jizda_id]
    soused = kde + 1 if dolu else kde - 1
    if not 0 <= soused < len(jizdy):
        return

    jizdy[kde], jizdy[soused] = jizdy[soused], jizdy[kde]
    for poradi, jizda in enumerate(jizdy):
        jizda.poradi = poradi


def _prvni_datum(jizdy) -> dt.date | None:
    """Prvni vyplnene datum v poradi, ve kterem jizdy prijdou.

    Zamerne se nehleda nejmensi/nejvetsi datum: poradi radku si urcuje clovek
    sipkami a papir ma vypadat tak, jak ho srovnal on.
    """
    for jizda in jizdy:
        if jizda.datum:
            return jizda.datum
    return None


# --- Pravidla cestaku vuci ostatnim datum -------------------------------------


def zapis_do_vydaju(cestak: Cestak) -> PokladniRadek:
    """Zapise cestak do pokladni knihy jako vydajovy radek (bez `commit`).

    Palivova uctenka, ze ktere se vzala cena za litr, se tim zaroven stava
    prilohou dokladu - vypise se na tiskopisu VPD (pokladni_kniha._prilohy_rucniho).
    """
    radek = PokladniRadek(
        akce=cestak.akce,
        smer=SMER_VYDEJ,
        castka=celkem(cestak),
        ucel=ucel_v_knize(cestak),
        datum=datum_konce(cestak),
        poradi=dalsi_poradi(r.poradi for r in cestak.akce.pokladni_radky),
    )
    cestak.pokladni_radek = radek
    _odepni_palivovku(cestak)
    return radek


def co_chybi(cestak: Cestak) -> list[str]:
    """Co cestaku chybi, nez ma smysl ho dat do vydaju.

    Chybejici vstup se jinak projevi jen jako nizsi cislo, ktere na pohled vypada
    platne: jizda autem bez km dava 0 Kc, bez vozidla nebo ceny za litr chybi
    PHM a hospodar by dostal nahradu bez nej, aniz by na to appka upozornila.
    Vety jdou do hlasky, ktera lezi v cookie (jen podepsane) - proto jen cisla
    jizd, nikdy mista z nich.
    """
    if not cestak.jizdy:
        return ["Cesťák bez jediné jízdy by byl na nulu — přidej nejdřív jízdy."]
    chybi = []
    for cislo, jizda in enumerate(cestak.jizdy, start=1):
        if jizda.je_na_km and not jizda.km:
            chybi.append(f"Jízda č. {cislo}: chybí kilometry.")
        elif not jizda.je_na_km and not jizda.jizdne:
            chybi.append(f"Jízda č. {cislo}: chybí jízdné.")
    # Sazba se pocita jen z jizd na km - cesta jen vlakem ji nepotrebuje.
    if any(jizda.je_na_km for jizda in cestak.jizdy):
        if cestak.opotrebeni is None:
            chybi.append("Chybí sazba opotřebení (Kč/km).")
        if not cestak.spotreba:
            chybi.append("Chybí spotřeba — vyber vozidlo.")
        if not cestak.cena_za_litr:
            chybi.append("Chybí cena za litr — vyber palivovou účtenku nebo ji napiš.")
    return chybi


def odeber_z_vydaju(cestak: Cestak) -> bool:
    """Smaze radek cestaku z pokladni knihy; cestak je zase rozpracovany (bez `commit`).

    Palivova uctenka se vrati do slozky, ve ktere lezela pred pridanim do vydaju.
    Vraci True, kdyz uctenka zustala nezarazena a clovek ji tam mohl cekat -
    puvodni slozka mezitim zmizela (smazana, pretrideni od nuly), nebo zadnou
    nemela; z databaze to poznat nejde, `ON DELETE SET NULL` to zapomene.
    """
    cestak.pokladni_radek = None
    return _vrat_palivovku(cestak)


def _odepni_palivovku(cestak: Cestak) -> None:
    """Vyradi palivovou uctenku ze slozky, ve ktere dosud lezela.

    Uctenka se lepi na doklad cestaku, takze uz nesmi byt vypsana na dokladu
    sve puvodni slozky - nalepit se da jen na jeden papir. Ze souctu ji to
    nevyradi, priloha se nikde nepocita uz ted.
    """
    uctenka = cestak.palivova_uctenka
    # Ucenka jine akce (starsi data, podvrzeny formular) se nesmi vyradit z jejiho
    # dokladu - vyuctovani te akce by se rozjelo s pokladni knihou.
    if uctenka is None or uctenka.akce_id != cestak.akce_id:
        return
    cestak.puvodni_slozka_palivovky_id = uctenka.slozka_id
    uctenka.slozka_id = None
    uctenka.priloha_k_id = None


def _vrat_palivovku(cestak: Cestak) -> bool:
    """Vrati palivovou uctenku do slozky z doby pred zapisem do vydaju.

    Vazbu `priloha_k_id` nevraci - mirila na doklad cestaku, ktery uz neexistuje.
    Kdyz clovek uctenku mezitim sam dal do jine slozky, zustane tam. Vraci True,
    kdyz zustala nezarazena (viz `odeber_z_vydaju`).
    """
    puvodni = cestak.puvodni_slozka_palivovky_id
    cestak.puvodni_slozka_palivovky_id = None
    uctenka = cestak.palivova_uctenka
    if uctenka is None or uctenka.akce_id != cestak.akce_id:
        return False
    if uctenka.slozka_id is not None:
        return False
    if puvodni is not None and any(s.id == puvodni for s in cestak.akce.slozky):
        uctenka.slozka_id = puvodni
        return False
    return True


def nastav_cestujiciho(sezeni: Session, cestak: Cestak, cislo: int | None) -> None:
    """Zmena cestujiciho prepise i adresu a kontakt.

    Udaje se z ciselniku OPISUJI, neodkazuji - papir uz uzavrene akce nesmi zmenit
    pozdejsi oprava ciselniku. Opravu v ciselniku Lide promita
    `promit_udaje_cloveka` jen do cestaku v neuzavrenych akcich.
    """
    clovek = sezeni.get(Clovek, cislo) if cislo else None
    # Neexistujici ID (podvrzene) se bere jako nevybrano - cizi klic by jinak
    # shodil ulozeni chybou 500.
    cestak.cestujici_id = clovek.id if clovek is not None else None
    if clovek is not None:
        cestak.adresa = clovek.adresa
        cestak.kontakt = clovek.kontakt


def nastav_vozidlo(sezeni: Session, cestak: Cestak, cislo: int | None) -> None:
    """Zmena vozidla prepise i spotrebu - je to otisk z technickeho prukazu.

    Pozdejsi opravu spotreby v ciselniku promita `promit_spotrebu_vozidla`.
    """
    vozidlo = sezeni.get(Vozidlo, cislo) if cislo else None
    cestak.vozidlo_id = vozidlo.id if vozidlo is not None else None
    if vozidlo is not None:
        cestak.spotreba = vozidlo.spotreba


def nastav_palivo(
    sezeni: Session, cestak: Cestak, akce_id: int, cislo: int | None, rucni_cena: Decimal | None
) -> None:
    """Vyber palivove uctenky prepise cenu za litr tim, co z ni prectla AI.

    Kdyz uctenka cenu nenese (AI ji neprectla), zustane rucne napsana hodnota -
    jinak by vyber uctenky cenu vymazal. Uctenka musi patrit akci cestaku;
    jina (podvrzene id) se bere jako nevybrana. `akce_id` se predava zvlast,
    protoze u nove zakladaneho cestaku jeste zadnou akci nema.
    """
    sken = sezeni.get(Sken, cislo) if cislo else None
    if sken is None or sken.akce_id != akce_id:
        cislo, sken = None, None
    zmenila_se = cislo != cestak.palivova_uctenka_id
    ve_vydajich = zmenila_se and cestak.pokladni_radek is not None
    if ve_vydajich:
        # Stara uctenka uz na dokladu cestaku nebude - vrati se do sve slozky.
        _vrat_palivovku(cestak)
    cestak.palivova_uctenka_id = cislo
    if zmenila_se:
        # Zapamatovana slozka patri puvodni uctence - jina do ni nesmi.
        cestak.puvodni_slozka_palivovky_id = None
    if ve_vydajich:
        # Nova se lepi na doklad cestaku, takze nesmi zustat vypsana i ve sve slozce.
        # Relace se nastavuje vedle id, protoze jinak by `_odepni_palivovku` cetla starou.
        cestak.palivova_uctenka = sken
        _odepni_palivovku(cestak)

    if zmenila_se and sken is not None and sken.cena_za_litr is not None:
        cestak.cena_za_litr = sken.cena_za_litr
        return
    cestak.cena_za_litr = rucni_cena


def _cestaky_otevrenych_akci(sezeni: Session, *podminky) -> list[Cestak]:
    """Cesťáky z akcí, které nejsou uzavřené, podle zadaných podmínek.

    Uzavřená akce je jen pro čtení - její vyúčtování se zpětně nemění, proto ji
    oprava číselníku ani účtenky nikdy nezasáhne.
    """
    return list(
        sezeni.scalars(
            select(Cestak)
            .join(Akce, Cestak.akce_id == Akce.id)
            .where(Akce.stav != STAV_UZAVRENA, *podminky)
        )
    )


def promit_spotrebu_vozidla(sezeni: Session, vozidlo: Vozidlo) -> int:
    """Po opravě spotřeby vozidla ji přepíše do cesťáků v otevřených akcích.

    Spotřeba se na cesťák při výběru vozidla opisuje (viz `nastav_vozidlo`), takže
    bez tohohle by oprava v číselníku zůstala viditelná jen u nově založených
    cesťáků. Cesťáky uzavřených akcí zůstávají s původní hodnotou. Částka v knize
    se dorovná; ručně zadaná cena za litr a opotřebení se nemění. Volající musí mít
    před načtením vozidla `db.zamkni_zapis` a po volání dělá `commit`.
    Vrací počet přepsaných cesťáků.
    """
    prepsano = 0
    for cestak in _cestaky_otevrenych_akci(sezeni, Cestak.vozidlo_id == vozidlo.id):
        if cestak.spotreba != vozidlo.spotreba:
            cestak.spotreba = vozidlo.spotreba
            sesynchronizuj(cestak)
            prepsano += 1
    return prepsano


def promit_udaje_cloveka(sezeni: Session, clovek: Clovek) -> int:
    """Po opravě bydliště či kontaktu člověka je přepíše do jeho cesťáků.

    Adresa a kontakt se na cesťák při výběru cestujícího opisují (viz
    `nastav_cestujiciho`) a v cesťáku se ručně upravit nedají - jediný zdroj je
    číselník Lidé, takže se tu nemá co přepsat omylem. Cesťáky uzavřených akcí
    zůstávají s tím, co bylo na vytištěném papíru. Volající má před načtením
    člověka `db.zamkni_zapis` a po volání dělá `commit`. Vrací počet cesťáků.
    """
    prepsano = 0
    for cestak in _cestaky_otevrenych_akci(sezeni, Cestak.cestujici_id == clovek.id):
        if cestak.adresa != clovek.adresa or cestak.kontakt != clovek.kontakt:
            cestak.adresa = clovek.adresa
            cestak.kontakt = clovek.kontakt
            prepsano += 1
    return prepsano


def promit_cenu_za_litr_z_uctenky(sezeni: Session, sken: Sken, puvodni: Decimal | None) -> int:
    """Po opravě ceny za litr na palivové účtence ji přepíše do cesťáků.

    Přepíše jen cesťáky, které mají ještě tu cenu, kterou účtenka nesla před
    opravou - tedy opsanou z ní. Cena, kterou člověk na cesťáku napsal ručně
    (je jiná než původní z účtenky), se nemění. Jen otevřené akce; volající má
    `db.zamkni_zapis` a dělá `commit`. Vrací počet přepsaných cesťáků.
    """
    if sken.cena_za_litr == puvodni:
        return 0
    prepsano = 0
    for cestak in _cestaky_otevrenych_akci(sezeni, Cestak.palivova_uctenka_id == sken.id):
        if cestak.cena_za_litr == puvodni:
            cestak.cena_za_litr = sken.cena_za_litr
            sesynchronizuj(cestak)
            prepsano += 1
    return prepsano


def posledni_sazba_opotrebeni(sezeni: Session) -> Decimal | None:
    """Sazba z naposledy zalozeneho cestaku.

    Zakladni nahrada za kilometr se meni vyhlaskou jednou za rok. Nabidnout tu
    minulou je presnejsi nez nechat pole prazdne a lepsi nez cislo natvrdo
    v kodu, ktere by zestarlo.
    """
    return sezeni.scalar(
        select(Cestak.opotrebeni)
        .where(Cestak.opotrebeni.is_not(None))
        .order_by(Cestak.id.desc())
        .limit(1)
    )


def cislo_dokladu(cestak: Cestak) -> str | None:
    """Cislo VPD, ktere cestak dostal v pokladni knize.

    Bere se z `pokladni_kniha.doklady()` - jedineho mista, kde se cisla
    prideluji - aby "VPD 03" na cestaku bylo tyz doklad jako "VPD 03" v knize.
    """
    radek = cestak.pokladni_radek
    if radek is None:
        return None
    for doklad in pokladni_kniha.doklady(cestak.akce):
        if doklad.zdroj == pokladni_kniha.ZDROJ_RUCNI and doklad.id == radek.id:
            return doklad.oznaceni
    return None


def popis_prostredku(cestak: Cestak) -> str:
    """Kolonka "4. Urceny dopr. prostredek" na papire.

    Sklada se z prostredku prvni jizdy a vozidla: "auto vlastní – Škoda Octavia
    - modrá (1AB2345)". Prostredek se na cestaku neuklada zvlast - kazda
    jizda ma svuj, aby sel jeden prikaz pouzit na cestu tam autem a zpatky
    vlakem.
    """
    casti = []
    if cestak.jizdy:
        casti.append(cestak.jizdy[0].nazev_prostredku)
    if cestak.vozidlo is not None:
        auto = cestak.vozidlo.nazev
        if cestak.vozidlo.spz:
            auto = f"{auto} ({cestak.vozidlo.spz})"
        casti.append(auto)
    return " – ".join(casti)


def vycisti_nepouzitou(jizda: Jizda) -> None:
    """Po prepnuti prostredku smaze hodnotu, kterou uz radek nepouziva.

    Kdyby v radku po prepnuti na vlak zustaly kilometry, vypadalo by to na
    papire, ze se ujely - a pritom se platilo jizdne.
    """
    if jizda.je_na_km:
        jizda.jizdne = None
    else:
        jizda.km = None


def prepni_prostredek(jizda: Jizda, prostredek: str) -> None:
    """Nastaví prostředek jízdy a přenese číslo do sloupce, který nový prostředek čte.

    Jeden sloupec na papíře, dva v databázi: u auta a motocyklu jsou to kilometry
    (`km`), u vlaku a dalších cena jízdenky (`jizdne`). Prohlížeč po změně jen
    výběru prostředku pošle jen toto pole (`js/prubezne.js`), takže číslo, které
    člověk vidí v „Km nebo cena“, musí přejít na serveru: auto 120 km -> vlak 120 Kč
    a zpět. Mezi dvěma prostředky téhož druhu (auto/motocykl, vlak/autobus) se nic
    nemění. Kilometry mají jedno desetinné místo (ROUND_HALF_UP jako u mapy); cena
    nad mezí kilometrů se nepřenese (po přepnutí na auto zůstane pole prázdné).
    """
    bylo_na_km = jizda.je_na_km
    jizda.prostredek = prostredek
    if bylo_na_km == jizda.je_na_km:
        return
    if jizda.je_na_km:
        cislo = jizda.jizdne
        jizda.jizdne = None
        if cislo is not None and cislo <= MAX_KM:
            jizda.km = cislo.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        else:
            jizda.km = None
    else:
        jizda.jizdne = jizda.km
        jizda.km = None
