"""Seznam obdarovanych - kdo na akci co dostal.

Radek je jeden clovek. Co dostal, visi na nem jako veci (`Dar`) a muze toho
byt vic; do Excelu se pak vypisou za sebou do jedne bunky.

Veci se neopisuji rucne. Vytahnou se AI z uctenek, ktere maji kategorii
vecnych daru, spadnou do zasobniku vpravo a odtamtud se rozdaji - bud
pretazenim mysi, nebo tlacitkem, ktere je rozhodi spravedlive samo.

Udaje o lidech se sem ze seznamu ucastniku opisuji (viz komentar u modelu
Obdarovany), takze seznam prezije i novy import ucastniku a jde do nej pridat
i clovek, ktery ucastnik neni.

Data obsahuji adresy a data narozeni deti. Zpracovavaji se lokalne a nikdy
neodchazi do Gemini - do nej jde jen fotka uctenky, stejne jako u skenu.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.formaty import mnozne
from app.formulare import id_z_textu, over_delky
from app.models import Dar, Obdarovany, Sken, dnes
from app.routes.spolecne import (
    MAX_POLI_UCASTNICI,
    Databaze,
    datum_narozeni_z_formulare,
    hlaska_dat_narozeni,
    nacti_akci,
    nacti_formular,
    nacti_otevrenou_akci,
    over_otevrenou,
    prazdne_na_none,
    prubezna_chyba,
    prubezne_ulozeni,
    text_z_formulare,
    uz_smazano,
    uz_smazano_kam,
)
from app.sablony import oznam, strana, zpet
from app.services import ciselniky, cteni_ai, ocr, rozdeleni_daru, tarif
from app.services.ciselniky import dalsi_poradi

router = APIRouter()

# Vety pro bublinu pri rozdavani veci, kdyz cil mezitim zmizel.
HLASKA_DAR_NENI = "Tahle věc už neexistuje — obnov stránku."
HLASKA_CLOVEK_NENI = "Ten člověk už v seznamu není — obnov stránku."

# Pole, ktera jdou u obdarovaneho prepsat tuztickou. Klic ve formulari je
# "<pole>_<id radku>" - stejne jako u ucastniku. Adresa a datum narozeni se
# tu vedome needituji: opisuji se z ucastniku a v tehle tabulce by jen
# prekazely - opravuji se v Ucastnicich.
TEXTOVA_POLE = ("jmeno", "prijmeni", "prezdivka")

# Nejvic daru z jedne uctenky. "3x mic" se rozmnozi na tri radky, takze by jedna
# odpoved AI (nebo zacyklena) mohla zalozit tisice radku.
MAX_DARU_Z_UCTENKY = 300


def _dalsi_poradi(akce) -> int:
    """Cislo za poslednim radkem. Nove radky se pridavaji na konec seznamu."""
    return dalsi_poradi((o.poradi for o in akce.obdarovani), zacatek=1)


def _dalsi_poradi_veci(akce) -> int:
    return dalsi_poradi((d.poradi for d in akce.dary), zacatek=1)


def _pridej(akce, radek: Obdarovany) -> None:
    """Zaradi radek na konec seznamu akce.

    Zamerne pres `akce.obdarovani`, ne pres `sezeni.add`: seznam akce se tim
    rovnou dozvi o novem radku. Kdyby se radek pridal jen do sezeni, drzela
    by akce porad stary seznam a dalsi radek by dostal stejne poradi.
    """
    akce.obdarovani.append(radek)


def _uctenky_s_dary(sezeni, akce) -> list[Sken]:
    """Skeny akce, ktere maji kategorii vecnych daru.

    Prilohy se preskakuji ze stejneho duvodu jako vsude jinde - papir dolozeny
    k uctence neni nakup a neni z ceho na nem vypisovat veci.
    """
    kategorie = ciselniky.kategorie_daru(sezeni)
    if kategorie is None:
        return []
    return [
        sken
        for sken in sorted(akce.skeny, key=lambda s: s.poradi)
        if sken.kategorie_id == kategorie.id and not sken.je_priloha
    ]


def _je_prectena(akce, sken: Sken) -> bool:
    """Uz se z uctenky veci vytahovaly? Staci vec, nebo priznak "0 veci"."""
    return sken.dary_precteny or any(dar.sken_id == sken.id for dar in akce.dary)


def _na_rade(akce, uctenky: list[Sken]) -> list[Sken]:
    """Uctenky, ktere se maji cist: jeste neprectene a bez tri neuspechu."""
    return [s for s in uctenky if not _je_prectena(akce, s) and cteni_ai.ma_se_cist(s)]


def _zaloz_dary(akce, sken: Sken, vysledek: ocr.VysledekDaru) -> tuple[int, bool]:
    """Zalozi z vysledku cteni veci. Vrati (kolik jich pribylo, zda se oriznuly).

    Kus = radek. "3x mic" je tedy trikrat "mic" - jinak by nesel kazdy mic
    dat nekomu jinemu a nesedelo by automaticke rozhozeni.
    """
    poradi = _dalsi_poradi_veci(akce)
    pridano = 0
    for polozka in vysledek.veci:
        nazev = (polozka.nazev or "").strip()
        if not nazev:
            continue
        # Zaporny nebo nesmyslne velky pocet je preklep AI, ne padesat micu.
        for _ in range(max(1, min(polozka.pocet, 99))):
            if pridano >= MAX_DARU_Z_UCTENKY:
                return pridano, True
            akce.dary.append(Dar(akce_id=akce.id, sken_id=sken.id, nazev=nazev, poradi=poradi))
            poradi += 1
            pridano += 1
    return pridano, False


def _hlaska_o_oriznuti(request: Request, sken: Sken) -> None:
    oznam(
        request,
        f"Z účtenky {sken.puvodni_nazev} je věcí nezvykle moc, ořezal jsem je na "
        f"{MAX_DARU_Z_UCTENKY}. Zkontroluj ji.",
        "pozor",
    )


@router.get("/akce/{akce_id}/obdarovani", response_class=HTMLResponse)
async def prehled(request: Request, akce_id: int, sezeni: Databaze):
    akce = nacti_akci(sezeni, akce_id)
    uctenky = _uctenky_s_dary(sezeni, akce)
    # Ktery klic ke Gemini je vybrany, je spolecne pro celou aplikaci - stejny
    # prepinac je i v Uctenkach a drzi ho prihlasovaci cookie.
    vybrany_tarif = tarif.aktualni(request.session)
    return strana(
        request,
        "obdarovani.html",
        akce=akce,
        kategorie_daru=ciselniky.kategorie_daru(sezeni),
        uctenky=uctenky,
        # Uctenka je "prectena", kdyz z ni uz nejaka vec vzesla, nebo AI rekla,
        # ze zadne nejsou. Druhe kliknuti na tlacitko tedy nezacne veci zdvojovat.
        neprectene=_na_rade(akce, uctenky),
        nepovedene=[
            s for s in uctenky if not _je_prectena(akce, s) and cteni_ai.nepodarilo_se_precist(s)
        ],
        pocty_veci={sken.id: sum(1 for d in akce.dary if d.sken_id == sken.id) for sken in uctenky},
        zasobnik=[dar for dar in akce.dary if dar.obdarovany_id is None],
        tarif=vybrany_tarif,
        davka=tarif.davka(vybrany_tarif),
        placeny_klic=tarif.prepinac_se_nabizi(),
    )


@router.post("/akce/{akce_id}/obdarovani/z-ucastniku")
async def pridat_z_ucastniku(request: Request, akce_id: int, sezeni: Databaze):
    """Prida vybrane ucastniky do seznamu obdarovanych, zatim bez veci."""
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    formular = await nacti_formular(request, MAX_POLI_UCASTNICI)
    vybrani = {
        cislo
        for klic in formular
        if klic.startswith("ucastnik_")
        and (cislo := id_z_textu(klic.removeprefix("ucastnik_"))) is not None
    }
    if not vybrani:
        oznam(request, "Nevybral jsi nikoho.", "chyba")
        return zpet(f"/akce/{akce_id}/obdarovani")

    # Radek je clovek, takze tyz clovek v seznamu podruhe nema co delat -
    # dve veci se mu daji obe k jednomu jmenu. Poznava se podle jmena, protoze
    # udaje jsou opsane, ne odkazovane.
    uz_v_seznamu = {
        (o.jmeno or "").strip().lower() + "|" + (o.prijmeni or "").strip().lower()
        for o in akce.obdarovani
    }

    poradi = _dalsi_poradi(akce)
    pridano, preskoceno = 0, 0
    for ucastnik in akce.ucastnici:
        if ucastnik.id not in vybrani:
            continue
        podpis = (
            (ucastnik.jmeno or "").strip().lower() + "|" + (ucastnik.prijmeni or "").strip().lower()
        )
        if podpis in uz_v_seznamu:
            preskoceno += 1
            continue
        _pridej(akce, Obdarovany.z_ucastnika(ucastnik, poradi))
        uz_v_seznamu.add(podpis)
        poradi += 1
        pridano += 1
    sezeni.commit()

    if pridano:
        kolik = mnozne(pridano, "člověk", "lidé", "lidí")
        oznam(request, f"Přidáno: {kolik}. Zbývá jim rozdat věci.")
    if preskoceno:
        oznam(
            request,
            f"{mnozne(preskoceno, 'člověk už', 'lidé už', 'lidí už')} v seznamu byli — "
            "víc věcí se dává k jednomu jménu.",
        )
    return zpet(f"/akce/{akce_id}/obdarovani")


@router.post("/akce/{akce_id}/obdarovani/pridat")
async def pridat_prazdny(request: Request, akce_id: int, sezeni: Databaze):
    """Prazdny radek pro nekoho, kdo v seznamu ucastniku neni."""
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    _pridej(akce, Obdarovany(poradi=_dalsi_poradi(akce)))
    sezeni.commit()
    oznam(request, "Přidán prázdný řádek. Dopiš jméno tužtičkou.")
    return zpet(f"/akce/{akce_id}/obdarovani")


@router.post("/akce/{akce_id}/obdarovani/ulozit")
async def ulozit(request: Request, akce_id: int, sezeni: Databaze):
    """Ulozi to, co Honza prepsal tuztickou v tabulce."""
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    formular = await nacti_formular(request)
    over_delky(formular, {pole: getattr(Obdarovany, pole) for pole in TEXTOVA_POLE})
    nepripustnych_dat = 0

    for radek in akce.obdarovani:
        for pole in TEXTOVA_POLE:
            klic = f"{pole}_{radek.id}"
            if klic in formular:
                setattr(radek, pole, prazdne_na_none(text_z_formulare(formular, klic)))
        klic = f"datum_narozeni_{radek.id}"
        if klic in formular:
            radek.datum_narozeni, ok = datum_narozeni_z_formulare(
                text_z_formulare(formular, klic),
                radek.datum_narozeni,
                akce.datum_od or dnes(),
            )
            nepripustnych_dat += not ok

    sezeni.commit()
    if (hotovo := prubezne_ulozeni(request, 0, nepripustnych_dat)) is not None:
        return hotovo
    oznam(request, "Uloženo.")
    if nepripustnych_dat:
        oznam(request, hlaska_dat_narozeni(nepripustnych_dat), "chyba")
    return zpet(f"/akce/{akce_id}/obdarovani")


# --- veci k rozdani -------------------------------------------------------


@router.post("/akce/{akce_id}/obdarovani/vycist")
def vycist(request: Request, akce_id: int, sezeni: Databaze):
    """Vytahne veci z dalsi davky uctenek za vecne dary. Postupne, ne paralelne.

    Cte se jen na vyzadani a jen uctenky, ze kterych jeste zadna vec nevzesla -
    druhe kliknuti tedy nic nezdvoji a uz rozdane veci nechá být.

    Obycejna `def` a commit po kazde uctence ze stejnych duvodu jako
    `skeny.precist`: server se nezastavi a zapis nedrzi zamek po davku.
    """
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    uctenky = _uctenky_s_dary(sezeni, akce)
    if not uctenky:
        oznam(
            request,
            "Žádná účtenka nemá kategorii věcných darů. Nastav ji v Účtenkách.",
            "chyba",
        )
        return zpet(f"/akce/{akce_id}/obdarovani")

    na_rade = _na_rade(akce, uctenky)
    if not na_rade:
        if any(not _je_prectena(akce, s) for s in uctenky):
            oznam(
                request,
                "Zbylé účtenky se nepodařilo přečíst ani na třetí pokus. "
                "U každé je tlačítko „Zkusit znovu“.",
                "chyba",
            )
        else:
            oznam(request, "Není co číst — ze všech účtenek už jsou věci vytažené.")
        return zpet(f"/akce/{akce_id}/obdarovani")

    vybrany_tarif = tarif.aktualni(request.session)
    pridano, chyby = 0, []
    for sken in na_rade[: tarif.davka(vybrany_tarif)]:
        try:
            vysledek = cteni_ai.precti_dary(
                sezeni, sken, vybrany_tarif=vybrany_tarif, uzivatel_id=request.state.uzivatel.id
            )
        except cteni_ai.ChybaCteni as chyba:
            chyby.append(f"{sken.puvodni_nazev}: {chyba}")
            sezeni.commit()  # utrata a pocitadlo pokusu se ulozi hned
            if chyba.zastavit_davku:
                break  # limit, klic nebo vypadek - dalsi uctenka by dopadla stejne
            continue
        kolik, oriznuto = _zaloz_dary(akce, sken, vysledek)
        sken.dary_precteny = True
        sezeni.commit()
        pridano += kolik
        if oriznuto:
            _hlaska_o_oriznuti(request, sken)

    zbyva = len(na_rade) - tarif.davka(vybrany_tarif)
    oznam(
        request,
        f"Vytaženo {mnozne(pridano, 'věc', 'věci', 'věcí')}."
        + (f" Zbývá {mnozne(zbyva, 'účtenka', 'účtenky', 'účtenek')}." if zbyva > 0 else ""),
    )
    for chyba in chyby:
        oznam(request, f"Nepodařilo se přečíst — {chyba}", "chyba")
    return zpet(f"/akce/{akce_id}/obdarovani")


@router.post("/sken/{sken_id}/dary/znovu")
def precist_znovu(request: Request, sken_id: int, sezeni: Databaze):
    """Zahodi veci z jedne uctenky a precte ji znovu.

    Pro pripad, ze AI z uctenky vytahla nesmysly. Rozdane veci z te uctenky
    se zahodi taky - jinak by u lidi zustaly stare nazvy vedle novych.

    Nejdriv se cte, az potom se stare veci mazou: kdyz cteni selze, zustanou
    veci beze zmeny a zaplacena utrata se presto ulozi.
    """
    sken = sezeni.get(Sken, sken_id)
    if sken is None:
        return uz_smazano(request)
    over_otevrenou(sken.akce)
    akce = sken.akce

    # Vedome kliknuti na sipku smi cist i uctenku, ktera uz vycerpala pokusy.
    # Ulozi se hned, at pri cteni (sekundy) nezustane zapis rozepsany.
    sken.pokusy_cteni = 0
    sezeni.commit()
    try:
        vysledek = cteni_ai.precti_dary(
            sezeni,
            sken,
            vybrany_tarif=tarif.aktualni(request.session),
            uzivatel_id=request.state.uzivatel.id,
        )
    except cteni_ai.ChybaCteni as chyba:
        sezeni.commit()  # ulozi utratu, veci se nezmenily
        oznam(request, f"Nepodařilo se přečíst — {chyba}", "chyba")
        return zpet(f"/akce/{akce.id}/obdarovani")

    for dar in [d for d in akce.dary if d.sken_id == sken.id]:
        akce.dary.remove(dar)
    sezeni.flush()
    pridano, oriznuto = _zaloz_dary(akce, sken, vysledek)
    sken.dary_precteny = True
    sezeni.commit()
    if oriznuto:
        _hlaska_o_oriznuti(request, sken)
    oznam(request, f"Přečteno znovu: {mnozne(pridano, 'věc', 'věci', 'věcí')}.")
    return zpet(f"/akce/{akce.id}/obdarovani")


@router.post("/akce/{akce_id}/obdarovani/rozhodit")
async def rozhodit(request: Request, akce_id: int, sezeni: Databaze):
    """Rozhodi veci ze zasobniku mezi lidi v seznamu. Viz `rozdeleni_daru`."""
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    zasobnik = [dar for dar in akce.dary if dar.obdarovany_id is None]
    if not zasobnik:
        oznam(request, "Zásobník je prázdný — není co rozhazovat.")
        return zpet(f"/akce/{akce_id}/obdarovani")
    if not akce.obdarovani:
        oznam(request, "Nejdřív přidej lidi, mezi které se to má rozdat.", "chyba")
        return zpet(f"/akce/{akce_id}/obdarovani")

    for vec, clovek in rozdeleni_daru.rozhod(zasobnik, list(akce.obdarovani)):
        clovek.dary.append(vec)
    sezeni.commit()

    oznam(
        request,
        f"Rozhozeno {mnozne(len(zasobnik), 'věc', 'věci', 'věcí')} "
        f"mezi {mnozne(len(akce.obdarovani), 'člověka', 'lidi', 'lidí')}. "
        "Teď si to popřetahuj, jak chceš.",
    )
    return zpet(f"/akce/{akce_id}/obdarovani")


@router.post("/dar/{dar_id}/priradit")
async def priradit(request: Request, dar_id: int, sezeni: Databaze):
    """Preveze vec k jinemu cloveku, nebo zpatky do zasobniku.

    Tohle vola pretahovani mysi. Prazdne pole "obdarovany" znamena zasobnik.
    """
    dar = sezeni.get(Dar, dar_id)
    if dar is None:
        if (chyba := prubezna_chyba(request, HLASKA_DAR_NENI)) is not None:
            return chyba
        return zpet("/")
    over_otevrenou(dar.akce)
    formular = await nacti_formular(request)
    komu = prazdne_na_none(text_z_formulare(formular, "obdarovany"))

    # Cokoli, co neni cislo, znamena zasobnik. Vec se tim nikdy neztrati.
    novy = None
    if komu and (cislo := id_z_textu(komu)) is not None:
        novy = sezeni.get(Obdarovany, cislo)
        # Clovek mezitim zmizel (smazala ho jina karta): vec by se tise vratila
        # do zasobniku, ale na obrazovce by zustala u nej.
        if novy is None and (chyba := prubezna_chyba(request, HLASKA_CLOVEK_NENI)) is not None:
            return chyba
    # Vec smi patrit jen nekomu z teze akce - jinak by zmizela ze zasobniku
    # a objevila se v cizim vyuctovani.
    if novy is not None and novy.akce_id != dar.akce_id:
        if (chyba := prubezna_chyba(request, "Ten člověk patří k jiné akci.")) is not None:
            return chyba
        oznam(request, "Ten člověk patří k jiné akci.", "chyba")
        return zpet(f"/akce/{dar.akce_id}/obdarovani")

    dar.obdarovany_id = novy.id if novy else None
    sezeni.commit()

    if (hotovo := prubezne_ulozeni(request)) is not None:
        return hotovo
    return zpet(f"/akce/{dar.akce_id}/obdarovani")


@router.post("/dar/{dar_id}/smazat")
async def smazat_dar(request: Request, dar_id: int, sezeni: Databaze):
    """Smaze vec uplne - ani u cloveka, ani v zasobniku uz nebude."""
    dar = sezeni.get(Dar, dar_id)
    if dar is None:
        if (chyba := prubezna_chyba(request, HLASKA_DAR_NENI)) is not None:
            return chyba
        return zpet("/")
    over_otevrenou(dar.akce)
    akce_id = dar.akce_id
    sezeni.delete(dar)
    sezeni.commit()

    if (hotovo := prubezne_ulozeni(request)) is not None:
        return hotovo
    return zpet(f"/akce/{akce_id}/obdarovani")


@router.post("/akce/{akce_id}/obdarovani/veci/smazat")
async def smazat_veci(request: Request, akce_id: int, sezeni: Databaze):
    """Zahodi vsechny veci vcetne rozdanych. Uctenky se pak daji precist znovu."""
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    pocet = len(akce.dary)
    for dar in list(akce.dary):
        akce.dary.remove(dar)
    sezeni.commit()
    oznam(request, f"Smazáno {mnozne(pocet, 'věc', 'věci', 'věcí')}.")
    return zpet(f"/akce/{akce_id}/obdarovani")


# --- mazani lidi ----------------------------------------------------------


@router.post("/obdarovany/{obdarovany_id}/smazat")
async def smazat(request: Request, obdarovany_id: int, sezeni: Databaze):
    """Odebere cloveka ze seznamu. Veci, ktere mel, se vrati do zasobniku."""
    obdarovany = sezeni.get(Obdarovany, obdarovany_id)
    if obdarovany is None:
        return await uz_smazano_kam(request)
    over_otevrenou(obdarovany.akce)
    akce_id = obdarovany.akce_id
    sezeni.delete(obdarovany)
    sezeni.commit()
    # Hlaska, ze radek opravdu zmizel. Bez jmena: viz `oznam`.
    oznam(request, "Obdarovaný odebrán ze seznamu. Jeho věci jsou zpátky v zásobníku.")
    return zpet(f"/akce/{akce_id}/obdarovani")


@router.post("/akce/{akce_id}/obdarovani/smazat")
async def smazat_vse(request: Request, akce_id: int, sezeni: Databaze):
    """Smaze seznam lidi. Veci zustanou a spadnou zpatky do zasobniku."""
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    pocet = len(akce.obdarovani)
    for radek in list(akce.obdarovani):
        sezeni.delete(radek)
    sezeni.commit()
    oznam(request, f"Seznam obdarovaných smazán ({pocet}). Věci jsou zpátky v zásobníku.")
    return zpet(f"/akce/{akce_id}/obdarovani")
