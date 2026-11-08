"""Navrh roztrideni skenu do slozek.

Pravidlo: **dokladem je clovek**. Vsechny uctenky jednoho
platice patri do jedne slozky, at uz jsou z jakekoli kategorie - na tiskopisu
se pak rozepisou po polozkach. Kazdemu cloveku se proplaci jeho slozka zvlast,
takze jiny platic je vzdy jina slozka.

Vyjimky z toho jsou dve:

  - **Cestovni prikaz** ma vzdy vlastni slozku, oddelenou od ostatnich uctenek
    teze osoby. Lepi se na nej benzinky, ktere ho dokladaji, a tvori tak jeden
    hotovy papir.
  - **Na jeden doklad se vejde nejvys deset uctenek** (`LIMIT_UCTENEK`) - vic
    uz clovek na formular nenacvaka. Kdyz jich plati vic, sklada se doklad
    po kategoriich: pridava se vzdycky cela kategorie, dokud se jeste vejde.
    Kategorie, ktera sama presahne desitku, se rozreze po deseti od nejstarsi
    uctenky (14x jidlo = deset nejstarsich a ctyri nejmladsi).

Prilohy se do desitky nepocitaji - stejne jako do souctu. Papir se lepi navic
a nakonec prebehne do slozky uctenky, ke ktere je pripnuty.

Pojmenovani je odvozene z obou vzoru v data/vzory (mimo git):
  - kdyz na akci platil jeden jediny clovek  ->  "Jídlo - Lednová víkendovka"
  - kdyz jich platilo vic                    ->  "Jídlo, Jízdné - Šindelář"

Vysledek je vzdy jen navrh. Honza slozky prejmenovava, sluci a rozdeluje.

Sluzba **neuklada** (`commit` je na volajici route) - jen `flush`, aby nove
slozky dostaly id. Jinak by sel slozit vic kroku do jedne transakce a vratit je
(`vsechno_znovu` pak bud projde cele, nebo vubec).
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import DRUH_CESTOVNI_PRIKAZ, Akce, Sken, Slozka

# Kolik uctenek se vejde na jeden doklad. Neni to nastaveni aplikace, ale
# vlastnost papiru - na formular PPD/VPD se jich vic nenalepi.
LIMIT_UCTENEK = 10

# Nejvic znaku v `Slozka.nazev`.
MAX_DELKA_NAZVU_SLOZKY = 200

# Clovek + jestli jde o cestovni prikaz. Podle tohohle klice vznikaji doklady.
Klic = tuple[int, bool]


@dataclass
class VolneMisto:
    """Existujici slozka a kolik uctenek se do ni jeste vejde."""

    slozka: Slozka
    zbyva: int


def _je_cestak(sken: Sken) -> bool:
    return sken.druh == DRUH_CESTOVNI_PRIKAZ


def _vaha(sken: Sken) -> int:
    """Kolik mista sken ubere z desitky. Priloha nula - lepi se navic."""
    return 0 if sken.je_priloha else 1


def _vaha_skupiny(skeny: list[Sken]) -> int:
    return sum(_vaha(s) for s in skeny)


def _klic(sken: Sken) -> Klic | None:
    """Kdo platil + cestak. Bez platice ani bez kategorie skon zaradit neumime.

    Obojí se necha lezet mezi nezarazenymi zamerne - at si toho Honza vsimne
    a doplni to, misto aby uctenka zapadla do cizi slozky.
    """
    if sken.kategorie_id is None or sken.clovek_id is None:
        return None
    return (sken.clovek_id, _je_cestak(sken))


def _platic_slozky(slozka: Slozka) -> int | None:
    """Komu slozka patri.

    Bere se pres `Slozka.platic`, tedy podle uctenek uvnitr - ne z pole
    `clovek_id`. Kdyby se cetlo pole, slozka s prepsanym platicem by mela jiny
    klic nez uctenky, ktere v ni lezi, a dalsi uctenka teze osoby by si zalozila
    druhou stejnou slozku.
    """
    platic = slozka.platic
    return platic.id if platic else None


def _chronologicky(sken: Sken) -> tuple[bool, dt.date, int]:
    """Poradi uctenek uvnitr kategorie. Uctenky bez data az nakonec."""
    return (sken.datum is None, sken.datum or dt.date.min, sken.id or 0)


def _poradi_kategorie(sken: Sken) -> tuple[int, str]:
    """Kategorie se radi jako v ciselniku, at je poradi v nazvu vzdy stejne."""
    kategorie = sken.kategorie
    return (kategorie.poradi, kategorie.nazev) if kategorie else (0, "")


def navrhni_nazev(kategorie: list[str], akce_nazev: str, kdo: str | None, deli_se: bool) -> str:
    cast = ", ".join(kategorie) or "Ostatní"
    nazev = f"{cast} - {kdo}" if deli_se and kdo else f"{cast} - {akce_nazev}"
    # Pevne kategorie maji dlouhe nazvy; slozka s nekolika z nich by se do sloupce
    # (`Slozka.nazev`, 200 znaku) nevesla a formular slozky by ji pak odmital.
    return nazev[:MAX_DELKA_NAZVU_SLOZKY].rstrip()


def _zaber_volny_nazev(navrh: str, obsazene: set[str]) -> str:
    """Kdyz uz se stejny nazev na akci vyskytuje, prida se poradove cislo, a vraceny
    nazev se ZAPISE do `obsazene` (dalsi volani s toutez mnozinou uz ho nevrati).

    Stava se to prave u rozdelene slozky: obe poloviny by se jmenovaly
    "Jídlo - Šindelář", takze druha bude "Jídlo - Šindelář 2".
    """
    nazev, poradi = navrh, 1
    while nazev in obsazene:
        poradi += 1
        nazev = f"{navrh} {poradi}"
    obsazene.add(nazev)
    return nazev


def _bloky(skeny: list[Sken]) -> list[list[Sken]]:
    """Nedelitelne kusy, ze kterych se doklady skladaji.

    Kus je cela jedna kategorie. Kategorii vetsi nez limit rozriznout musime -
    jinak by se nevesla nikam - a reze se od nejstarsi uctenky.
    """
    podle_kategorie: dict[int | None, list[Sken]] = defaultdict(list)
    for sken in skeny:
        podle_kategorie[sken.kategorie_id].append(sken)

    bloky: list[list[Sken]] = []
    for v_kategorii in sorted(
        podle_kategorie.values(), key=lambda skeny: _poradi_kategorie(skeny[0])
    ):
        kus: list[Sken] = []
        vaha = 0
        for sken in sorted(v_kategorii, key=_chronologicky):
            if kus and vaha + _vaha(sken) > LIMIT_UCTENEK:
                bloky.append(kus)
                kus, vaha = [], 0
            kus.append(sken)
            vaha += _vaha(sken)
        if kus:
            bloky.append(kus)
    return bloky


def _do_dokladu(bloky: list[list[Sken]]) -> list[list[Sken]]:
    """Poskláda kusy do dokladu: pridava se, dokud se cely kus jeste vejde."""
    doklady: list[list[Sken]] = []
    aktualni: list[Sken] = []
    vaha = 0
    for blok in bloky:
        if aktualni and vaha + _vaha_skupiny(blok) > LIMIT_UCTENEK:
            doklady.append(aktualni)
            aktualni, vaha = [], 0
        aktualni += blok
        vaha += _vaha_skupiny(blok)
    if aktualni:
        doklady.append(aktualni)
    return doklady


def _slozky_cloveka(akce: Akce, clovek_id: int, cestak: bool) -> list[Slozka]:
    """Slozky, do kterych se uctenka toho cloveka jeste smi pridat.

    Cestaky a bezne uctenky se nemichaji ani tady - slozka, ve ktere uz nejaky
    cestak lezi, beznou uctenku neprijme a naopak.
    """
    vhodne = []
    for slozka in akce.slozky:
        if _platic_slozky(slozka) != clovek_id:
            continue
        radky = slozka.skeny_s_radkem
        if not radky or any(_je_cestak(s) for s in radky) != cestak:
            continue
        vhodne.append(slozka)
    return sorted(vhodne, key=lambda slozka: slozka.poradi)


def roztrid(sezeni: Session, akce: Akce) -> list[Slozka]:
    """Zaradi skeny do slozek. Vraci slozky, ktere pritom vznikly."""
    ke_zpracovani = [s for s in akce.skeny if s.slozka_id is None]
    # Pripnuta priloha se netridi sama - jde tam, kde skonci jeji uctenka.
    pripnute = [s for s in ke_zpracovani if s.je_priloha and s.priloha_k_id is not None]
    # Sken je ORM objekt a porovnava se podle identity, ne podle obsahu.
    pripnute_mnozina = set(pripnute)
    tridene = [s for s in ke_zpracovani if s not in pripnute_mnozina and _klic(s) is not None]

    nove = _zaloz_slozky(akce, tridene) if tridene else []
    for slozka in nove:
        sezeni.add(slozka)

    for priloha in pripnute:
        if priloha.priloha_k is not None and priloha.priloha_k.slozka is not None:
            priloha.slozka = priloha.priloha_k.slozka

    sezeni.flush()
    return nove


def _platici_na_akci(akce: Akce, skupiny: dict[Klic, list[Sken]]) -> set[int]:
    """Lide, kteri na akci platili - z tridenych skenu i z uz hotovych slozek."""
    platici = {clovek_id for clovek_id, _ in skupiny}
    platici |= {p for p in (_platic_slozky(s) for s in akce.slozky) if p is not None}
    return platici


def _dopln_do_existujicich(
    akce: Akce, clovek_id: int, cestak: bool, skeny: list[Sken]
) -> list[list[Sken]]:
    """Nejdriv se dopni to, co uz clovek na akci ma - jinak by kazde dalsi
    roztrideni zakladalo vedle stavajici slozky druhou stejnou. Vraci bloky,
    ktere se do zadne existujici slozky uz nevesly."""
    volne = [
        VolneMisto(slozka, LIMIT_UCTENEK - _vaha_skupiny(slozka.skeny_s_radkem))
        for slozka in _slozky_cloveka(akce, clovek_id, cestak)
    ]
    zbyle: list[list[Sken]] = []
    for blok in _bloky(skeny):
        misto = next((v for v in volne if v.zbyva >= _vaha_skupiny(blok)), None)
        if misto is None:
            zbyle.append(blok)
            continue
        for sken in blok:
            sken.slozka = misto.slozka
        misto.zbyva -= _vaha_skupiny(blok)
    return zbyle


def _nova_slozka(
    akce: Akce,
    doklad: list[Sken],
    clovek_id: int,
    deli_se: bool,
    obsazene_nazvy: set[str],
    poradi: int,
) -> Slozka:
    """Zalozi slozku pro jeden doklad a pripne do ni jeho skeny."""
    kategorie = [s.kategorie.nazev for s in sorted(doklad, key=_poradi_kategorie) if s.kategorie]
    slozka = Slozka(
        akce=akce,
        nazev=_zaber_volny_nazev(
            navrhni_nazev(
                kategorie=list(dict.fromkeys(kategorie)),
                akce_nazev=akce.nazev,
                kdo=doklad[0].kdo_platil,
                deli_se=deli_se,
            ),
            obsazene_nazvy,
        ),
        # Kategorie zustane u slozky jen tehdy, kdyz je v ni jedina.
        # U smisene slozky by kterakoli z nich lhala - ucel platby se
        # pak poskláda z uctenek uvnitr (`Slozka.navrh_ucelu`).
        kategorie_id=(
            doklad[0].kategorie_id if len({s.kategorie_id for s in doklad}) == 1 else None
        ),
        clovek_id=clovek_id,
        poradi=poradi,
    )
    for sken in doklad:
        sken.slozka = slozka
    return slozka


def _zaloz_slozky(akce: Akce, tridene: list[Sken]) -> list[Slozka]:
    """Jadro pravidla: z uctenek jednoho cloveka poskláda doklady."""
    skupiny: dict[Klic, list[Sken]] = defaultdict(list)
    for sken in tridene:
        # `_klic` vraci None jen u skenu bez kategorie nebo cloveka; ty `roztrid`
        # odfiltrovala v `tridene` (`_klic(s) is not None`), pyright to nevidi.
        skupiny[_klic(sken)].append(sken)  # type: ignore[index]

    # Jmeno platice se do nazvu pise, jen kdyz je na akci platicu vic. Kdyz vsechno
    # platil jeden clovek, je jeho jmeno v kazde slozce zbytecne a misto nej se
    # pise nazev akce - tak to ma Honza ve starsich vyuctovanich.
    deli_se = len(_platici_na_akci(akce, skupiny)) > 1

    obsazene_nazvy = {s.nazev for s in akce.slozky}
    nejvyssi_poradi = max((s.poradi for s in akce.slozky), default=-1)
    nove: list[Slozka] = []

    for (clovek_id, cestak), skeny in skupiny.items():
        zbyle = _dopln_do_existujicich(akce, clovek_id, cestak, skeny)
        for doklad in _do_dokladu(zbyle):
            nejvyssi_poradi += 1
            nove.append(
                _nova_slozka(akce, doklad, clovek_id, deli_se, obsazene_nazvy, nejvyssi_poradi)
            )
    return nove


def uklid_prazdne_slozky(sezeni: Session, akce: Akce) -> int:
    """Smaze slozky, ve kterych po presunech nezustal zadny sken. Neuklada."""
    prazdne = [s for s in akce.slozky if not s.skeny]
    for slozka in prazdne:
        sezeni.delete(slozka)
    if prazdne:
        sezeni.flush()
    return len(prazdne)


def vsechno_znovu(sezeni: Session, akce: Akce) -> list[Slozka]:
    """Zahodi vsechny slozky akce a poskláda je znovu podle pravidla.

    Rucni prejmenovani, ucely platby i presuny mezi slozkami se tim ztrati -
    proto se na to routa pta predem. Skeny zustavaji, jen prijdou o slozku.
    Vsechno je jedna transakce - kdyz skladani novych slozek spadne, vrati se
    i smazani starych.
    """
    for sken in akce.skeny:
        sken.slozka = None
    sezeni.flush()
    akce.slozky.clear()
    sezeni.flush()
    return roztrid(sezeni, akce)
