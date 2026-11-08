"""Nahrani skenu, cteni pres AI a rucni kontrola udaju."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from sqlalchemy.orm import Session

from app.config import nastaveni
from app.db import zamkni_zapis
from app.formulare import NECITELNE, CastRozpisuForm, UctenkaRadekForm, mez_delky, over_delky
from app.models import Akce, CastSkenu, Sken
from app.routes.spolecne import (
    Databaze,
    hlaska_necitelnych,
    je_prubezne,
    kam_dal,
    na_datum,
    nacti_akci,
    nacti_formular,
    nacti_otevrenou_akci,
    over_otevrenou,
    prazdne_na_none,
    prubezne_ulozeni,
    radky_z_formulare,
    text_z_formulare,
    uz_smazano_kam,
    zaskrtnuto,
)
from app.sablony import oznam, strana, zpet
from app.services import cestaky, cteni_ai, kontrola_dat, nahledy, tarif, uctenky
from app.services import soubory as soubory_sluzba
from app.services.ciselniky import nabidka_pro_ai, vsechny_kategorie, vsichni_lide
from app.services.soubory import POVOLENE_PRIPONY

router = APIRouter()
log = logging.getLogger(__name__)

# Kolik skenu precist na jedno stisknuti tlacitka a kterym klicem, to urcuje
# vybrany tarif - viz `app/services/tarif.py`. Cte se vzdy po davkach a jen na
# vyzadani, nikdy samo od sebe na pozadi.


# Pole rozpisu: `cast_<id skenu>_<poradi>_<pole>`. Poradi je jen razeni radku
# ve formulari, proto mu staci rozumna mez.
CAST_ROZPISU = re.compile(r"cast_(\d+)_(\d+)_(kategorie|castka|popis)")
MAX_PORADI_CASTI = 10_000
# Popis casti rozpisu nema ID na konci jmena, proto vzor (viz `over_delky`).
POPIS_CASTI = re.compile(r"cast_\d+_\d+_popis")


def _uloz_rucni_rozdeleni(
    sken: Sken, formular, platne_kategorie: set[int], castecne: bool = False
) -> int:
    """Prepise rozpis uctenky tim, co je ve formulari. Vraci pocet poli, ktera
    se nepodarilo precist.

    Radky prijdou jako `cast_<sken>_<poradi>_kategorie` a `..._castka`. Radek
    bez castky se zahodi - tak se cast maze, aniz by bylo potreba zvlastni
    tlacitko. Zbyde-li po prepisu jedina cast, zahodi se taky: uctenka s jednou
    kategorii se nedeli.

    Nectitelna castka (nebo kategorie mimo nabidku) cast nemaze: u cast, ktera
    uz existuje, zustane stara hodnota; nova cast s nectitelnou castkou se
    preskoci.

    Prubezne ukladani posila jen zmenena pole, ale rozpis je jeden celek (cast se
    maze tim, ze se vymaze jeji castka) - prohlizec ho proto posila vcelku, kdyz
    se zmenila kterakoli jeho cast. Bez jedineho pole rozpisu (`castecne`) se
    na rozpis nesaha.
    """
    radky: dict[int, dict[str, str]] = {}
    for klic, hodnota in formular.items():
        shoda = CAST_ROZPISU.fullmatch(klic)
        if not shoda or int(shoda[1]) != sken.id or not isinstance(hodnota, str):
            continue
        poradi = int(shoda[2])
        if poradi <= MAX_PORADI_CASTI:
            radky.setdefault(poradi, {})[shoda[3]] = hodnota
    if castecne and not radky:
        return 0
    stare = {c.poradi: (c.kategorie_id, c.castka) for c in sken.casti}

    necitelnych = 0
    nove: list[uctenky.CastRozpisu] = []
    for poradi in sorted(p for p, radek in radky.items() if "castka" in radek):
        radek = radky[poradi]
        zadano = CastRozpisuForm.model_validate(radek)
        necitelnych += zadano.necitelnych()
        puvodni_kategorie, puvodni_castka = stare.get(poradi, (None, None))

        castka = zadano.castka
        if castka is NECITELNE:
            if puvodni_castka is None:
                continue
            castka = puvodni_castka
        elif castka is None:
            continue

        kategorie = zadano.kategorie
        if kategorie is NECITELNE or (kategorie is not None and kategorie not in platne_kategorie):
            kategorie = puvodni_kategorie
            if zadano.kategorie is not NECITELNE:
                necitelnych += 1
        nove.append((kategorie, castka, prazdne_na_none(radek.get("popis"))))

    uctenky.nastav_rozdeleni(sken, nove)
    return necitelnych


@router.get("/akce/{akce_id}/skeny", response_class=HTMLResponse)
async def prehled(request: Request, akce_id: int, sezeni: Databaze):
    akce = nacti_akci(sezeni, akce_id)
    skeny = sorted(akce.skeny, key=lambda s: (s.poradi, s.id))
    vybrany_tarif = tarif.aktualni(request.session)
    return strana(
        request,
        "skeny.html",
        akce=akce,
        skeny=skeny,
        kategorie=vsechny_kategorie(sezeni),
        lide=vsichni_lide(sezeni),
        # Ve fronte je jen to, co se ma cist; co se tri razy nepovedlo, ceka
        # na cloveka (rucni vyplneni, lepsi fotka, "Zkusit znovu"; K-047).
        neprectene=[s for s in skeny if s.jistota is None and cteni_ai.ma_se_cist(s)],
        nepovedene=[s for s in skeny if s.jistota is None and cteni_ai.nepodarilo_se_precist(s)],
        tarif=vybrany_tarif,
        davka=tarif.davka(vybrany_tarif),
        placeny_klic=tarif.prepinac_se_nabizi(),
        statistika=uctenky.statistika(skeny),
        # {id skenu: (stupen, popis)} - pocita se tady, aby sablona jen kreslila.
        varovani={s.id: kontrola_dat.zkontroluj(s.datum, akce) for s in skeny},
    )


@router.post("/akce/{akce_id}/skeny/tarif")
async def prepnout_tarif(request: Request, akce_id: int):
    """Prepne cteni mezi bezplatnym a placenym klicem ke Gemini.

    Vyber plati do odhlaseni a nic necte - jen si ho aplikace zapamatuje.
    Plati pro cele cteni pres AI, tedy i pro vytahovani veci z uctenek za
    vecne dary; podle pole "kam" se vraci na tu obrazovku, ze ktere se prepinalo.
    """
    formular = await nacti_formular(request)
    vybrany = tarif.nastav(request.session, text_z_formulare(formular, "tarif"))
    if vybrany == tarif.PLACENY:
        oznam(
            request,
            f"Čte se placeným klíčem — každá účtenka stojí peníze. "
            f"Dávka je teď {tarif.davka(vybrany)} účtenek.",
            "pozor",
        )
    else:
        oznam(request, "Čte se bezplatným klíčem.")
    return zpet(await kam_dal(request, f"/akce/{akce_id}/skeny"))


@router.post("/sken/{sken_id}/ztisit")
async def ztisit(request: Request, sken_id: int, sezeni: Databaze):
    """Prepne ztiseni varovani o datu mimo termin akce.

    Uctenka mimo termin se da normalne zauctovat, takze varovani musi jit
    utlumit - zustane videt, jen prestane kricet.
    """
    sken = sezeni.get(Sken, sken_id)
    if sken is None:
        return await uz_smazano_kam(request)
    over_otevrenou(sken.akce)
    sken.varovani_ztiseno = not sken.varovani_ztiseno
    sezeni.commit()
    return zpet(f"/akce/{sken.akce_id}/skeny")


@dataclass
class _Nahrani:
    """Co se pri jednom nahrani stalo se soubory."""

    pridano: int = 0
    odmitnuto: list[str] = field(default_factory=list)  # nepovolena pripona
    poskozeno: list[str] = field(default_factory=list)  # nepřijato z jineho duvodu
    zapsane: list[str] = field(default_factory=list)  # nazvy souboru zapsanych na disk


def _novy_sken(
    akce: Akce, puvodni_nazev: str, soubor: str, pocet_stranek: int, poradi: int
) -> Sken:
    return Sken(
        akce=akce,
        soubor=soubor,
        puvodni_nazev=puvodni_nazev,
        # Kmen jen navrhuje nazev uctenky (clovek ho stejne prepise), proto se orizne
        # na delku sloupce, kdyz je nazev souboru delsi.
        nazev=Path(puvodni_nazev).stem[: mez_delky(Sken.nazev)],
        pocet_stranek=pocet_stranek,
        poradi=poradi,
    )


async def _uloz_nahrane(sezeni: Session, akce: Akce, soubory: list[UploadFile]) -> _Nahrani:
    """Zapise soubory na disk a prida k akci radky `Sken` (bez commitu)."""
    # Kolik uz akce zabira a kolik smi zabirat celkem - spravce muze
    # limit prebit na strance akce, jinak plati vychozi hodnota z .env.
    limit_bajtu = soubory_sluzba.limit_mista_akce_mb(akce) * 1024 * 1024
    pouzito = soubory_sluzba.velikost_souboru_akce(akce, nastaveni.slozka_uploadu)
    misto_dosly = False
    vysledek = _Nahrani()
    nejvyssi = max((s.poradi for s in akce.skeny), default=-1)

    for nahrany in soubory:
        if not nahrany.filename:
            continue
        # Prilis dlouhy nazev se odmita, ne zkracuje: zkraceni by mohlo useknout
        # priponu a puvodni nazev je to, pod cim se soubor pozdeji stahuje.
        # V hlasce je nazev zkraceny - hlasky lezi v cookie.
        if len(nahrany.filename) > mez_delky(Sken.puvodni_nazev):
            vysledek.poskozeno.append(
                f"{soubory_sluzba.zkrat_pro_hlasku(nahrany.filename)} "
                f"(název souboru má víc než {mez_delky(Sken.puvodni_nazev)} znaků)"
            )
            continue
        pripona = Path(nahrany.filename).suffix.lower()
        if pripona not in POVOLENE_PRIPONY:
            vysledek.odmitnuto.append(nahrany.filename)
            continue
        if misto_dosly:
            vysledek.poskozeno.append(f"{nahrany.filename} (došlo místo na akci)")
            continue

        try:
            nazev_na_disku, pocet_stranek = await soubory_sluzba.uloz_nahrany(
                nahrany, nastaveni.slozka_uploadu, pripona
            )
        except soubory_sluzba.ChybaObsahu as chyba:
            vysledek.poskozeno.append(f"{nahrany.filename} ({chyba})")
            continue

        velikost = (nastaveni.slozka_uploadu / nazev_na_disku).stat().st_size
        if pouzito + velikost > limit_bajtu:
            (nastaveni.slozka_uploadu / nazev_na_disku).unlink(missing_ok=True)
            vysledek.poskozeno.append(f"{nahrany.filename} (došlo místo na akci)")
            misto_dosly = True
            continue
        pouzito += velikost
        vysledek.zapsane.append(nazev_na_disku)

        nejvyssi += 1
        sezeni.add(_novy_sken(akce, nahrany.filename, nazev_na_disku, pocet_stranek, nejvyssi))
        vysledek.pridano += 1
    return vysledek


def _hlasky_nahrani(request: Request, vysledek: _Nahrani) -> None:
    if vysledek.pridano:
        oznam(request, f"Nahráno {vysledek.pridano} souborů. Teď je nech přečíst.")
    if vysledek.odmitnuto:
        oznam(request, "Nepodporovaný formát: " + ", ".join(vysledek.odmitnuto), "chyba")
    if vysledek.poskozeno:
        oznam(request, "Nepřijato: " + ", ".join(vysledek.poskozeno), "chyba")


@router.post("/akce/{akce_id}/skeny/nahrat")
async def nahrat(
    request: Request,
    akce_id: int,
    sezeni: Databaze,
    soubory: list[UploadFile] = File(...),
):
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    nastaveni.zaloz_slozky()

    if len(soubory) > nastaveni.max_souboru_na_nahrani:
        oznam(
            request,
            f"Najednou jde nahrát nejvýš {nastaveni.max_souboru_na_nahrani} souborů.",
            "chyba",
        )
        return zpet(f"/akce/{akce_id}/skeny")

    vysledek = await _uloz_nahrane(sezeni, akce, soubory)
    try:
        sezeni.commit()
    except Exception:
        # Databaze se vrati sama (rollback) - soubory uz zapsane na disk se
        # musi vratit rucne, jinak by z nahrani, ktere "selhalo", zbyly
        # osirele uctenky s osobnimi udaji.
        sezeni.rollback()
        soubory_sluzba.smaz_soubory(vysledek.zapsane, nastaveni.slozka_uploadu)
        log.exception("Nahrání skenů selhalo při ukládání do databáze (akce %s)", akce_id)
        oznam(request, "Nahrání se nepovedlo kvůli neočekávané chybě. Nic se neuložilo.", "chyba")
        return zpet(f"/akce/{akce_id}/skeny")

    _hlasky_nahrani(request, vysledek)
    return zpet(f"/akce/{akce_id}/skeny")


def _precti_davku(
    request: Request, sezeni: Session, davka: list[Sken], vybrany_tarif: str
) -> tuple[int, list[str], list[str]]:
    """Precte skeny z davky jeden po druhem. Vraci (kolik se precetlo, chyby,
    nazvy uctenek oznacenych jako palivo). Po kazdem skenu commit - viz `precist`."""
    # AI vybira kategorii z Honzova ciselniku, ne z pevneho seznamu v kodu.
    nabidka = nabidka_pro_ai(sezeni)
    hotovo, chyby, palivove = 0, [], []
    for sken in davka:
        try:
            vysledek = cteni_ai.precti_uctenku(
                sezeni,
                sken,
                vybrany_tarif=vybrany_tarif,
                uzivatel_id=request.state.uzivatel.id,
                kategorie=nabidka,
            )
        except cteni_ai.ChybaCteni as chyba:
            chyby.append(f"{sken.puvodni_nazev}: {chyba}")
            sezeni.commit()  # utrata a pocitadlo pokusu se ulozi hned
            if chyba.zastavit_davku:
                break  # limit, klic nebo vypadek - dalsi sken by dopadl stejne
            continue

        # Prevzeti ceny za litr se promita do cestaku: "cti -> pocitej -> zapis"
        # pod zamkem. Az PO pomale AI, aby zamek nedrzel behem cteni.
        zamkni_zapis(sezeni)
        if uctenky.prevezmi_vysledek_ai(sezeni, sken, vysledek):
            palivove.append(sken.puvodni_nazev)
        hotovo += 1
        sezeni.commit()
    return hotovo, chyby, palivove


def _hlasky_po_cteni(
    request: Request, akce: Akce, hotovo: int, chyby: list[str], palivove: list[str]
) -> None:
    zbyva = len([s for s in akce.skeny if s.jistota is None and cteni_ai.ma_se_cist(s)])
    oznam(request, f"Přečteno {hotovo} skenů." + (f" Zbývá {zbyva}." if zbyva else ""))
    # Příloha se nikde nepočítá, takže tichá chyba AI by vyřadila výdaj z knihy.
    for nazev in palivove:
        oznam(
            request,
            f"Účtenka {nazev} vypadá jako palivová — označil jsem ji jako přílohu cesťáku, "
            f"takže se nepočítá do výdajů. Pokud to není pravda, odškrtni u ní „Příloha“.",
            "pozor",
        )
    for chyba in chyby:
        oznam(request, f"Nepodařilo se přečíst — {chyba}", "chyba")


@router.post("/akce/{akce_id}/skeny/precist")
def precist(request: Request, akce_id: int, sezeni: Databaze):
    """Precte dalsi davku neprectenych skenu. Postupne, ne paralelne.

    Obycejna `def`: cteni Gemini trva sekundy a v `async def` by na ten cas
    zastavilo cely server. Po kazde uctence se uklada - jinak by
    rozepsany zapis drzel zamek databaze po celou davku a ostatni by cekali,
    a pad v pulce davky by zahodil uz zaplacena cteni.
    """
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    # Nepovedene skeny jdou az za nove: jeden vadny soubor nesmi zabirat misto
    # v davce dalsim.
    neprectene = sorted(
        (s for s in akce.skeny if s.jistota is None and cteni_ai.ma_se_cist(s)),
        key=lambda s: (s.pokusy_cteni, s.poradi),
    )
    if not neprectene:
        if any(s.jistota is None for s in akce.skeny):
            oznam(
                request,
                "Zbylé účtenky se nepodařilo přečíst ani na třetí pokus. "
                "Vyplň je ručně, nebo u nich zvol „Zkusit znovu“.",
                "chyba",
            )
        else:
            oznam(request, "Není co číst — všechny skeny už jsou přečtené.")
        return zpet(f"/akce/{akce_id}/skeny")

    vybrany_tarif = tarif.aktualni(request.session)
    davka = neprectene[: tarif.davka(vybrany_tarif)]
    hotovo, chyby, palivove = _precti_davku(request, sezeni, davka, vybrany_tarif)
    _hlasky_po_cteni(request, akce, hotovo, chyby, palivove)
    return zpet(f"/akce/{akce_id}/skeny")


@router.post("/sken/{sken_id}/cteni/znovu")
async def zkusit_cteni_znovu(request: Request, sken_id: int, sezeni: Databaze):
    """Vynuluje pocitadlo neuspechu skenu, aby se do davky vratil.

    Nic necte a nic neplati - cist se bude az pri dalsim kliknuti na "Nacist
    udaje". Funguje z Uctenek i z Obdarovanych (pole `kam`).
    """
    sken = sezeni.get(Sken, sken_id)
    if sken is None:
        return await uz_smazano_kam(request)
    over_otevrenou(sken.akce)
    sken.pokusy_cteni = 0
    sezeni.commit()
    return zpet(await kam_dal(request, f"/akce/{sken.akce_id}/skeny"))


# Pole tabulky uctenek, kazde se ve formulari jmenuje `<pole>_<id skenu>`.
POLE_UCTENKY = (
    "nazev",
    "obchodnik",
    "datum",
    "castka",
    "litr",
    "kdo",
    "kategorie",
    "priloha",
    "priloha_k",
    "ok",
)


def _uloz_radek_uctenky(
    sken: Sken,
    pole: dict[str, str],
    formular,
    platni_lide: set[int],
    platne_kategorie: set[int],
    prubezne: bool,
) -> int:
    """Zapise do skenu pole jednoho radku formulare. Vraci pocet nectitelnych poli."""
    if "nazev" in pole:
        sken.nazev = prazdne_na_none(pole["nazev"])
    if "obchodnik" in pole:
        sken.obchodnik = prazdne_na_none(pole["obchodnik"])
    if "datum" in pole:
        sken.datum = na_datum(pole["datum"])

    zadano = UctenkaRadekForm.model_validate(pole)
    necitelnych = zadano.necitelnych()
    zmeny = zadano.zmeny()
    if "castka" in zmeny:
        sken.castka = zmeny["castka"]
    if "litr" in zmeny:
        sken.cena_za_litr = zmeny["litr"]
    # ID z nabidky: neexistujici zaznam (smazany jinou kartou, podvrzeny
    # formular) nechá starou hodnotu misto chyby cizího klice.
    if "kdo" in zmeny:
        if zmeny["kdo"] is None or zmeny["kdo"] in platni_lide:
            sken.clovek_id = zmeny["kdo"]
        else:
            necitelnych += 1
    if "kategorie" in zmeny:
        if zmeny["kategorie"] is None or zmeny["kategorie"] in platne_kategorie:
            sken.kategorie_id = zmeny["kategorie"]
        else:
            necitelnych += 1

    necitelnych += _uloz_rucni_rozdeleni(sken, formular, platne_kategorie, castecne=prubezne)
    sken.je_priloha = zaskrtnuto(pole, "priloha", sken.je_priloha, prubezne)
    sken.zkontrolovano = zaskrtnuto(pole, "ok", sken.zkontrolovano, prubezne)
    return necitelnych


def _prilozeno_k(sken: Sken, pole: dict[str, str], prubezne: bool) -> str | None:
    """K cemu ma byt sken prilozeny podle radku formulare (prazdny text = k niceho),
    nebo None, kdyz se vazba nema menit. Volat az po `_uloz_radek_uctenky`."""
    if "priloha_k" in pole:
        return pole["priloha_k"]
    if not prubezne:
        return ""
    if "priloha" in pole:
        # Odskrtnuti prilohy zrusi vazbu, zaskrtnuti nechava dosavadni.
        return str(sken.priloha_k_id or "")
    return None


@router.post("/akce/{akce_id}/skeny/ulozit")
async def ulozit(request: Request, akce_id: int, sezeni: Databaze):
    """Ulozi rucni opravy vsech radku najednou.

    Prazdna castka se smaze, nectitelna (a kazde cislo mimo meze) nechá starou
    hodnotu a zapocita se do hlasky - viz `app/formulare.py`.
    """
    formular = await nacti_formular(request)
    # Oprava ceny za litr se promita do cestaku: "cti -> pocitej -> zapis" pod zamkem.
    if any(klic.startswith("litr_") for klic in formular):
        zamkni_zapis(sezeni)
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    over_delky(
        formular,
        {"nazev": Sken.nazev, "obchodnik": Sken.obchodnik, POPIS_CASTI: CastSkenu.popis},
    )
    podle_id = {s.id: s for s in akce.skeny}
    puvodni_litr = {s.id: s.cena_za_litr for s in akce.skeny}
    platni_lide = {clovek.id for clovek in vsichni_lide(sezeni)}
    platne_kategorie = {kategorie.id for kategorie in vsechny_kategorie(sezeni)}
    parovani: dict[int, str] = {}
    zmeneno = 0
    necitelnych = 0
    # Prubezne ukladani posila jen zmenena pole: co neprislo, zustava.
    prubezne = je_prubezne(request)

    radky = radky_z_formulare(formular, POLE_UCTENKY)
    # Ucenka, u ktere se zmenila jen cast rozpisu, posle jen pole `cast_...`.
    for klic in formular:
        if shoda := CAST_ROZPISU.fullmatch(klic):
            radky.setdefault(int(shoda[1]), {})

    for sken_id, pole in radky.items():
        sken = podle_id.get(sken_id)
        # Cely formular: radek je na strance, kdyz ma nazev. Castecny: staci jakekoli pole.
        if sken is None or (not prubezne and "nazev" not in pole):
            continue

        necitelnych += _uloz_radek_uctenky(
            sken, pole, formular, platni_lide, platne_kategorie, prubezne
        )
        prilozeno = _prilozeno_k(sken, pole, prubezne)
        if prilozeno is not None:
            parovani[sken_id] = prilozeno
        zmeneno += 1

    presunuto = uctenky.sparuj_prilohy(podle_id, parovani)
    for sken in podle_id.values():
        cestaky.promit_cenu_za_litr_z_uctenky(sezeni, sken, puvodni_litr[sken.id])
    sezeni.commit()
    if (hotovo := prubezne_ulozeni(request, necitelnych)) is not None:
        return hotovo
    zprava = f"Uloženo {zmeneno} řádků."
    if presunuto:
        zprava += f" Příloh přesunutých do složky svého dokladu: {presunuto}."
    oznam(request, zprava)
    if necitelnych:
        oznam(request, hlaska_necitelnych(necitelnych), "chyba")
    return zpet(f"/akce/{akce_id}/skeny")


@router.post("/sken/{sken_id}/smazat")
async def smazat(request: Request, sken_id: int, sezeni: Databaze):
    sken = sezeni.get(Sken, sken_id)
    if sken is None:
        return await uz_smazano_kam(request)
    over_otevrenou(sken.akce)
    akce_id = sken.akce_id
    nazev_souboru = sken.soubor
    soubor = nastaveni.slozka_uploadu / nazev_souboru
    sezeni.delete(sken)
    sezeni.commit()
    soubor.unlink(missing_ok=True)
    nahledy.zapomen(nazev_souboru)
    oznam(request, "Sken smazán.")
    return zpet(await kam_dal(request, f"/akce/{akce_id}/skeny"))


@router.get("/sken/{sken_id}/soubor")
async def puvodni_soubor(sken_id: int, sezeni: Databaze):
    sken = sezeni.get(Sken, sken_id)
    if sken is None:
        return Response(status_code=404)
    cesta = nastaveni.slozka_uploadu / sken.soubor
    if not cesta.is_file():
        return Response(status_code=404)
    # Originál nese jméno, datum a částky z účtenky - nesmí zůstat v cache
    # prohlížeče (sdílený počítač) ani po odhlášení (B-039, rozhodnutí R20).
    return FileResponse(
        cesta, filename=sken.puvodni_nazev, headers={"Cache-Control": "private, no-store"}
    )


@router.get("/sken/{sken_id}/nahled")
def nahled(
    sken_id: int,
    sezeni: Databaze,
    strana_c: int = 1,
    velky: bool = False,
    v: str = "",
):
    """Nahled stranky jako JPEG. `velky=1` vraci velkou verzi pro lupu.

    Prohlizec neumi zobrazit ani PDF, ani HEIC z iPhonu, proto se prevadi na
    obrazek. Vysledek se uklada, jinak by se pri kazdem nacteni tabulky
    prepocitaval znovu.
    """
    sken = sezeni.get(Sken, sken_id)
    if sken is None:
        return Response(status_code=404)
    cesta = nastaveni.slozka_uploadu / sken.soubor
    if not cesta.is_file():
        return Response(status_code=404)
    # Cislo stranky z adresy nesmi byt libovolne - jinak by kazde dalsi cislo
    # vyrobilo dalsi vykresleni a dalsi soubor v cache.
    if not 1 <= strana_c <= sken.pocet_stranek:
        return Response(status_code=404)

    data = nahledy.dej_nahled(cesta, sken.pocet_stranek, strana_c, velky)
    if data is None:
        return Response(status_code=415)

    # Adresa se znamkou souboru (`?v=`) vraci porad tentyz obrazek, takze si ho
    # prohlizec smi nechat nadlouho. Adresa bez znamky se ukladat nesmi: id
    # smazaneho skenu dostane driv nebo pozdeji novy sken a v prohlizeci by pod
    # nim cely den svitila cizi uctenka. `private` (ne `public`): nahled nese
    # udaje z uctenky a sdilena mezivrstva ho drzet nesmi (B-039, rozhodnuti R20).
    trvanlivost = "private, max-age=3600" if v == sken.otisk else "no-store"
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": trvanlivost})
