"""Sprava akci - seznam, zalozeni, prehled, uzavreni."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app import auth, cas
from app.chyby_poli import StavFormulare, stav_s_chybami
from app.config import nastaveni
from app.formaty import mnozne
from app.formulare import AkceForm, AkceUpravaForm, formular_s_chybami
from app.models import STAV_ROZPRACOVANA, STAV_UZAVRENA, Akce, Clovek
from app.routes.spolecne import Databaze, nacti_akci, nacti_otevrenou_akci
from app.sablony import oznam, strana, strana_s_chybou, zpet
from app.services import cestaky as vypocet_cestaku
from app.services import nahledy, pokladni_kniha, seznam_akci
from app.services import soubory as soubory_sluzba
from app.services.ciselniky import vsichni_lide

router = APIRouter()

# Nejvyssi limit mista akce v MB (viz `limit_mista`).
MAX_LIMIT_MB = 10_000_000

NAZEV_POVINNY = "Akce musí mít název."


def _strana_seznamu(
    request: Request, sezeni: Session, rok: str = "", stav: StavFormulare | None = None
) -> HTMLResponse:
    """Seznam akci; se `stav` je to znovu vykresleny formular zalozeni s chybou u pole."""
    dnes = cas.dnes()
    vybrany_rok = int(rok) if rok.isdecimal() and len(rok) <= 4 else None
    roky = seznam_akci.roky_starsich(sezeni, dnes)
    kontext = {
        "akce": seznam_akci.beznych(sezeni, dnes),
        "lide": vsichni_lide(sezeni),
        "starsi_roky": roky,
        "starsi_pocet": sum(pocet for _, pocet in roky),
        "vybrany_rok": vybrany_rok,
        "starsi_akce": (
            seznam_akci.starsi_v_roce(sezeni, dnes, vybrany_rok) if vybrany_rok else []
        ),
    }
    if stav is not None:
        return strana_s_chybou(request, "akce_seznam.html", stav, **kontext)
    return strana(request, "akce_seznam.html", **kontext)


@router.get("/", response_class=HTMLResponse)
async def seznam(request: Request, sezeni: Databaze, rok: str = ""):
    """Rozpracované akce a uzavřené z posledního roku; starší uzavřené jsou pod
    „Starší akce“ a vypíšou se až po výběru roku (`?rok=2020`) - viz seznam_akci."""
    return _strana_seznamu(request, sezeni, rok)


@router.post("/akce")
async def zalozit(
    request: Request,
    data: Annotated[AkceForm | StavFormulare, Depends(formular_s_chybami(AkceForm))],
    sezeni: Databaze,
):
    # Chyba formulare nebo chyba, kterou zjistime az tady: stranka znovu, s chybou u pole.
    if isinstance(data, StavFormulare):
        # Prazdny nazev model nehlida, tady je povinny.
        if not data.hodnota("nazev").strip():
            data.chyby.setdefault("nazev", NAZEV_POVINNY)
        return _strana_seznamu(request, sezeni, stav=data)
    if not data.nazev:
        return _strana_seznamu(request, sezeni, stav=stav_s_chybami(request, nazev=NAZEV_POVINNY))
    if data.pokladnik is not None and sezeni.get(Clovek, data.pokladnik) is None:
        return _strana_seznamu(
            request,
            sezeni,
            stav=stav_s_chybami(request, pokladnik="Vybraný pokladník v nabídce není."),
        )

    akce = Akce(
        nazev=data.nazev,
        datum_od=data.datum_od,
        datum_do=data.datum_do,
        datum_vyuctovani=data.datum_vyuctovani,
        pokladnik_id=data.pokladnik,
    )
    sezeni.add(akce)
    sezeni.commit()
    oznam(request, f"Akce „{akce.nazev}“ založena.")
    return zpet(f"/akce/{akce.id}")


def _strana_detailu(
    request: Request, sezeni: Session, akce: Akce, stav: StavFormulare | None = None
) -> HTMLResponse:
    """Detail akce; se `stav` je to znovu vykresleny formular „Nastavení akce“ s chybou u pole."""
    neprectene = [s for s in akce.skeny if s.jistota is None]
    nezkontrolovane = [s for s in akce.skeny if not s.zkontrolovano]
    nezarazene = [s for s in akce.skeny if s.slozka_id is None]

    kniha = pokladni_kniha.sestav(akce)
    _, _, zustatek = pokladni_kniha.souhrn(kniha)
    bez_data, mimo_slozku = pokladni_kniha.problemy(kniha)

    # Clovek v seznamu, kteremu nikdo nic nedal, je radek k nicemu - hospodar
    # se z nej nedozvi, co se rozdalo. Dlazdice na to upozorni.
    bez_veci = [o for o in akce.obdarovani if not o.dary]

    # Cestak, kteremu neco chybi (jizda, km, sazba, vozidlo...), je rozdelana prace -
    # nahrada by vysla nula nebo podhodnocena.
    neuplne_cestaky = [c for c in akce.cestaky if vypocet_cestaku.co_chybi(c)]

    kontext = {
        "akce": akce,
        "nahrady_cestaku": sum((vypocet_cestaku.celkem(c) for c in akce.cestaky), Decimal("0.00")),
        "pocet_neuplnych_cestaku": len(neuplne_cestaky),
        "pocet_neprectenych": len(neprectene),
        "pocet_nezkontrolovanych": len(nezkontrolovane),
        "pocet_nezarazenych": len(nezarazene),
        "pocet_radku_knihy": len(kniha),
        "konecny_zustatek": zustatek,
        "pocet_neuplnych_v_knize": bez_data + mimo_slozku,
        "pocet_bez_veci": len(bez_veci),
        "lide": vsichni_lide(sezeni),
        "misto_pouzito_mb": soubory_sluzba.velikost_souboru_akce(akce, nastaveni.slozka_uploadu)
        // (1024 * 1024),
        "misto_limit_mb": soubory_sluzba.limit_mista_akce_mb(akce),
    }
    if stav is not None:
        return strana_s_chybou(request, "akce_detail.html", stav, **kontext)
    return strana(request, "akce_detail.html", **kontext)


@router.get("/akce/{akce_id}", response_class=HTMLResponse)
async def detail(request: Request, akce_id: int, sezeni: Databaze):
    return _strana_detailu(request, sezeni, nacti_akci(sezeni, akce_id))


@router.post("/akce/{akce_id}/upravit")
async def upravit(
    request: Request,
    akce_id: int,
    data: Annotated[AkceUpravaForm | StavFormulare, Depends(formular_s_chybami(AkceUpravaForm))],
    sezeni: Databaze,
):
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    # Chyba formulare nebo chyba, kterou zjistime az tady: stranka znovu, s chybou u pole.
    if isinstance(data, StavFormulare):
        # Prazdny nazev model nehlida, tady je povinny (driv se tise ignoroval).
        if not data.hodnota("nazev").strip():
            data.chyby.setdefault("nazev", NAZEV_POVINNY)
        return _strana_detailu(request, sezeni, akce, data)
    if not data.nazev:
        stav = stav_s_chybami(request, nazev=NAZEV_POVINNY)
        return _strana_detailu(request, sezeni, akce, stav)
    if data.pokladnik is not None and sezeni.get(Clovek, data.pokladnik) is None:
        stav = stav_s_chybami(request, pokladnik="Vybraný pokladník v nabídce není.")
        return _strana_detailu(request, sezeni, akce, stav)

    akce.nazev = data.nazev
    akce.datum_od = data.datum_od
    akce.datum_do = data.datum_do
    akce.datum_vyuctovani = data.datum_vyuctovani
    akce.prefix_dokladu = data.prefix_dokladu
    akce.pokladnik_id = data.pokladnik
    sezeni.commit()
    oznam(request, "Uloženo.")
    return zpet(f"/akce/{akce_id}")


@router.post("/akce/{akce_id}/limit-mista", dependencies=[Depends(auth.vyzaduj_spravce)])
async def nastavit_limit_mista(
    request: Request,
    akce_id: int,
    sezeni: Databaze,
    limit_mb: str = Form(""),
):
    """Kolik MB souboru smi mit akce dohromady.

    Jen pro spravce, na rozdil od zbytku "Nastaveni akce" (`upravit` vyse),
    ktery smi kazdy editor - je to ochrana serveru pred zaplnenim disku,
    ne bezna vlastnost akce. Na uzavrene akci je zamceny jako vse ostatni
    (rozhodnuti Honzy): limit se meni az po „Znovu otevrit akci“.
    """
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    limit_mb = limit_mb.strip()
    if not limit_mb:
        akce.misto_mb_limit = None
    # `isascii()`: "²" projde `isdigit()`, ale `int()` spadne. Horni mez drzi cislo
    # v rozsahu SQLite (obri cislo by pretekilo) - 10 000 000 MB je vic nez kterykoli disk.
    elif (
        limit_mb.isascii()
        and limit_mb.isdigit()
        and len(limit_mb) <= 8
        and 0 < int(limit_mb) <= MAX_LIMIT_MB
    ):
        akce.misto_mb_limit = int(limit_mb)
    else:
        oznam(request, "Limit místa musí být kladné celé číslo, nebo prázdný.", "chyba")
        return zpet(f"/akce/{akce_id}")
    sezeni.commit()
    oznam(request, "Uloženo.")
    return zpet(f"/akce/{akce_id}")


@router.post("/akce/{akce_id}/stav")
async def zmenit_stav(request: Request, akce_id: int, sezeni: Databaze):
    akce = nacti_akci(sezeni, akce_id)
    if akce.stav == STAV_UZAVRENA:
        akce.stav = STAV_ROZPRACOVANA
        oznam(request, "Akce znovu otevřena.")
    else:
        # Co přečetla AI, je ostré hned (pokladní kniha, doklady, Excel). Aspoň
        # před odevzdáním hospodáři ať to člověk zkontroluje.
        zbyva = [s for s in akce.skeny if not s.zkontrolovano]
        if zbyva:
            oznam(
                request,
                f"Akci jsem neuzavřel. Nejdřív zkontroluj "
                f"{mnozne(len(zbyva), 'účtenku', 'účtenky', 'účtenek')} "
                f"(zaškrtni „OK“ v Účtenkách).",
                "chyba",
            )
            return zpet(f"/akce/{akce_id}")
        akce.stav = STAV_UZAVRENA
        oznam(request, "Akce uzavřena.")
    sezeni.commit()
    return zpet(f"/akce/{akce_id}")


@router.post("/akce/{akce_id}/smazat", dependencies=[Depends(auth.vyzaduj_spravce)])
async def smazat(
    request: Request,
    akce_id: int,
    sezeni: Databaze,
    potvrzeni: str = Form(""),
):
    """Smazani akce vcetne skenu a jejich souboru. Jen pro spravce - je to jedina nevratna
    operace v cele aplikaci. Vyzaduje i opsani nazvu, aby to neslo omylem.
    Uzavrena akce je jen ke cteni (rozhodnuti Honzy): smazat ji jde az po
    „Znovu otevrit akci“."""
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    if potvrzeni.strip() != akce.nazev:
        oznam(request, "Smazání jsem neprovedl — název nesouhlasí.", "chyba")
        return zpet(f"/akce/{akce_id}")

    nazev = akce.nazev
    # Soubory se mazou az po commitu (jako u jednoho skenu): kdyby commit selhal,
    # akce zustane a bez souboru by byla k nicemu. Akce jde celou obnovit ze zalohy
    #, proto obrazovka mazani pred potvrzenim nabizi jeji stazeni.
    nazvy_souboru = [sken.soubor for sken in akce.skeny]
    sezeni.delete(akce)
    sezeni.commit()
    soubory_sluzba.smaz_soubory(nazvy_souboru, nastaveni.slozka_uploadu)
    for nazev_souboru in nazvy_souboru:
        nahledy.zapomen(nazev_souboru)
    oznam(request, f"Akce „{nazev}“ smazána i se soubory.", "chyba")
    return zpet("/")
