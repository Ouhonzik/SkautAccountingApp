"""Cestovni prikazy jedne akce.

Driv se cestak delal v h.skauting.cz a do aplikace se nahraval hotovy jako
PDF. Tady se rovnou vytvari - hlavicka prikazu a pod ni rozpis jizd, ze
ktereho se pocita nahrada (app/services/cestaky.py).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic_core import PydanticCustomError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData

from app.chyby_poli import StavFormulare, stav_s_chybami
from app.config import nastaveni
from app.db import zamkni_zapis
from app.formulare import (
    CestakForm,
    CestakUlozitForm,
    JizdaForm,
    formular_s_chybami,
    over_delky,
    sloupce_jizdy,
)
from app.models import (
    NAZVY_PROSTREDKU,
    PROSTREDKY_NA_KM,
    Cestak,
    Jizda,
    Sken,
)
from app.routes.spolecne import (
    Databaze,
    hlaska_necitelnych,
    kam_dal,
    na_datum,
    nacti_akci,
    nacti_formular,
    nacti_otevrenou_akci,
    over_otevrenou,
    prazdne_na_none,
    prubezne_ulozeni,
    radky_z_formulare,
    uz_smazano,
)
from app.sablony import oznam, strana, strana_s_chybou, zpet
from app.services import cestaky, mapy, slovy
from app.services.ciselniky import dalsi_poradi, vsechna_vozidla, vsichni_lide

router = APIRouter()

# Pole hlavicky cestaku, ktera jsou proste text.
POLE_JIZDY = ("prostredek", "datum", "odkud", "kam", "hodnota")
HLASKA_PALIVOVKA_NEZARAZENA = (
    "Palivová účtenka zůstala nezařazená — její původní složka už není "
    "(nebo žádnou neměla). Zařaď ji, ať je na dokladu své složky."
)
TEXTOVA_POLE = ("ucel_cesty", "misto", "spolucestujici", "poznamka", "jednotka")


def _nacti_cestak(sezeni, cestak_id: int) -> Cestak:
    cestak = sezeni.get(Cestak, cestak_id)
    if cestak is None:
        raise HTTPException(status_code=404, detail="Takový cestovní příkaz neexistuje.")
    return cestak


def _nacti_otevreny_cestak(sezeni, cestak_id: int) -> Cestak:
    """`_nacti_cestak` pro zapisove routy: cestak uzavrene akce se nemeni."""
    cestak = _nacti_cestak(sezeni, cestak_id)
    over_otevrenou(cestak.akce)
    return cestak


def _nacti_cestak_k_zapisu(sezeni, cestak_id: int) -> Cestak:
    """Pro routy, ktere z jizd pocitaji castku v knize: nejdriv zamkne
    zapis, az pak cestak nacte - kdyz se zmeni zaroven dve okna, druha pocka
    na prvni a pocita uz z jejiho vysledku, ne ze stareho stavu."""
    zamkni_zapis(sezeni)
    return _nacti_otevreny_cestak(sezeni, cestak_id)


def _palivove_uctenky(akce) -> list[Sken]:
    """Uctenky akce oznacene jako priloha - z nich se vybira cena za litr.

    Palivova uctenka je v aplikaci vzdycky priloha (cte se tak uz z AI), takze
    se nabizeji prilohy, ne vsechny uctenky.
    """
    return [s for s in sorted(akce.skeny, key=lambda s: s.poradi) if s.je_priloha]


def _vypis(request: Request, sezeni, cestak: Cestak):
    return strana(
        request,
        "cestak_detail.html",
        akce=cestak.akce,
        cestak=cestak,
        radky=cestaky.ceny_jizd(cestak),
        # Na obrazovku i na papir jde sazba zaokrouhlena na halere (6,85),
        # ale pocita se z nezaokrouhlene (6,854) - viz app/services/cestaky.py.
        sazba=cestaky.na_zobrazeni(cestaky.sazba_cestaku(cestak)),
        cena_phm=cestaky.na_zobrazeni(cestaky.cena_phm_za_km(cestak.spotreba, cestak.cena_za_litr)),
        soucet=cestaky.soucet_radku(cestak),
        celkem=cestaky.celkem(cestak),
        pocet_km=cestaky.pocet_km(cestak),
        co_chybi=cestaky.co_chybi(cestak),
        lide=vsichni_lide(sezeni),
        vozidla=vsechna_vozidla(sezeni),
        uctenky=_palivove_uctenky(cestak.akce),
        prostredky=NAZVY_PROSTREDKU,
        prostredky_na_km=PROSTREDKY_NA_KM,
        jizd_na_stranku=cestaky.JIZD_NA_STRANKU,
        cislo_dokladu=cestaky.cislo_dokladu(cestak),
        mapa_zapnuta=mapy.je_zapnuta(),
        # Jizdy, ktere maji body z mapy - mapa je nakresli, at je videt, kudy
        # uz se jelo. Rucne psane jizdy souradnice nemaji a na mape nejsou.
        mapa_jizdy=[
            [j.z_lat, j.z_lon, j.do_lat, j.do_lon]
            for j in cestak.jizdy
            if None not in (j.z_lat, j.z_lon, j.do_lat, j.do_lon)
        ],
    )


# --- Seznam cestaku akce ----------------------------------------------------


def _strana_cestaku(request: Request, sezeni: Session, akce, stav: StavFormulare | None = None):
    """Seznam cestaku akce; se `stav` je to znovu vykresleny (rozbaleny) formular zalozeni
    s chybou u pole."""
    kontext = {
        "akce": akce,
        "castky": {c.id: cestaky.celkem(c) for c in akce.cestaky},
        "zacatky": {c.id: cestaky.datum_zacatku(c) for c in akce.cestaky},
        "lide": vsichni_lide(sezeni),
        "vozidla": vsechna_vozidla(sezeni),
        "uctenky": _palivove_uctenky(akce),
        "vychozi_opotrebeni": cestaky.posledni_sazba_opotrebeni(sezeni),
        "vychozi_jednotka": nastaveni.stredisko_jednotka,
    }
    if stav is not None:
        return strana_s_chybou(request, "cestaky.html", stav, **kontext)
    return strana(request, "cestaky.html", **kontext)


@router.get("/akce/{akce_id}/cestaky", response_class=HTMLResponse)
async def prehled(request: Request, akce_id: int, sezeni: Databaze):
    return _strana_cestaku(request, sezeni, nacti_akci(sezeni, akce_id))


@router.post("/akce/{akce_id}/cestaky/pridat")
def pridat(
    request: Request,
    akce_id: int,
    sezeni: Databaze,
    data: Annotated[CestakForm | StavFormulare, Depends(formular_s_chybami(CestakForm))],
):
    if isinstance(data, StavFormulare):
        # Nic se nezapisuje, zamek netreba; uzavrena akce ale chybovou stranku nedostane.
        return _strana_cestaku(request, sezeni, nacti_otevrenou_akci(sezeni, akce_id), data)
    zamkni_zapis(sezeni)  # poradi noveho cestaku se bere z uz ulozenych
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    if not data.ucel_cesty:
        stav = stav_s_chybami(request, ucel_cesty="Cestovní příkaz musí mít účel cesty.")
        return _strana_cestaku(request, sezeni, akce, stav)

    cestak = Cestak(
        ucel_cesty=data.ucel_cesty,
        misto=data.misto,
        spolucestujici=data.spolucestujici,
        poznamka=data.poznamka,
        jednotka=data.jednotka or nastaveni.stredisko_jednotka,
        opotrebeni=data.opotrebeni,
        poradi=dalsi_poradi(c.poradi for c in akce.cestaky),
    )
    cestaky.nastav_cestujiciho(sezeni, cestak, data.cestujici_id)
    cestaky.nastav_vozidlo(sezeni, cestak, data.vozidlo_id)
    cestaky.nastav_palivo(sezeni, cestak, akce.id, data.palivova_uctenka_id, data.cena_za_litr)

    # Pres akci, ne pres sezeni.add - kolekce uz je nactena a radek by se
    # do ni jinak dostal az po dalsim dotazu do databaze.
    akce.cestaky.append(cestak)
    sezeni.commit()
    oznam(request, "Cestovní příkaz založen. Teď do něj přidej jízdy.")
    return zpet(f"/cestak/{cestak.id}")


# --- Jeden cestak -----------------------------------------------------------


@router.get("/cestak/{cestak_id}", response_class=HTMLResponse)
async def detail(request: Request, cestak_id: int, sezeni: Databaze):
    return _vypis(request, sezeni, _nacti_cestak(sezeni, cestak_id))


@router.post("/cestak/{cestak_id}/ulozit")
def ulozit(
    request: Request,
    cestak_id: int,
    data: Annotated[CestakUlozitForm, Form()],
    sezeni: Databaze,
):
    """Hlavicka cestaku. Uklada se prubezne, proto se na chybejici pole nesaha."""
    cestak = _nacti_cestak_k_zapisu(sezeni, cestak_id)
    poslana = data.model_fields_set

    for pole in TEXTOVA_POLE:
        if pole in poslana:
            hodnota = getattr(data, pole) or None
            # Ucel cesty je jedine povinne pole - prazdny by zmizel z papiru
            # i z radku pokladni knihy.
            if pole == "ucel_cesty" and not hodnota:
                continue
            setattr(cestak, pole, hodnota)

    if "opotrebeni" in poslana:
        cestak.opotrebeni = data.opotrebeni
    if "cestujici_id" in poslana:
        cestaky.nastav_cestujiciho(sezeni, cestak, data.cestujici_id)
    if "vozidlo_id" in poslana:
        cestaky.nastav_vozidlo(sezeni, cestak, data.vozidlo_id)
    if "palivova_uctenka_id" in poslana:
        # Prohlizec po zmene vyberu posila jen toto pole. Kdyz cena za litr
        # nepriszla, zustava ta ulozena - jinak by se rucni cena smazala.
        rucni_cena = data.cena_za_litr if "cena_za_litr" in poslana else cestak.cena_za_litr
        cestaky.nastav_palivo(sezeni, cestak, cestak.akce_id, data.palivova_uctenka_id, rucni_cena)
    elif "cena_za_litr" in poslana:
        cestak.cena_za_litr = data.cena_za_litr

    cestaky.sesynchronizuj(cestak)
    sezeni.commit()
    if (hotovo := prubezne_ulozeni(request)) is not None:
        return hotovo
    oznam(request, "Uloženo.")
    return zpet(f"/cestak/{cestak_id}")


@router.post("/cestak/{cestak_id}/do-vydaju")
def do_vydaju(request: Request, cestak_id: int, sezeni: Databaze):
    """Zapise cestak do pokladni knihy jako vydajovy radek.

    Palivova uctenka, ze ktere se vzala cena za litr, se tim zaroven stava
    prilohou dokladu - vypise se na tiskopisu VPD (pokladni_kniha._prilohy_rucniho).
    """
    cestak = _nacti_cestak_k_zapisu(sezeni, cestak_id)
    if cestak.pokladni_radek is not None:
        oznam(request, "Tenhle cesťák už ve výdajích je.", "pozor")
        return zpet(f"/cestak/{cestak_id}")
    if chybi := cestaky.co_chybi(cestak):
        oznam(request, "Cesťák zatím nejde přidat do výdajů. " + " ".join(chybi), "chyba")
        return zpet(f"/cestak/{cestak_id}")

    cestaky.zapis_do_vydaju(cestak)
    sezeni.commit()
    oznam(request, "Cesťák je ve výdajích — v pokladní knize dostal číslo dokladu.")
    return zpet(f"/cestak/{cestak_id}")


@router.post("/cestak/{cestak_id}/z-vydaju")
def z_vydaju(request: Request, cestak_id: int, sezeni: Databaze):
    cestak = _nacti_cestak_k_zapisu(sezeni, cestak_id)
    if cestak.pokladni_radek is None:
        return zpet(f"/cestak/{cestak_id}")
    zustala_nezarazena = cestaky.odeber_z_vydaju(cestak)
    sezeni.commit()
    oznam(request, "Cesťák je zase rozpracovaný — z pokladní knihy zmizel.")
    if zustala_nezarazena:
        oznam(request, HLASKA_PALIVOVKA_NEZARAZENA, "pozor")
    return zpet(f"/cestak/{cestak_id}")


@router.get("/cestak/{cestak_id}/tisk", response_class=HTMLResponse)
async def tisk(request: Request, cestak_id: int, sezeni: Databaze):
    """Cestovni prikaz na A4 podle formulare z h.skauting.cz.

    Stejnou technikou jako tiskopisy PPD/VPD - obycejne HTML a `@media print`
    v app/static/tisk.css, tisk resi prohlizec (Ctrl+P). Zadny generator PDF.
    """
    cestak = _nacti_cestak(sezeni, cestak_id)
    celkem = cestaky.celkem(cestak)
    return strana(
        request,
        "cestak_tisk.html",
        akce=cestak.akce,
        cestak=cestak,
        radky=cestaky.ceny_jizd(cestak),
        sazba=cestaky.na_zobrazeni(cestaky.sazba_cestaku(cestak)),
        cena_phm=cestaky.na_zobrazeni(cestaky.cena_phm_za_km(cestak.spotreba, cestak.cena_za_litr)),
        celkem=celkem,
        slovy=slovy.korunami(celkem),
        pocet_km=cestaky.pocet_km(cestak),
        zacatek_misto=cestaky.misto_zacatku(cestak),
        zacatek_datum=cestaky.datum_zacatku(cestak),
        konec_misto=cestaky.misto_konce(cestak),
        konec_datum=cestaky.datum_konce(cestak),
        prostredek=cestaky.popis_prostredku(cestak),
        pocet_priloh=1 if cestak.palivova_uctenka_id else 0,
        # Dokud cestak neni ve vydajich, zadne cislo dokladu nema a kolonka
        # na papire zustava prazdna.
        cislo_dokladu=cestaky.cislo_dokladu(cestak),
        stredisko=nastaveni.stredisko_nazev,
        sidlo=nastaveni.stredisko_sidlo,
        ico=nastaveni.stredisko_ico,
        dic=nastaveni.stredisko_dic,
    )


@router.post("/cestak/{cestak_id}/smazat")
async def smazat(request: Request, cestak_id: int, sezeni: Databaze):
    # Zamek zapisu se bere ve vlakne: cekani na nej by v `async` route zastavilo smycku.
    akce_id = await run_in_threadpool(_smazat, request, cestak_id, sezeni)
    return zpet(await kam_dal(request, f"/akce/{akce_id}/cestaky"))


def _smazat(request: Request, cestak_id: int, sezeni: Session) -> int:
    cestak = _nacti_cestak_k_zapisu(sezeni, cestak_id)
    akce_id = cestak.akce_id
    zustala_nezarazena = False
    if cestak.pokladni_radek is not None:
        # Palivova uctenka se vraci do sve puvodni slozky jako pri odebrani z vydaju.
        zustala_nezarazena = cestaky.odeber_z_vydaju(cestak)
    sezeni.delete(cestak)
    sezeni.commit()
    oznam(request, "Cestovní příkaz smazán.")
    if zustala_nezarazena:
        oznam(request, HLASKA_PALIVOVKA_NEZARAZENA, "pozor")
    return akce_id


# --- Jizdy ------------------------------------------------------------------


@router.post("/cestak/{cestak_id}/jizdy/pridat")
def pridat_jizdu(
    cestak_id: int,
    data: Annotated[JizdaForm, Form()],
    sezeni: Databaze,
):
    cestak = _nacti_cestak_k_zapisu(sezeni, cestak_id)
    cestak.jizdy.append(
        Jizda(
            datum=data.datum,
            odkud=data.odkud,
            kam=data.kam,
            prostredek=data.prostredek,
            poradi=dalsi_poradi(j.poradi for j in cestak.jizdy),
            **data.sloupce(),
            **data.souradnice(),
        )
    )
    cestaky.sesynchronizuj(cestak)
    sezeni.commit()
    return zpet(f"/cestak/{cestak_id}")


@router.post("/cestak/{cestak_id}/jizdy/ulozit")
async def ulozit_jizdy(request: Request, cestak_id: int, sezeni: Databaze):
    """Tabulka jizd. Prazdna hodnota (km, jizdne) se smaze, nectitelna nebo
    mimo meze nechá starou a zapocita se do hlasky."""
    formular = await nacti_formular(request)
    # Zamek zapisu se bere ve vlakne: cekani na nej by v `async` route zastavilo smycku.
    return await run_in_threadpool(_ulozit_jizdy, request, cestak_id, sezeni, formular)


def _ulozit_jizdy(request: Request, cestak_id: int, sezeni: Session, formular: FormData):
    cestak = _nacti_cestak_k_zapisu(sezeni, cestak_id)
    over_delky(formular, {"odkud": Jizda.odkud, "kam": Jizda.kam})
    radky = radky_z_formulare(formular, POLE_JIZDY)
    necitelnych = 0

    for jizda in cestak.jizdy:
        pole = radky.get(jizda.id, {})
        if "prostredek" in pole and pole["prostredek"] in NAZVY_PROSTREDKU:
            cestaky.prepni_prostredek(jizda, pole["prostredek"])
        if "datum" in pole:
            jizda.datum = na_datum(pole["datum"])
        if "odkud" in pole:
            jizda.odkud = prazdne_na_none(pole["odkud"])
        if "kam" in pole:
            jizda.kam = prazdne_na_none(pole["kam"])
        if "hodnota" in pole:
            # Jeden sloupec, dva vyznamy - u auta kilometry, u vlaku cena
            # jizdenky. Na papire je to taky jedna kolonka.
            try:
                sloupce = sloupce_jizdy(jizda.prostredek, pole["hodnota"])
            except PydanticCustomError:
                necitelnych += 1
            else:
                for nazev, hodnota in sloupce.items():
                    setattr(jizda, nazev, hodnota)
            cestaky.vycisti_nepouzitou(jizda)

    cestaky.sesynchronizuj(cestak)
    sezeni.commit()
    if (hotovo := prubezne_ulozeni(request, necitelnych)) is not None:
        return hotovo
    oznam(request, "Uloženo.")
    if necitelnych:
        oznam(request, hlaska_necitelnych(necitelnych), "chyba")
    return zpet(f"/cestak/{cestak_id}")


@router.post("/cestak/{cestak_id}/jizdy/presunout")
def presunout_jizdu(
    request: Request,
    cestak_id: int,
    sezeni: Databaze,
    presun: str = Form(...),
):
    """Sipky u radku. Tlacitko umi poslat jen jednu hodnotu, proto "id:smer"."""
    cestak = _nacti_cestak_k_zapisu(sezeni, cestak_id)
    cislo, _, smer = presun.partition(":")
    # isascii: isdigit() pusti i cislice jako "²", ktere int() odmitne.
    if cislo.isascii() and cislo.isdigit():
        cestaky.presun(cestak, int(cislo), dolu=(smer == "dolu"))
        # Presun muze zmenit posledni jizdu, a s ni datum dokladu.
        cestaky.sesynchronizuj(cestak)
        sezeni.commit()
    return zpet(f"/cestak/{cestak_id}")


@router.post("/jizda/{jizda_id}/smazat")
def smazat_jizdu(request: Request, jizda_id: int, sezeni: Databaze):
    zamkni_zapis(sezeni)
    jizda = sezeni.get(Jizda, jizda_id)
    if jizda is None:
        return uz_smazano(request)
    cestak_id = jizda.cestak_id
    cestak = jizda.cestak
    over_otevrenou(cestak.akce)
    cestak.jizdy.remove(jizda)
    cestaky.sesynchronizuj(cestak)
    sezeni.commit()
    oznam(request, "Jízda smazána.")
    return zpet(f"/cestak/{cestak_id}")


# --- Pomocnici --------------------------------------------------------------
