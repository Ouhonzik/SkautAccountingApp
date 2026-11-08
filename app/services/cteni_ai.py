"""Cteni skenu pres AI: jedno misto pro pravidla, ktera plati pro uctenky i dary.

Obe obrazovky (Uctenky a Obdarovani) ctou stejne - vyberou klic podle tarifu,
zavolaji Gemini, zapisou utratu. Pravidla jsou tady, aby se zadne z nich
neopravilo jen na jednom miste:

- **Kazde cteni, za ktere se platilo, se zapise do `PouzitiAI`**, i kdyz
  Gemini vratila prazdnou nebo vadnou odpoved (SPEC §19, K-012).
- Chyba Gemini se prelozi do ceske hlasky pro uzivatele (`ChybaCteni`);
  detail jde do zurnalu serveru, ale jen s ID skenu.
- Chyba, po ktere nema smysl cist dalsi uctenky z davky (vycerpany limit,
  neplatny klic, vypadek), je oznacena `zastavit_davku`.

Sluzba nic nekomituje - `PouzitiAI` jen prida do session a commit je v route,
stejne jako u ostatnich sluzeb.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from dataclasses import dataclass
from decimal import Decimal

import httpx
from google.genai import errors as chyby_gemini
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cas import PRAHA, ted
from app.config import nastaveni
from app.models import PouzitiAI, Sken, Uzivatel
from app.services import ocr, tarif

log = logging.getLogger(__name__)

UCEL_UCTENKA = "uctenka"
UCEL_DARY = "dary"

# Kolikrat se smi stejny sken zkusit precist, nez se z davky vynecha.
# Pocitaji se jen neuspechy, ktere jdou za skenem (vadna odpoved, poskozeny
# soubor) - vypadek, limit nebo klic (`zastavit_davku`) za nic neste.
MAX_POKUSU_CTENI = 3

# Gemini se cte postupne, nikdy soubezne: jeden pozadavek po druhem
# nezasahne limity bezplatneho klice a utrata placeneho se nenarusti nahodou.
# Routy na cteni jsou obycejne `def`, tedy bezi ve vlastnich vlaknech
# a samy by se klidne prekryly - tenhle zamek jim to nedovoli. Ceka se jen
# na samotne volani Gemini (nejvyse timeout z ocr.py), ne na zbytek routy.
_zamek_cteni = threading.Lock()

# Zkratky mene, ktere pro nas znamenaji koruny. Prazdne pole bereme jako koruny:
# prompt rika "vsechny castky jsou v Kc" a model pole vetsinou nevyplni.
_KORUNY = {"", "CZK", "KC", "KČ"}


def ma_se_cist(sken: Sken) -> bool:
    """Smi sken do davky? Ne, kdyz se uz `MAX_POKUSU_CTENI`krat nepovedl."""
    return sken.pokusy_cteni < MAX_POKUSU_CTENI


def nepodarilo_se_precist(sken: Sken) -> bool:
    """Sken, ktery se vzdal po vsech pokusech a ceka na lidske rozhodnuti."""
    return not ma_se_cist(sken)


# Meze posuvniku limitu na /uzivatele: (nejmene, nejvice, krok). Vychozi hodnoty
# (300 cteni, 100 Kc za den, 500 Kc za rok) jsou v app/models/ucty.py:Uzivatel.
MEZE_LIMITU_AI = {
    "limit_cteni_den": (0, 1000, 10),
    "limit_kc_den": (0, 500, 10),
    "limit_kc_rok": (0, 5000, 50),
}


@dataclass(frozen=True)
class Vyuziti:
    """Co uzivatel dnes a letos precetl pres AI (z `PouzitiAI`)."""

    dnes_pocet: int
    dnes_kc: Decimal
    rok_kc: Decimal


def _na_naivni_utc(cas: dt.datetime) -> dt.datetime:
    return cas.astimezone(dt.UTC).replace(tzinfo=None)


def vyuziti(sezeni: Session, uzivatel_id: int, *, v_case: dt.datetime | None = None) -> Vyuziti:
    """Kolik uzivatel dnes a letos precetl pres AI. Den a rok zacinaji o pulnoci v Praze
    (v databazi je naivni UTC, proto se hranice prevadi). Do Kc se pocita jen
    placene cteni; cteni modelu, ktery cenik nezna, se do Kc nezapocita, jen do
    poctu (test hlida, ze vychozi model cenu ma)."""
    return vyuziti_vice(sezeni, [uzivatel_id], v_case=v_case)[uzivatel_id]


def vyuziti_vice(
    sezeni: Session, uzivatel_ids: list[int], *, v_case: dt.datetime | None = None
) -> dict[int, Vyuziti]:
    """`vyuziti` pro vic lidi najednou - dva dotazy bez ohledu na jejich pocet
    (stranka Uzivatele se jinak ptala dvakrat na kazdy ucet)."""
    if not uzivatel_ids:
        return {}
    okamzik = v_case if v_case is not None else ted()
    mistni = okamzik.replace(tzinfo=dt.UTC).astimezone(PRAHA)
    zacatek_dne_mistni = mistni.replace(hour=0, minute=0, second=0, microsecond=0)
    zacatek_dne = _na_naivni_utc(zacatek_dne_mistni)
    zacatek_roku = _na_naivni_utc(zacatek_dne_mistni.replace(month=1, day=1))

    dnes_pocty = {
        uzivatel_id: pocet
        for uzivatel_id, pocet in sezeni.execute(
            select(PouzitiAI.uzivatel_id, func.count(PouzitiAI.id))
            .where(PouzitiAI.uzivatel_id.in_(uzivatel_ids), PouzitiAI.kdy >= zacatek_dne)
            .group_by(PouzitiAI.uzivatel_id)
        )
    }
    dnes_kc = dict.fromkeys(uzivatel_ids, Decimal(0))
    rok_kc = dict.fromkeys(uzivatel_ids, Decimal(0))
    # Jen sloupce k cene; cena kazdeho cteni se zaokrouhluje zvlast (viz tarif._souhrn_pouziti).
    placena = select(
        PouzitiAI.uzivatel_id,
        PouzitiAI.kdy,
        PouzitiAI.tarif,
        PouzitiAI.model,
        PouzitiAI.tokeny_vstup,
        PouzitiAI.tokeny_vystup,
        PouzitiAI.tokeny_mysleni,
    ).where(
        PouzitiAI.uzivatel_id.in_(uzivatel_ids),
        PouzitiAI.tarif == tarif.PLACENY,
        PouzitiAI.kdy >= zacatek_roku,
    )
    for pouziti in sezeni.execute(placena):
        cena = tarif.odhad_czk(pouziti)
        if cena is None:
            continue
        rok_kc[pouziti.uzivatel_id] += cena
        if pouziti.kdy >= zacatek_dne:
            dnes_kc[pouziti.uzivatel_id] += cena
    return {
        uzivatel_id: Vyuziti(
            dnes_pocet=dnes_pocty.get(uzivatel_id, 0),
            dnes_kc=dnes_kc[uzivatel_id],
            rok_kc=rok_kc[uzivatel_id],
        )
        for uzivatel_id in uzivatel_ids
    }


class ChybaCteni(Exception):
    """Cteni se nepovedlo. `str(chyba)` je cesky text, ktery se smi ukazat uzivateli."""

    def __init__(self, zprava: str, *, zastavit_davku: bool = False):
        super().__init__(zprava)
        self.zastavit_davku = zastavit_davku


def _zapis_utratu(
    sezeni: Session,
    pouziti: ocr.PouzitiTokenu,
    *,
    sken: Sken,
    vybrany_tarif: str,
    uzivatel_id: int,
    ucel: str,
) -> None:
    sezeni.add(
        PouzitiAI(
            uzivatel_id=uzivatel_id,
            akce_id=sken.akce_id,
            sken_id=sken.id,
            tarif=vybrany_tarif,
            model=pouziti.model,
            tokeny_vstup=pouziti.tokeny_vstup,
            tokeny_vystup=pouziti.tokeny_vystup,
            tokeny_mysleni=pouziti.tokeny_mysleni,
            ucel=ucel,
        )
    )


def _prelozit(chyba: Exception, sken: Sken) -> ChybaCteni:
    """Zmeni vyjimku z Gemini na ceskou hlasku a zapise ji do zurnalu (bez
    nazvu souboru a obsahu - jen ID skenu)."""
    log.warning("Čtení AI selhalo u skenu %s", sken.id, exc_info=chyba)

    if isinstance(chyba, chyby_gemini.ClientError):
        if chyba.code == 429:
            return ChybaCteni(
                "Došel limit čtení u Gemini. Zkus to za pár minut, "
                "u bezplatného klíče případně zítra.",
                zastavit_davku=True,
            )
        if chyba.code in (401, 403):
            return ChybaCteni("Klíč ke Gemini neplatí — dej vědět správci.", zastavit_davku=True)
    if isinstance(chyba, chyby_gemini.ServerError):
        return ChybaCteni("Gemini je teď přetížené, zkus to za chvíli.", zastavit_davku=True)
    if isinstance(chyba, httpx.TimeoutException):
        return ChybaCteni(
            "Gemini neodpověděla včas, zkus to za chvíli.",
            zastavit_davku=True,
        )
    if isinstance(chyba, httpx.TransportError):
        return ChybaCteni("Nepodařilo se spojit s Gemini, zkus to za chvíli.", zastavit_davku=True)
    # Neznama chyba (poskozeny soubor, chyba v kodu...): nechame aspon nazev
    # vyjimky, at je neco, podle ceho se da hledat v zurnalu.
    return ChybaCteni(type(chyba).__name__)


def _over_limity(sezeni: Session, uzivatel_id: int, vybrany_tarif: str) -> None:
    """Zastavi cteni, kdyz uzivatel vycerpal svuj limit. Bezi PRED volanim
    Gemini a nic nezapisuje. Pocita i s cteni z prave bezici davky: dotaz na
    `PouzitiAI` nejdriv zapise rozpracovane radky (autoflush).

    Limit v Kc se uplatnuje jen u placeneho cteni - bezplatne nestoji nic."""
    ucet = sezeni.get(Uzivatel, uzivatel_id)
    if ucet is None:
        return
    spotreba = vyuziti(sezeni, uzivatel_id)
    kdo_rozhodne = "Zkus to zítra, nebo se ozvi správci."

    if ucet.limit_cteni_den <= 0:
        raise ChybaCteni("Čtení AI máš vypnuté, ozvi se správci.", zastavit_davku=True)
    if spotreba.dnes_pocet >= ucet.limit_cteni_den:
        raise ChybaCteni(
            f"Dnešní limit čtení AI ({ucet.limit_cteni_den}) je vyčerpaný. {kdo_rozhodne}",
            zastavit_davku=True,
        )
    if vybrany_tarif != tarif.PLACENY:
        return
    if spotreba.dnes_kc >= ucet.limit_kc_den:
        raise ChybaCteni(
            f"Dnešní limit útraty za čtení AI ({ucet.limit_kc_den} Kč) je vyčerpaný. "
            f"{kdo_rozhodne}",
            zastavit_davku=True,
        )
    if spotreba.rok_kc >= ucet.limit_kc_rok:
        raise ChybaCteni(
            f"Roční limit útraty za čtení AI ({ucet.limit_kc_rok} Kč) je vyčerpaný. "
            "Ozvi se správci.",
            zastavit_davku=True,
        )


def _precti(sezeni: Session, cteni, *, sken: Sken, vybrany_tarif: str, uzivatel_id: int, ucel: str):
    """Spolecny postup: zkontroluje klic, zavola `cteni(cesta, api_klic=...)`,
    zapise utratu a pri chybe ji preda jako `ChybaCteni`."""
    if not tarif.cteni_je_povolene():
        raise ChybaCteni(
            "Čtení AI na serveru není zapnuté — chybí placený klíč ke Gemini. Dej vědět správci.",
            zastavit_davku=True,
        )

    _over_limity(sezeni, uzivatel_id, vybrany_tarif)

    cesta = nastaveni.slozka_uploadu / sken.soubor
    try:
        with _zamek_cteni:
            vysledek, pouziti = cteni(cesta, api_klic=tarif.api_klic(vybrany_tarif))
    except ocr.ChybaOdpovediAI as chyba:
        # Gemini odpovedela, ale k nicemu - tokeny se presto zaplatily.
        _zapis_utratu(
            sezeni,
            chyba.pouziti,
            sken=sken,
            vybrany_tarif=vybrany_tarif,
            uzivatel_id=uzivatel_id,
            ucel=ucel,
        )
        log.warning("Čtení AI skončilo vadnou odpovědí u skenu %s: %s", sken.id, chyba)
        sken.pokusy_cteni += 1
        raise ChybaCteni(str(chyba)) from chyba
    except Exception as chyba:  # kazda chyba se prelozi, jeden sken nesmi shodit davku
        prelozena = _prelozit(chyba, sken)
        if not prelozena.zastavit_davku:
            sken.pokusy_cteni += 1
        raise prelozena from chyba

    _zapis_utratu(
        sezeni,
        pouziti,
        sken=sken,
        vybrany_tarif=vybrany_tarif,
        uzivatel_id=uzivatel_id,
        ucel=ucel,
    )
    sken.pokusy_cteni = 0
    return vysledek


def precti_uctenku(
    sezeni: Session,
    sken: Sken,
    *,
    vybrany_tarif: str,
    uzivatel_id: int,
    kategorie: list[tuple[str, str]],
) -> ocr.VysledekCteni:
    """Precte beznou stranku uctenky. Pri jine mene nez koruny pripise
    upozorneni do poznamky a snizi jistotu."""
    vysledek = _precti(
        sezeni,
        lambda cesta, api_klic: ocr.precti_sken(cesta, api_klic=api_klic, kategorie=kategorie),
        sken=sken,
        vybrany_tarif=vybrany_tarif,
        uzivatel_id=uzivatel_id,
        ucel=UCEL_UCTENKA,
    )
    return _oznac_cizi_menu(vysledek)


def precti_dary(
    sezeni: Session, sken: Sken, *, vybrany_tarif: str, uzivatel_id: int
) -> ocr.VysledekDaru:
    """Vytahne z uctenky veci, ktere se maji rozdat."""
    return _precti(
        sezeni,
        lambda cesta, api_klic: ocr.precti_dary(cesta, api_klic=api_klic),
        sken=sken,
        vybrany_tarif=vybrany_tarif,
        uzivatel_id=uzivatel_id,
        ucel=UCEL_DARY,
    )


def _oznac_cizi_menu(vysledek: ocr.VysledekCteni) -> ocr.VysledekCteni:
    """Appka pocita jen v korunach. Uctenku v EUR by jinak ulozila jako koruny
    bez varovani, proto ji oznaci k rucni kontrole. Prevod mene se zatim
    nedela."""
    mena = (vysledek.mena or "").strip().upper()
    if mena in _KORUNY:
        return vysledek
    upozorneni = f"Účtenka je v měně {mena} — částka není v Kč, zkontroluj ji."
    poznamka = f"{upozorneni} {vysledek.poznamka}" if vysledek.poznamka else upozorneni
    return vysledek.model_copy(update={"jistota": "nizka", "poznamka": poznamka})
