"""Kontrola formularu: modely Pydantic s typy, mezemi a ceskymi hlaskami.

V route staci `data: Annotated[AkceForm, Form()]` - FastAPI formular samo
prevede a zkontroluje. Chyba skonci v obsluze `chyba_formulare` (app/main.py):
hlaska nahore a navrat na stranku, odkud clovek prisel; do databaze se nic
nezapise. Pravidla jsou tady jednou pro vsechny formulare:

- castka 0 az 10 000 000 Kc; zaporna, NaN, Infinity a exponent (1e30) se odmitne,
- rok data 2000 az 2100,
- text nejvyse tak dlouhy, jak je `String(n)` v app/models/ (SQLite delku
  nehlida); delsi se NEORIZNE, ale odmitne - u penez by to menilo obsah dokladu,
- ID z nabidky je cele cislo nebo nic.

Prevodniky ze `routes/spolecne.py` zustavaji, tady slouzi jako pomocnici.
Hlasky vlastnich chyb maji typ "formular" - podle nej je obsluha pozna od
obecnych chyb Pydantic a ukaze jen ty.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainValidator,
    PrivateAttr,
    ValidationError,
    model_validator,
)
from pydantic.functional_validators import BeforeValidator
from pydantic_core import PydanticCustomError

from app.auth import cesta_pro_log
from app.chyby_poli import StavFormulare
from app.models import (
    NAZVY_PROSTREDKU,
    PROSTREDEK_AUTO,
    PROSTREDKY_NA_KM,
    ROLE_UZIVATEL,
    SMER_VYDEJ,
    Akce,
    Cestak,
    Clovek,
    Jizda,
    Kategorie,
    PokladniRadek,
    Prijem,
    Uzivatel,
    Vozidlo,
)
from app.routes.spolecne import MAX_ID, bez_koncovek, na_datum, na_desetinne
from app.services.termin_akce import MAX_DNU_AKCE, over_rok, over_termin

zurnal = logging.getLogger(__name__)

TYP_CHYBY = "formular"
MAX_CASTKA = Decimal(10_000_000)
MAX_KM = Decimal(100_000)
MAX_SPOTREBA = Decimal(100)
# Volne poznamky (sloupec Text v databazi nema delku).
POZNAMKA_MAX = 2000
# Tecka jako oddelovac tisicu ("1.500"): v cestine je to nejednoznacne
# (1,5 nebo 1500?), proto se misto hadani odmita. Kontroluje se zapis PRED
# prevodem carky na tecku - "905,555" je ctitelne (zaokrouhli se na haléře),
# "1.234,50" taky. "0.500" projde: nula na zacatku znamena desetinnou cast.
TECKA_JAKO_TISICE = re.compile(r"[1-9]\d{0,2}(\.\d{3})+")

OBECNA_HLASKA = "Formulář se nepodařilo přečíst. Zkontroluj zadané hodnoty a zkus to znovu."


def _chyba(zprava: str, pole: str | None = None) -> PydanticCustomError:
    """Chyba s ceskym textem. `pole` je jmeno pole formulare, ke kteremu chyba patri,
    kdyz ji hlasi cely model (termin akce): formular s chybou u pole (chyby_poli)
    podle nej pozna, ktere pole orámovat."""
    # Sablona je literal a text jde jako promenna - hlaska se tak nikdy nerozparsuje.
    return PydanticCustomError(TYP_CHYBY, "{zprava}", {"zprava": zprava, "pole": pole})


# --- text -------------------------------------------------------------------


def _na_text(hodnota: Any) -> str:
    """Text z pole formulare bez okolnich mezer. Chybejici pole je prazdny text;
    soubor podvrzeny pod jmenem textoveho pole je chyba, ne text."""
    if hodnota is None:
        return ""
    if isinstance(hodnota, str):
        return hodnota.strip()
    raise _chyba(OBECNA_HLASKA)


def _na_text_nebo_none(hodnota: Any) -> str | None:
    return _na_text(hodnota) or None


Text = Annotated[str, BeforeValidator(_na_text)]
TextNeboNic = Annotated[str | None, BeforeValidator(_na_text_nebo_none)]


def mez_delky(sloupec: Any) -> int:
    """Nejvetsi delka textu podle `String(n)` sloupce; sloupec bez delky (Text)
    dostane `POZNAMKA_MAX`."""
    return getattr(sloupec.type, "length", None) or POZNAMKA_MAX


def zprava_o_delce(mez: int) -> str:
    return f"Text je moc dlouhý, nejvýš {mez} znaků."


def _sloupec_pole(klic: str, meze: Mapping[Any, Any]) -> Any:
    """Sloupec, podle ktereho se hlida delka pole `klic`, nebo None.

    Klic `meze` je jmeno pole bez ID ("nazev" pro "nazev_5"), nebo zkompilovany
    vzor (`re.Pattern`) pro pole, jejichz ID neni na konci ("cast_5_0_popis")."""
    jmeno, _, zbytek = klic.rpartition("_")
    sloupec = meze.get(jmeno if zbytek.isdigit() else klic)
    if sloupec is not None:
        return sloupec
    for vzor, sloupec in meze.items():
        if isinstance(vzor, re.Pattern) and vzor.fullmatch(klic):
            return sloupec
    return None


def over_delky(formular: Mapping[str, Any], meze: Mapping[Any, Any]) -> None:
    """Zkontroluje delku textu v poli tabulky ("<pole>_<id>") PRED jakymkoli zapisem.

    `meze` je `{"nazev": Kategorie.nazev, ...}` - klic je jmeno pole bez ID,
    hodnota sloupec modelu, ze ktereho se bere delka. Pole, jehoz ID neni na
    konci jmena (cast_5_0_popis), se zada zkompilovanym vzorem (`re.compile`) misto jmena.
    Pole mimo `meze` se nekontroluji, soubor misto textu taky ne (ten zahazuje
    `radky_z_formulare`).
    Delsi text se odmitne stejnou cestou jako chyba modelu (`chyba_formulare`
    v app/main.py), takze se nic neulozi a clovek vidi hlasku.
    """
    for klic, hodnota in formular.items():
        if not isinstance(hodnota, str):
            continue
        sloupec = _sloupec_pole(klic, meze)
        if sloupec is not None and len(hodnota.strip()) > mez_delky(sloupec):
            raise RequestValidationError(
                [
                    {
                        "type": TYP_CHYBY,
                        "loc": ("body", klic),
                        "msg": zprava_o_delce(mez_delky(sloupec)),
                        "input": None,
                    }
                ]
            )


def delka(sloupec: Any = None, *, nejvic: int | None = None) -> AfterValidator:
    """Mez delky textu podle `String(n)` sloupce v modelu (nebo zadane `nejvic`).

    Sloupec bez delky (Text) dostane `POZNAMKA_MAX`. Pri prekroceni se text
    neorezava, ale odmitne.
    """
    mez = nejvic or mez_delky(sloupec)

    def hlidej(hodnota: str | None) -> str | None:
        if hodnota is not None and len(hodnota) > mez:
            raise _chyba(zprava_o_delce(mez))
        return hodnota

    return AfterValidator(hlidej)


# --- cisla a penize ---------------------------------------------------------


def _na_cislo(
    hodnota: Any, *, necitelne: str, zaporne: str, velke: str, nejvic: Decimal
) -> Decimal | None:
    """Desetinne cislo 0 az `nejvic`; prazdne pole je None.

    Exponent se odmita uz v textu (`1e30` by jinak prosel jako obycejne cislo)
    a NaN/Infinity pozna `is_finite`. Jinak se cte jako vsude (`1 234,50`).
    """
    text = _na_text(hodnota)
    if not text:
        return None
    if "e" in text.lower() or TECKA_JAKO_TISICE.fullmatch(bez_koncovek(text)):
        raise _chyba(necitelne)
    cislo = na_desetinne(text)
    if cislo is None or not cislo.is_finite():
        raise _chyba(necitelne)
    if cislo < 0:
        raise _chyba(zaporne)
    if cislo > nejvic:
        raise _chyba(velke)
    return cislo


def _castka(hodnota: Any) -> Decimal | None:
    return _na_cislo(
        hodnota,
        necitelne="Částku jsem nepřečetl. Napiš třeba 1500 nebo 1 500,50.",
        zaporne="Částka nemůže být záporná.",
        velke="Částka je moc velká — nejvýš 10 000 000 Kč.",
        nejvic=MAX_CASTKA,
    )


def _castka_povinna(hodnota: Any) -> Decimal:
    cislo = _castka(hodnota)
    if cislo is None:
        raise _chyba("Částku jsem nepřečetl. Napiš třeba 1500 nebo 1 500,50.")
    return cislo


def _spotreba(hodnota: Any) -> Decimal | None:
    return _na_cislo(
        hodnota,
        necitelne="Spotřebu jsem nepřečetl. Napiš třeba 6,5.",
        zaporne="Spotřeba nemůže být záporná.",
        velke="Spotřeba je moc velká — nejvýš 100 l na 100 km.",
        nejvic=MAX_SPOTREBA,
    )


# Penize (typ `Castka`): povinne a nepovinne pole.
CastkaPovinna = Annotated[Decimal, PlainValidator(_castka_povinna)]
CastkaNeboNic = Annotated[Decimal | None, PlainValidator(_castka)]
Spotreba = Annotated[Decimal | None, PlainValidator(_spotreba)]


def _na_id(hodnota: Any) -> int | None:
    """ID z nabidky (select): prazdne = nevybrano, jinak jen cislice a rozumna
    velikost. Jestli zaznam existuje, hlida routa - tady neni databaze."""
    text = _na_text(hodnota)
    if not text:
        return None
    if not (text.isascii() and text.isdigit()) or int(text) > MAX_ID:
        raise _chyba("Vybraná položka v nabídce neexistuje.")
    return int(text)


Id = Annotated[int | None, PlainValidator(_na_id)]


def id_z_textu(text: str) -> int | None:
    """ID z nabidky v tabulce: cele cislo, nebo None u prazdneho a nectitelneho."""
    try:
        return _na_id(text)
    except PydanticCustomError:
        return None


def _datum(hodnota: Any) -> dt.date | None:
    text = _na_text(hodnota)
    if not text:
        return None
    den = na_datum(text)
    if den is None:
        raise _chyba("Datum jsem nepřečetl.")
    if chyba := over_rok(den):
        raise _chyba(chyba)
    return den


Datum = Annotated[dt.date | None, PlainValidator(_datum)]

# Souradnice z mapy: meze zemepisne site, NaN a Infinity ne.
ZemSirka = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
ZemDelka = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]


def _prazdne_na_none(hodnota: Any) -> Any:
    """Skryte pole formulare bez souradnice prijde jako prazdny retezec."""
    if isinstance(hodnota, str) and not hodnota.strip():
        return None
    return hodnota


ZemSirkaNeboNic = Annotated[ZemSirka | None, BeforeValidator(_prazdne_na_none)]
ZemDelkaNeboNic = Annotated[ZemDelka | None, BeforeValidator(_prazdne_na_none)]


# --- formulare --------------------------------------------------------------


class Formular(BaseModel):
    # Prohlizec posila i pole, ktera model nezna (`kam` apod.) - ta se ignoruji.
    model_config = ConfigDict(extra="ignore")


class AkceForm(Formular):
    """Zalozeni akce (`POST /akce`)."""

    nazev: Annotated[Text, delka(Akce.nazev)] = ""
    datum_od: Datum = None
    datum_do: Datum = None
    datum_vyuctovani: Datum = None
    pokladnik: Id = None

    @model_validator(mode="after")
    def _termin(self) -> AkceForm:
        if chyba := over_termin(self.datum_od, self.datum_do):
            # Chybi-li jen zacatek, patri chyba k nemu; jinak ke konci.
            raise _chyba(chyba, "datum_od" if self.datum_od is None else "datum_do")
        return self


class AkceUpravaForm(AkceForm):
    """Uprava akce - k zalozeni pribyva predpona dokladu."""

    prefix_dokladu: Annotated[TextNeboNic, delka(Akce.prefix_dokladu)] = None


class CestakForm(Formular):
    ucel_cesty: Annotated[Text, delka(Cestak.ucel_cesty)] = ""
    misto: Annotated[TextNeboNic, delka(Cestak.misto)] = None
    spolucestujici: Annotated[TextNeboNic, delka(Cestak.spolucestujici)] = None
    poznamka: Annotated[TextNeboNic, delka(Cestak.poznamka)] = None
    jednotka: Annotated[TextNeboNic, delka(Cestak.jednotka)] = None
    cestujici_id: Id = None
    vozidlo_id: Id = None
    palivova_uctenka_id: Id = None
    cena_za_litr: CastkaNeboNic = None
    opotrebeni: CastkaNeboNic = None


def sloupce_jizdy(prostredek: str, text: str) -> dict[str, Any]:
    """Jeden sloupec tabulky, dve ruzna pole podle prostredku: u auta kilometry
    (0 az 100 000), u vlaku a dalsich cena jizdenky. Prazdny text je None,
    nectitelny nebo mimo meze `PydanticCustomError`."""
    if prostredek in PROSTREDKY_NA_KM:
        cislo = _na_cislo(
            text,
            necitelne="Kilometry jsem nepřečetl. Napiš třeba 120 nebo 12,4.",
            zaporne="Kilometry nemůžou být záporné.",
            velke="Kilometrů je moc — nejvýš 100 000.",
            nejvic=MAX_KM,
        )
        # Jedno desetinne misto, ROUND_HALF_UP jako u mapy (12,37 -> 12,4).
        return {
            "km": cislo.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            if cislo is not None
            else None
        }
    return {"jizdne": _castka(text)}


class JizdaForm(Formular):
    """Jizda pridana rucne. Sloupec `km` v tabulce je podle prostredku bud
    pocet kilometru, nebo jizdne - proto se cislo cte az tady, kdyz je znamy
    prostredek (`sloupce`)."""

    datum: Datum = None
    odkud: Annotated[TextNeboNic, delka(Jizda.odkud)] = None
    kam: Annotated[TextNeboNic, delka(Jizda.kam)] = None
    prostredek: Text = PROSTREDEK_AUTO
    km: Text = ""
    # Body z mapy (vyplni je app/static/cestak_mapa.js, kdyz clovek misto vybral
    # v mape). Jen pro nakresleni jizdy v mape - kilometry se berou z pole `km`.
    z_lat: ZemSirkaNeboNic = None
    z_lon: ZemDelkaNeboNic = None
    do_lat: ZemSirkaNeboNic = None
    do_lon: ZemDelkaNeboNic = None

    _sloupce: dict[str, Any] = PrivateAttr(default_factory=dict)

    @model_validator(mode="after")
    def _km_nebo_jizdne(self) -> JizdaForm:
        if self.prostredek not in NAZVY_PROSTREDKU:
            self.prostredek = PROSTREDEK_AUTO
        self._sloupce = sloupce_jizdy(self.prostredek, self.km)
        # Pulka dvojice bodu by mapu jen mátla: bud jsou body oba, nebo zadny.
        if None in (self.z_lat, self.z_lon, self.do_lat, self.do_lon):
            self.z_lat = self.z_lon = self.do_lat = self.do_lon = None
        return self

    def sloupce(self) -> dict[str, Any]:
        """`{"km": ...}` nebo `{"jizdne": ...}` pro `Jizda(**...)`."""
        return self._sloupce

    def souradnice(self) -> dict[str, float | None]:
        """Body z mapy pro `Jizda(**...)`; bez mapy samé `None`."""
        return {
            "z_lat": self.z_lat,
            "z_lon": self.z_lon,
            "do_lat": self.do_lat,
            "do_lon": self.do_lon,
        }


class CestakUlozitForm(Formular):
    """Hlavicka cestaku ukladana prubezne: co formular neposlal, v
    `model_fields_set` neni a nemeni se."""

    ucel_cesty: Annotated[Text, delka(Cestak.ucel_cesty)] = ""
    misto: Annotated[TextNeboNic, delka(Cestak.misto)] = None
    spolucestujici: Annotated[TextNeboNic, delka(Cestak.spolucestujici)] = None
    poznamka: Annotated[TextNeboNic, delka(Cestak.poznamka)] = None
    jednotka: Annotated[TextNeboNic, delka(Cestak.jednotka)] = None
    cestujici_id: Id = None
    vozidlo_id: Id = None
    palivova_uctenka_id: Id = None
    cena_za_litr: CastkaNeboNic = None
    opotrebeni: CastkaNeboNic = None


class RadekKnihyForm(Formular):
    ucel: Annotated[Text, delka(PokladniRadek.ucel)] = ""
    castka: CastkaPovinna
    smer: Text = SMER_VYDEJ
    datum: Datum = None


class VratkaForm(Formular):
    ucastnik_id: Id = None
    castka: CastkaPovinna
    datum: Datum = None


class PrijemForm(Formular):
    popis: Annotated[Text, delka(Prijem.popis)] = "Účastnické poplatky"
    castka: CastkaPovinna
    datum: Datum = None
    # Kdo vybral: ID cloveka z ciselniku (existenci hlida routa).
    clovek: Id = None


class ClovekForm(Formular):
    """Pridani cloveka do ciselniku lidi (`POST /lide/pridat`)."""

    jmeno: Annotated[Text, delka(Clovek.jmeno)] = ""


class KategorieForm(Formular):
    """Pridani kategorie do ciselniku (`POST /kategorie/pridat`). Klic je nepovinny."""

    nazev: Annotated[Text, delka(Kategorie.nazev)] = ""
    klic: Annotated[Text, delka(Kategorie.klic)] = ""


class VozidloForm(Formular):
    nazev: Annotated[Text, delka(Vozidlo.nazev)] = ""
    spz: Annotated[TextNeboNic, delka(Vozidlo.spz)] = None
    spotreba: Spotreba = None


class PrihlaseniForm(Formular):
    """Prihlaseni (`POST /prihlaseni`). Heslo se neorezava ani nevraci do stranky
    (`tajna` ve `formular_s_chybami`); chyba se hlasi jen u prazdneho pole,
    nikdy podle toho, jestli ucet existuje."""

    uzivatel: Text = ""
    heslo: str = ""


class ZapomenuteHesloForm(Formular):
    """Zadost o odkaz na obnoveni hesla (`POST /zapomenute-heslo`). Chyba je jen
    v tom, co clovek nevyplnil nebo co je moc dlouhe; zda ucet existuje, se
    tady neprozradi."""

    prihlaseni: Annotated[Text, delka(Uzivatel.email)] = ""


class PozvankaForm(Formular):
    """Prijeti pozvanky (`POST /pozvanka/{token}`). Hesla se neorezavaji a nevraci
    do stranky (`tajna`); chybejici pole je prazdne, chybu hlasi pravidla uctu."""

    login: Annotated[Text, delka(Uzivatel.login)] = ""
    jmeno: Annotated[Text, delka(Uzivatel.jmeno)] = ""
    heslo: str = ""
    znovu_heslo: str = ""


class ObnovitHesloForm(Formular):
    """Nove heslo z odkazu v e-mailu (`POST /heslo/obnovit/{token}`). Hesla se
    neorezavaji a nevraci do stranky (`tajna`); pravidla hesla hlida `chyba_hesla`."""

    heslo: str = ""
    znovu_heslo: str = ""


class UzivatelForm(Formular):
    """Novy ucet. Tvar loginu a e-mailu a platnost role hlida sluzba `zaloz_ucet`."""

    login: Annotated[Text, delka(Uzivatel.login)] = ""
    jmeno: Annotated[Text, delka(Uzivatel.jmeno)] = ""
    email: Annotated[TextNeboNic, delka(Uzivatel.email)] = None
    role: Text = ROLE_UZIVATEL
    poslat_pozvanku: bool = False


class ProfilForm(Formular):
    """Vlastni profil (`POST /profil/ulozit`). `heslo` je aktualni heslo, ktere se
    nikdy neorezava (mezera na kraji hesla je soucast hesla) ani nevraci do stranky."""

    jmeno: Annotated[Text, delka(Uzivatel.jmeno)] = ""
    email: Annotated[TextNeboNic, delka(Uzivatel.email)] = None
    heslo: str = ""


class ZmenaHeslaForm(Formular):
    """Zmena hesla v profilu. Hesla se neorezavaji a nevraci do stranky."""

    stare_heslo: str = ""
    nove_heslo: str = ""
    znovu_heslo: str = ""


# --- hromadne tabulky -------------------------------------------------------
#
# Tabulka (ucenky, ucastnici, pokladni kniha) se uklada po radcich a jedno
# nectitelne pole nesmi shodit ani zahodit cely radek. Pravidlo:
# prazdne pole = hodnotu smazat, nectitelne = nechat starou a zapocitat.
# Tolerantni typy proto chybu nevyhodi, ale vrati `NECITELNE`.


class _Necitelne:
    def __repr__(self) -> str:
        return "NECITELNE"


NECITELNE = _Necitelne()


def _tolerantne(prevod: Any) -> PlainValidator:
    def bezpecne(hodnota: Any) -> Any:
        try:
            return prevod(hodnota)
        except PydanticCustomError:
            return NECITELNE

    return PlainValidator(bezpecne)


def _dny(hodnota: Any) -> int | None:
    """Pocet dnu ucastnika (osobodny, detodny): cele cislo 0 az delka nejdelsi akce."""
    text = _na_text(hodnota)
    if not text:
        return None
    if not (text.isascii() and text.isdigit()) or int(text) > MAX_DNU_AKCE:
        raise _chyba(f"Počet dnů musí být celé číslo od 0 do {MAX_DNU_AKCE}.")
    return int(text)


# `Any`, protoze hodnota je bud skutecna hodnota, nebo `NECITELNE`.
CastkaRadku = Annotated[Any, _tolerantne(_castka)]
IdRadku = Annotated[Any, _tolerantne(_na_id)]
DnyRadku = Annotated[Any, _tolerantne(_dny)]
SpotrebaRadku = Annotated[Any, _tolerantne(_spotreba)]


class RadekTabulky(Formular):
    """Jeden radek tabulky. Co ve formulari nebylo, v `model_fields_set` neni a
    nemeni se. Texty a data si cte routa sama, tady jsou jen cisla a ID z nabidky."""

    def zmeny(self) -> dict[str, Any]:
        """Pole, ktera formular poslal a podarilo se je precist (prazdne = None)."""
        hodnoty = {pole: getattr(self, pole) for pole in self.model_fields_set}
        return {pole: h for pole, h in hodnoty.items() if h is not NECITELNE}

    def necitelnych(self) -> int:
        return sum(1 for pole in self.model_fields_set if getattr(self, pole) is NECITELNE)


class UctenkaRadekForm(RadekTabulky):
    castka: CastkaRadku = None
    litr: CastkaRadku = None
    kdo: IdRadku = None
    kategorie: IdRadku = None


class CastRozpisuForm(RadekTabulky):
    kategorie: IdRadku = None
    castka: CastkaRadku = None


class UcastnikRadekForm(RadekTabulky):
    osobodny: DnyRadku = None
    detodny: DnyRadku = None
    hotove: CastkaRadku = None
    prevodem: CastkaRadku = None
    vouchery: CastkaRadku = None


class VozidloRadekForm(RadekTabulky):
    spotreba: SpotrebaRadku = None


class CastkaKnihyForm(RadekTabulky):
    castka: CastkaRadku = None


class PrijemRadekForm(RadekTabulky):
    castka: CastkaRadku = None


MAX_PORADI = 10_000


def poradi_z_textu(text: str) -> int | None:
    """Poradi v ciselniku: cele cislo 0 az `MAX_PORADI`, jinak None (nechat stare)."""
    if text.isascii() and text.isdigit() and int(text) <= MAX_PORADI:
        return int(text)
    return None


def castka_z_bunky(hodnota: Any) -> Decimal | None:
    """Castka z bunky Excelu (import ucastniku): stejna pravidla jako ve formulari.

    Prazdna bunka je None, necitelna hodnota (text, NaN, zaporne cislo, nad
    mez) je `ValueError`. Cislo v bunce se bere tak, jak je - "2.125" jako text
    je podezrely zapis, ale cislo 2,125 je cislo.
    """
    if hodnota is None or (isinstance(hodnota, str) and not hodnota.strip()):
        return None
    if isinstance(hodnota, int | float | Decimal) and not isinstance(hodnota, bool):
        cislo = Decimal(str(hodnota))
        if not cislo.is_finite() or not 0 <= cislo <= MAX_CASTKA:
            raise ValueError("necitelna castka")
        return cislo
    try:
        return _castka(str(hodnota))
    except PydanticCustomError as chyba:
        raise ValueError("necitelna castka") from chyba


# --- hlaska pro uzivatele ---------------------------------------------------


def hlaska(chyba: RequestValidationError) -> str | None:
    """Cesky text pro uzivatele, nebo None, kdyz chyba neni z tela formulare
    (cesta, dotaz) - tu necha obsluhu FastAPI. Ukaze prvni vlastni hlasku;
    obecne chyby Pydantic ("Field required") dostanou jednu obecnou vetu."""
    chyby = [c for c in chyba.errors() if c["loc"] and c["loc"][0] == "body"]
    if not chyby:
        return None
    for c in chyby:
        if c["type"] == TYP_CHYBY:
            return c["msg"]
    for c in chyby:
        if c["type"] == "string_too_long":
            return zprava_o_delce(c["ctx"]["max_length"])
    return OBECNA_HLASKA


def text_chyby(c: Any) -> str:
    """Cesky text jedne chyby Pydantic (jeden prvek `errors()`) pro pole formulare."""
    if c["type"] == TYP_CHYBY:
        return c["msg"]
    if c["type"] == "string_too_long":
        return zprava_o_delce(c["ctx"]["max_length"])
    return OBECNA_HLASKA


def pole_s_chybou(chyba: RequestValidationError) -> list[tuple[str, str]]:
    """Jmena poli a typy chyb pro log - nikdy ne zadane hodnoty (osobni udaje)."""
    return [
        (".".join(str(c) for c in e["loc"][1:]), e["type"])
        for e in chyba.errors()
        if e["loc"] and e["loc"][0] == "body"
    ]


# --- formular s chybou u pole -----------------------------------------


def _stav_z_vyjimky(hodnoty: dict[str, str], chyba: ValidationError) -> StavFormulare:
    stav = StavFormulare(hodnoty=hodnoty, odeslano=True)
    for c in chyba.errors():
        # Chyba celeho modelu (termin) nese jmeno pole v `ctx`, viz `_chyba`.
        pole = str(c["loc"][0]) if c["loc"] else (c.get("ctx") or {}).get("pole")
        text = text_chyby(c)
        if pole is None:
            stav.obecne.append(text)
        else:
            stav.chyby.setdefault(pole, text)
    return stav


def formular_s_chybami[M: BaseModel](
    model: type[M], *, tajna: tuple[str, ...] = ()
) -> Callable[[Request], Awaitable[M | StavFormulare]]:
    """Zavislost (`Depends`) pro route: hotovy model, nebo `StavFormulare` s chybami.

    Misto obecne cesty (`chyba_formulare`: hlaska a navrat) route dostane stav a
    stranku vykresli znovu se stavem 422 (`sablony.strana_s_chybou`) - hodnoty
    zustanou a chyba je u pole. Do databaze se tou dobou nic nezapsalo.
    `tajna` jsou pole, jejichz hodnota se nesmi vratit do stranky (hesla).
    Chyba mimo telo formulare (adresa, dotaz) tudy neprojde: ta zustava obecna.
    """

    async def nacti(request: Request) -> M | StavFormulare:
        odeslano = await request.form()
        hodnoty = {
            klic: hodnota
            for klic, hodnota in odeslano.items()
            if isinstance(hodnota, str) and klic not in tajna
        }
        # Route z toho sestavi chybu, kterou nasla az sama (`chyby_poli.stav_s_chybami`).
        request.state.hodnoty_formulare = hodnoty
        try:
            return model.model_validate(dict(odeslano))
        except ValidationError as chyba:
            # Jen jmena poli a typy chyb - zadane hodnoty jsou osobni udaje. Cesta
            # pozvanky a obnovy hesla nese token, proto `cesta_pro_log`.
            zurnal.info(
                "Formulář %s zamítnut, pole: %s",
                cesta_pro_log(request.url.path),
                [(".".join(str(c) for c in e["loc"]), e["type"]) for e in chyba.errors()],
            )
            return _stav_z_vyjimky(hodnoty, chyba)

    return nacti
