"""Obnova akce ze zalohy - opak app/services/zaloha.py.

Cte ZIP, ktery vyrobilo tlacitko "Stáhnout zálohu akce", a slozi z nej
UPLNE NOVOU akci (Honza, 12. 9. 2026 - obnova nikdy neprepisuje existujici
akci, jen zaklada dalsi). Zaloha v jinem/starsim formatu (jiny nebo chybejici
`verze_zalohy`) se rovnou odmitne jasnou hlaskou - radeji nic, nez tise
poloobnovena akce s chybejicimi udaji.

Zaloha je soubor, ktery mohl projit cizyma rukama, proto se `data.json` NEJDRIV
cely overi schematem (modely `Zaloha*` nize: povolene hodnoty, rozsahy castek
a dat, delky textu, indexy v mezich seznamu) a teprve z overenych dat se
sklada databaze. Neplatna zaloha skonci jednou obecnou hlaskou, detail
(kde je chyba, bez hodnot - mohou to byt udaje deti) jde jen do logu.

Soubory skenu se na disk zapisuji az na uplny konec `obnov()`, pote co se cela
zaloha v poradku precetla a poskladala v pameti - kdyby neco vybuchlo driv
(spatny ZIP, poskozeny data.json, index mimo rozsah), na disku tak nezustanou
osirele soubory k rucnimu mazani.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import logging
import uuid
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Annotated, NamedTuple

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import nastaveni
from app.models import (
    CINNOST_OBNOVA,
    DRUH_ARCH_JIZDENEK,
    DRUH_CESTOVNI_PRIKAZ,
    DRUH_JINE,
    DRUH_PALIVO,
    DRUH_UCTENKA,
    NAZVY_PROSTREDKU,
    PROSTREDEK_AUTO,
    SMER_PRIJEM,
    SMER_VYDEJ,
    STAV_ROZPRACOVANA,
    STAV_UZAVRENA,
    Akce,
    CastSkenu,
    Cestak,
    Clovek,
    Dar,
    Jizda,
    Kategorie,
    Obdarovany,
    PokladniRadek,
    Prijem,
    Sken,
    Slozka,
    Ucastnik,
    Vozidlo,
    ZaznamCinnosti,
)
from app.models.ciselniky import zkrat_klic_kategorie
from app.models.konstanty import PEVNE_KLICE_KATEGORII
from app.services.ciselniky import clovek_podle_jmena, pevny_klic_podle_stareho_nazvu, uz_existuje
from app.services.soubory import (
    POVOLENE_PRIPONY,
    ChybaObsahu,
    over_obsah,
    rozbalena_velikost_zipu,
    smaz_soubory,
)
from app.services.termin_akce import over_termin
from app.services.zaloha import VERZE_ZALOHY

# Verze data.json, ktere obnova umi precist. Verze 3 nema `puvodni_slozka_palivovky`
# a km jsou v ni cela cisla - obojí nove pole a typ bez problemu prijmou.
# Verze 3 a 4 nesou u ucastnika i `vratka`; ta se ignoruje, spocita se z radku knihy.
PODPOROVANE_VERZE_ZALOHY = (3, 4, 5, VERZE_ZALOHY)

log = logging.getLogger(__name__)

OBECNA_HLASKA_SCHEMATU = "Záloha obsahuje neplatná data a nedá se bezpečně obnovit."


class ChybaObnovy(Exception):
    """Zalohu se nepodarilo obnovit. Text miri primo do oznam() na strance -
    proto je vzdy v citelne cestine, ne technicky popis vyjimky."""


class VysledekObnovy(NamedTuple):
    akce: Akce
    zapsane_soubory: list[str]  # nazvy souboru, ktere obnov() zapsal na disk
    chybejici_soubory: list[str]  # skeny, jejichz soubor uz v zaloze chybel


# --- Stejny ZIP podruhe ---------------------------------------------
#
# Obnova kazdym odeslanim zaklada novou akci, takze dvojklik nebo dvakrat nahrany
# tentyz soubor vyrobi dve kopie se vsemi skeny na disku. Aby se to nestalo omylem,
# zapisuje se do popisu souhrnneho radku logu otisk (SHA-256) nahraneho ZIPu a pred
# dalsi obnovou se hleda. Do logu a ne do vlastni tabulky proto, ze radek tam uz
# stejne vznika a nova tabulka by chtela migraci; otisk obsah zalohy neprozradi.


def otisk_zip(obsah: bytes) -> str:
    """Kratky otisk nahraneho ZIPu (prvnich 16 znaku SHA-256)."""
    return hashlib.sha256(obsah).hexdigest()[:16]


_ZNACKA_OTISKU = "otisk ZIPu "


def popis_obnovy(nazev: str, otisk: str) -> str:
    """Popis souhrnneho radku v logu; `predchozi_obnova` ho podle otisku hleda."""
    return f"obnovena akce „{nazev}“ ze zálohy ({_ZNACKA_OTISKU}{otisk})"


def predchozi_obnova(sezeni: Session, otisk: str) -> ZaznamCinnosti | None:
    """Posledni obnova z ZIPu se stejnym otiskem, nebo None."""
    return sezeni.scalars(
        select(ZaznamCinnosti)
        .where(
            ZaznamCinnosti.typ_cinnosti == CINNOST_OBNOVA,
            ZaznamCinnosti.popis.endswith(f"({_ZNACKA_OTISKU}{otisk})"),
        )
        .order_by(ZaznamCinnosti.do.desc())
    ).first()


# --- Schema data.json ---------------------------------------------
#
# Modely zrcadli, co vyrabi app/services/zaloha.py. Delky textu jsou podle
# `String(n)` v app/models/. Volne `Text` pole maji strop 10 000 znaku -
# volnejsi nez formulare (ty zprisni kapitoly o kontrole formularu), protoze
# obnova musi prijmout vsechno, co appka dosud ulozila.

_MAX_CASTKA = Decimal(10_000_000)  # pravidlo "0 az 10 mil. Kc"
_DATUM_NEJDRIV = dt.date(2000, 1, 1)
_DATUM_NEJPOZDEJI = dt.date(2100, 12, 31)


def _z_vycetu(hodnoty) -> AfterValidator:
    povolene = frozenset(hodnoty)

    def over(hodnota: str) -> str:
        if hodnota not in povolene:
            raise ValueError("hodnota není v seznamu povolených")
        return hodnota

    return AfterValidator(over)


def _je_json(hodnota: str | None) -> str | None:
    if hodnota is not None:
        try:
            json.loads(hodnota)
        except ValueError:
            raise ValueError("není platný JSON") from None
    return hodnota


# Texty podle delky sloupce `String(n)` v app/models/ (pyright nepusti volani
# funkce primo v typu, proto hotove aliasy).
Text20 = Annotated[str, Field(max_length=20)]
Text30 = Annotated[str, Field(max_length=30)]
Text50 = Annotated[str, Field(max_length=50)]
Text60 = Annotated[str, Field(max_length=60)]
Text80 = Annotated[str, Field(max_length=80)]
Text100 = Annotated[str, Field(max_length=100)]
Text120 = Annotated[str, Field(max_length=120)]
Text150 = Annotated[str, Field(max_length=150)]
Text200 = Annotated[str, Field(max_length=200)]
Text300 = Annotated[str, Field(max_length=300)]
Text1000 = Annotated[str, Field(max_length=1000)]


# Index do jineho seznamu v zaloze. Zaporny by Python tise vzal od konce
# (`skeny[-1]`) a propojil by jine zaznamy, nez byly - proto `ge=0`; horni mez
# se kontroluje v `Zaloha._over_indexy`, az jsou znamy delky seznamu.
Index = Annotated[int, Field(ge=0)]
Poradi = Annotated[int, Field(ge=-(2**31), le=2**31)]
Penize = Annotated[Decimal, Field(ge=0, le=_MAX_CASTKA, allow_inf_nan=False)]
Spotreba = Annotated[Decimal, Field(ge=0, le=1000, allow_inf_nan=False)]
Datum = Annotated[dt.date, Field(ge=_DATUM_NEJDRIV, le=_DATUM_NEJPOZDEJI)]
DatumNarozeni = Annotated[dt.date, Field(ge=dt.date(1900, 1, 1), le=_DATUM_NEJPOZDEJI)]
Zemepis = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
VolnyText = Annotated[str, Field(max_length=10_000)]
VolnyJson = Annotated[str, Field(max_length=10_000), AfterValidator(_je_json)]


class _Radek(BaseModel):
    # Pole navic v data.json se ignoruji - obnova kvuli neskodnemu poli nespadne.
    model_config = ConfigDict(extra="ignore")


class ZalohaCiselnikKategorie(_Radek):
    nazev: Text80
    # Sloupec je String(30), ale formular kategorii dlouho delku nehlidal
    # a SQLite ji nevynucuje - zalohy vytvorene driv, nez se klic zkratil
    #, ho maji i o 40 znacich. Takova zaloha se musi dat obnovit, proto
    # se tu bere az 100 znaku a `_Ciselniky.kategorie` ho pri zalozeni zkrati.
    klic: Text100 | None = None
    poradi: Poradi = 0


class ZalohaCiselnikClovek(_Radek):
    jmeno: Text120
    poradi: Poradi = 0
    adresa: Text200 | None = None
    kontakt: Text120 | None = None


class ZalohaCiselnikVozidlo(_Radek):
    nazev: Text120
    spz: Text20 | None = None
    spotreba: Spotreba | None = None
    poradi: Poradi = 0


class ZalohaAkce(_Radek):
    nazev: Text200
    datum_vyuctovani: Datum | None = None
    datum_od: Datum | None = None
    datum_do: Datum | None = None
    stav: Annotated[str, _z_vycetu((STAV_ROZPRACOVANA, STAV_UZAVRENA))] = STAV_ROZPRACOVANA
    prefix_dokladu: Text20 | None = None
    pokladnik: Text120 | None = None

    @model_validator(mode="after")
    def _termin_ma_rozumnou_delku(self) -> ZalohaAkce:
        # Stejne pravidlo jako formular (konec ne pred zacatkem, nejvyse 62 dni) -
        # jinak by obnova obesla B-025 a obratem ulozila akci na tisice dni.
        # Jen kdyz jsou vyplnene oba dny: zaloha muze nest i akci s jednim koncem
        # z doby, kdy to appka jeste pripoustela, a obnova musi prijmout vsechno.
        if self.datum_od and self.datum_do and (chyba := over_termin(self.datum_od, self.datum_do)):
            raise ValueError(chyba)
        return self


class ZalohaSlozka(_Radek):
    nazev: Text200
    kategorie: Text80 | None = None
    clovek: Text120 | None = None
    ucel_platby: Text200 | None = None
    poradi: Poradi = 0


class ZalohaCastSkenu(_Radek):
    kategorie: Text80 | None = None
    castka: Penize | None = None
    popis: Text200 | None = None
    poradi: Poradi = 0


class ZalohaSken(_Radek):
    soubor_v_zipu: Text1000 | None = None
    puvodni_nazev: Text300
    pocet_stranek: Annotated[int, Field(ge=0, le=10_000)] = 1
    nazev: Text200 | None = None
    druh: Annotated[
        str,
        _z_vycetu((DRUH_UCTENKA, DRUH_ARCH_JIZDENEK, DRUH_PALIVO, DRUH_CESTOVNI_PRIKAZ, DRUH_JINE)),
    ] = DRUH_UCTENKA
    obchodnik: Text150 | None = None
    datum: Datum | None = None
    castka: Penize | None = None
    cena_za_litr: Penize | None = None
    clovek: Text120 | None = None
    kategorie: Text80 | None = None
    je_priloha: bool = False
    priloha_k: Index | None = None
    zkontrolovano: bool = False
    varovani_ztiseno: bool = False
    jistota: Annotated[str, _z_vycetu(("vysoka", "stredni", "nizka"))] | None = None
    poznamka: VolnyText | None = None
    rozpis_json: VolnyJson | None = None
    poradi: Poradi = 0
    poradi_v_knize: Poradi | None = None
    slozka: Index | None = None
    casti: list[ZalohaCastSkenu] = []


class ZalohaPrijem(_Radek):
    popis: Text200
    castka: Penize
    # Od verze 6 jmeno z ciselniku lidi; `kdo` je volny text (starsi zalohy, nebo
    # prijem, jehoz jmeno se v ciselniku nenaslo).
    clovek: Text120 | None = None
    kdo: Text120 | None = None
    datum: Datum | None = None
    poradi: Poradi = 0
    poradi_v_knize: Poradi | None = None


class ZalohaPokladniRadek(_Radek):
    datum: Datum | None = None
    ucel: Text200
    smer: Annotated[str, _z_vycetu((SMER_PRIJEM, SMER_VYDEJ))] = SMER_VYDEJ
    castka: Penize
    poradi: Poradi = 0
    poradi_v_knize: Poradi | None = None
    ucastnik: Index | None = None
    cestak: Index | None = None


class ZalohaUcastnik(_Radek):
    poradi: Poradi = 0
    jmeno: Text80 | None = None
    prijmeni: Text80 | None = None
    prezdivka: Text80 | None = None
    ulice: Text150 | None = None
    mesto: Text100 | None = None
    psc: Text20 | None = None
    datum_narozeni: DatumNarozeni | None = None
    jednotka: Text50 | None = None
    osobodny: Annotated[int, Field(ge=0, le=100_000)] | None = None
    detodny: Annotated[int, Field(ge=0, le=100_000)] | None = None
    dochazka_json: VolnyJson | None = None
    hotove: Penize | None = None
    hotove_doklad: Text60 | None = None
    prevodem: Penize | None = None
    prevodem_doklad: Text60 | None = None
    vouchery: Penize | None = None
    vouchery_doklad: Text60 | None = None
    vratka_doklad: Text60 | None = None


class ZalohaObdarovany(_Radek):
    poradi: Poradi = 0
    jmeno: Text80 | None = None
    prijmeni: Text80 | None = None
    prezdivka: Text80 | None = None
    ulice: Text150 | None = None
    mesto: Text100 | None = None
    psc: Text20 | None = None
    datum_narozeni: DatumNarozeni | None = None


class ZalohaDar(_Radek):
    nazev: Text200
    poradi: Poradi = 0
    sken: Index | None = None
    obdarovany: Index | None = None


class ZalohaJizda(_Radek):
    datum: Datum | None = None
    odkud: Text150 | None = None
    kam: Text150 | None = None
    prostredek: Annotated[str, _z_vycetu(NAZVY_PROSTREDKU)] = PROSTREDEK_AUTO
    # Verze 3 ma cela cisla, od verze 4 text s jednim desetinnym mistem ("12.4").
    km: Annotated[Decimal, Field(ge=0, le=100_000, decimal_places=1)] | None = None
    jizdne: Penize | None = None
    z_lat: Zemepis | None = None
    z_lon: Zemepis | None = None
    do_lat: Zemepis | None = None
    do_lon: Zemepis | None = None
    poradi: Poradi = 0


class ZalohaCestak(_Radek):
    ucel_cesty: Text200
    misto: Text200 | None = None
    spolucestujici: Text300 | None = None
    poznamka: VolnyText | None = None
    jednotka: Text30 | None = None
    cestujici: Text120 | None = None
    adresa: Text200 | None = None
    kontakt: Text120 | None = None
    vozidlo: Text120 | None = None
    spotreba: Spotreba | None = None
    palivova_uctenka: Index | None = None
    puvodni_slozka_palivovky: Index | None = None
    cena_za_litr: Penize | None = None
    opotrebeni: Penize | None = None
    poradi: Poradi = 0
    jizdy: list[ZalohaJizda] = []


class Zaloha(_Radek):
    """Cely `data.json`. Seznamy bez klice v souboru jsou prazdne."""

    akce: ZalohaAkce
    kategorie: list[ZalohaCiselnikKategorie] = []
    lide: list[ZalohaCiselnikClovek] = []
    vozidla: list[ZalohaCiselnikVozidlo] = []
    slozky: list[ZalohaSlozka] = []
    skeny: list[ZalohaSken] = []
    prijmy: list[ZalohaPrijem] = []
    pokladni_radky: list[ZalohaPokladniRadek] = []
    ucastnici: list[ZalohaUcastnik] = []
    obdarovani: list[ZalohaObdarovany] = []
    dary: list[ZalohaDar] = []
    cestaky: list[ZalohaCestak] = []

    @model_validator(mode="after")
    def _over_indexy(self) -> Zaloha:
        """Kazdy index musi mirit do existujiciho zaznamu. Text chyby nesmi
        obsahovat hodnoty ze zalohy - jen kde je chyba."""
        pocty = {
            "slozky": len(self.slozky),
            "skeny": len(self.skeny),
            "ucastnici": len(self.ucastnici),
            "cestaky": len(self.cestaky),
            "obdarovani": len(self.obdarovani),
        }
        odkazy = (
            [("skeny.slozka", "slozky", s.slozka) for s in self.skeny]
            + [("skeny.priloha_k", "skeny", s.priloha_k) for s in self.skeny]
            + [("pokladni_radky.ucastnik", "ucastnici", r.ucastnik) for r in self.pokladni_radky]
            + [("pokladni_radky.cestak", "cestaky", r.cestak) for r in self.pokladni_radky]
            + [("cestaky.palivova_uctenka", "skeny", c.palivova_uctenka) for c in self.cestaky]
            + [
                ("cestaky.puvodni_slozka_palivovky", "slozky", c.puvodni_slozka_palivovky)
                for c in self.cestaky
            ]
            + [("dary.sken", "skeny", d.sken) for d in self.dary]
            + [("dary.obdarovany", "obdarovani", d.obdarovany) for d in self.dary]
        )
        for pole, cil, index in odkazy:
            if index is not None and index >= pocty[cil]:
                raise ValueError(f"index v poli {pole} míří mimo seznam {cil}")
        return self


# --- Cteni ZIPu -----------------------------------------------------------


def _otevri_zip(obsah: bytes) -> zipfile.ZipFile:
    try:
        archiv = zipfile.ZipFile(io.BytesIO(obsah))
    except zipfile.BadZipFile:
        raise ChybaObnovy("Tohle není platný ZIP soubor.") from None
    # Zaloha je taky ZIP archiv - data.json v ni muze byt samo male, ale po
    # rozbaleni zabrat stovky MB pameti ("zip bomba", B-037c).
    if rozbalena_velikost_zipu(archiv) > nastaveni.max_rozbalena_velikost_mb * 1024 * 1024:
        raise ChybaObnovy(
            f"Záloha po rozbalení zabírá víc než {nastaveni.max_rozbalena_velikost_mb} MB "
            "— je poškozená, nebo to není platná záloha z téhle aplikace."
        )
    return archiv


def _precti_data(archiv: zipfile.ZipFile) -> Zaloha:
    try:
        surova = archiv.read("data.json")
    except KeyError:
        raise ChybaObnovy("V ZIPu chybí data.json — tohle není záloha z téhle aplikace.") from None
    try:
        data = json.loads(surova)
    except ValueError:
        raise ChybaObnovy("Soubor data.json v záloze je poškozený a nedá se přečíst.") from None

    if not isinstance(data, dict):
        raise ChybaObnovy("Soubor data.json v záloze nemá očekávaný tvar.")
    if data.get("verze_zalohy") not in PODPOROVANE_VERZE_ZALOHY:
        raise ChybaObnovy(
            "Tahle záloha je v jiném/starším formátu, než umí obnovit tahle verze "
            "aplikace, a bezpečně se nedá obnovit. Obnov ji na verzi aplikace, "
            "kterou byla vytvořená."
        )
    try:
        return Zaloha.model_validate(data)
    except ValidationError as chyba:
        # Jen kde a jaka chyba - `input` (hodnota ze zalohy) se nikdy nelogu-
        # je, mohou to byt udaje deti.
        podrobnosti = "; ".join(
            f"{'.'.join(str(c) for c in e['loc']) or '(celek)'}: {e['msg']}"
            for e in chyba.errors(include_input=False, include_url=False)[:20]
        )
        log.warning(
            "Obnova ze zálohy: data.json neprošel kontrolou (%d chyb): %s",
            chyba.error_count(),
            podrobnosti,
        )
        raise ChybaObnovy(OBECNA_HLASKA_SCHEMATU) from None


class _Ciselniky:
    """Kategorie, lide a vozidla jsou sdileny ciselnik pro vsechny akce (na
    rozdil od zbytku zalohy) - hledaji se v nem podle jmena, a kdyz tam chybi
    (byly mezitim smazany nebo prejmenovany), zalozi se znovu (Honza,
    12. 9. 2026)."""

    def __init__(self, sezeni: Session, data: Zaloha) -> None:
        self._sezeni = sezeni
        self._kategorie: dict[str, Kategorie] = {}
        self._lide: dict[str, Clovek] = {}
        self._vozidla: dict[str, Vozidlo] = {}
        self._udaje_kategorii = {k.nazev: k for k in data.kategorie}
        self._udaje_lidi = {c.jmeno: c for c in data.lide}
        self._udaje_vozidel = {v.nazev: v for v in data.vozidla}

    def kategorie(self, nazev: str | None) -> Kategorie | None:
        if nazev is None:
            return None
        if nazev not in self._kategorie:
            kategorie = self._sezeni.scalar(select(Kategorie).where(Kategorie.nazev == nazev))
            if kategorie is None:
                udaje = self._udaje_kategorii.get(nazev)
                klic = zkrat_klic_kategorie(udaje.klic if udaje else None)
                # Pevna kategorie je kazda jedna jedina a nejde prejmenovat: zaloha
                # ji muze nest pod starym nazvem ("Jídlo", driv sla prejmenovat; po zavedeni
                # devíti pevnych i "Věcné dary" misto "věcné dary (...)"), ale podle klice
                # nebo podle dosavadniho nazvu je to ta sama. Pouzije se ta v databazi
                # a odkazy ze zalohy na ni miri; druha se nezaklada (klic je unikatni,
                # a v jine velikosti pismen by vznikly dve pevne).
                klic_pevne = (
                    klic.lower()
                    if klic and klic.lower() in PEVNE_KLICE_KATEGORII
                    else pevny_klic_podle_stareho_nazvu(nazev)
                )
                if klic_pevne:
                    pevna = self._sezeni.scalar(
                        select(Kategorie).where(func.lower(Kategorie.klic) == klic_pevne)
                    )
                    if pevna is not None:
                        self._kategorie[nazev] = pevna
                        return pevna
                # Zkraceny klic mohl splynout s klicem jine kategorie (je unikatni);
                # klic je jen oznaceni pro AI, bez nej se kategorie najde podle nazvu.
                if klic and uz_existuje(klic, self._sezeni.scalars(select(Kategorie.klic))):
                    klic = None
                kategorie = Kategorie(
                    nazev=nazev,
                    klic=klic,
                    poradi=udaje.poradi if udaje else 0,
                )
                self._sezeni.add(kategorie)
            self._kategorie[nazev] = kategorie
        return self._kategorie[nazev]

    def clovek(self, jmeno: str | None) -> Clovek | None:
        if jmeno is None:
            return None
        if jmeno not in self._lide:
            clovek = self._sezeni.scalar(select(Clovek).where(Clovek.jmeno == jmeno))
            if clovek is None:
                udaje = self._udaje_lidi.get(jmeno)
                clovek = Clovek(
                    jmeno=jmeno,
                    poradi=udaje.poradi if udaje else 0,
                    adresa=udaje.adresa if udaje else None,
                    kontakt=udaje.kontakt if udaje else None,
                )
                self._sezeni.add(clovek)
            self._lide[jmeno] = clovek
        return self._lide[jmeno]

    def existujici_clovek(self, text: str) -> Clovek | None:
        """Clovek, ktery uz v ciselniku je a jmenuje se jako `text` - bez zakladani
        noveho (starsi zaloha nese u prijmu jen volny text, ne udaje cloveka)."""
        return clovek_podle_jmena(self._sezeni.scalars(select(Clovek)), text)

    def vozidlo(self, nazev: str | None) -> Vozidlo | None:
        """Fotka technickeho prukazu se v zaloze akce nevozi - obnovene
        vozidlo bude bez ni a Honza ji nahraje znovu."""
        if nazev is None:
            return None
        if nazev not in self._vozidla:
            vozidlo = self._sezeni.scalar(select(Vozidlo).where(Vozidlo.nazev == nazev))
            if vozidlo is None:
                udaje = self._udaje_vozidel.get(nazev)
                vozidlo = Vozidlo(
                    nazev=nazev,
                    spz=udaje.spz if udaje else None,
                    spotreba=udaje.spotreba if udaje else None,
                    poradi=udaje.poradi if udaje else 0,
                )
                self._sezeni.add(vozidlo)
            self._vozidla[nazev] = vozidlo
        return self._vozidla[nazev]


# --- Skladani akce po tabulkach -------------------------------------------
#
# Funkce `_obnov_*` odpovidaji funkcim v zaloha.py. POZOR NA PORADI, ve kterem
# je `obnov()` vola - je vyznamove:
#   1. slozky pred skeny (sken odkazuje na slozku indexem),
#   2. skeny pred vsim ostatnim, co na ne odkazuje (cestaky, dary),
#      a uvnitr skenu ve dvou pruchodech: napred vsechny, az potom vazba
#      priloha -> uctenka (priloha muze ukazovat na sken, ktery jeste nebyl na rade),
#   3. ucastnici a cestaky pred radky knihy (radek na ne odkazuje indexem),
#   4. obdarovani pred dary.


def _obnov_akce(sezeni: Session, u: ZalohaAkce, ciselniky: _Ciselniky) -> Akce:
    nova_akce = Akce(
        nazev=u.nazev,
        datum_vyuctovani=u.datum_vyuctovani,
        datum_od=u.datum_od,
        datum_do=u.datum_do,
        stav=u.stav,
        prefix_dokladu=u.prefix_dokladu,
        pokladnik_clovek=ciselniky.clovek(u.pokladnik),
    )
    sezeni.add(nova_akce)
    return nova_akce


def _obnov_slozky(sezeni: Session, akce: Akce, data: Zaloha, ciselniky: _Ciselniky) -> list[Slozka]:
    slozky = [
        Slozka(
            akce=akce,
            nazev=u.nazev,
            kategorie=ciselniky.kategorie(u.kategorie),
            clovek=ciselniky.clovek(u.clovek),
            ucel_platby=u.ucel_platby,
            poradi=u.poradi,
        )
        for u in data.slozky
    ]
    sezeni.add_all(slozky)
    return slozky


class _ObnoveneSkeny(NamedTuple):
    skeny: list[Sken]
    soubory_k_zapisu: list[tuple[Sken, bytes]]
    chybejici_soubory: list[str]


def _obnov_skeny(
    sezeni: Session,
    akce: Akce,
    data: Zaloha,
    slozky: list[Slozka],
    ciselniky: _Ciselniky,
    archiv: zipfile.ZipFile,
) -> _ObnoveneSkeny:
    skeny: list[Sken] = []
    soubory_k_zapisu: list[tuple[Sken, bytes]] = []
    chybejici_soubory: list[str] = []
    for u in data.skeny:
        sken = Sken(
            akce=akce,
            slozka=slozky[u.slozka] if u.slozka is not None else None,
            soubor="",  # doplni se nize, az bude znamy novy nazev na disku
            puvodni_nazev=u.puvodni_nazev,
            pocet_stranek=u.pocet_stranek,
            nazev=u.nazev,
            druh=u.druh,
            obchodnik=u.obchodnik,
            datum=u.datum,
            castka=u.castka,
            cena_za_litr=u.cena_za_litr,
            clovek=ciselniky.clovek(u.clovek),
            kategorie=ciselniky.kategorie(u.kategorie),
            je_priloha=u.je_priloha,
            zkontrolovano=u.zkontrolovano,
            varovani_ztiseno=u.varovani_ztiseno,
            jistota=u.jistota,
            poznamka=u.poznamka,
            rozpis_json=u.rozpis_json,
            poradi=u.poradi,
            poradi_v_knize=u.poradi_v_knize,
        )
        for u_cast in u.casti:
            sezeni.add(
                CastSkenu(
                    sken=sken,
                    kategorie=ciselniky.kategorie(u_cast.kategorie),
                    castka=u_cast.castka,
                    popis=u_cast.popis,
                    poradi=u_cast.poradi,
                )
            )

        pripona = Path(u.puvodni_nazev).suffix.lower()
        obsah_souboru = None
        if u.soubor_v_zipu and pripona in POVOLENE_PRIPONY:
            try:
                obsah_souboru = archiv.read(u.soubor_v_zipu)
            except KeyError:
                obsah_souboru = None
        # Obsah se overuje stejne jako pri nahrani: HTML pod priponou .pdf
        # nebo cizi format pod .jpg se na disk nedostane a bere se jako chybejici.
        if obsah_souboru is not None:
            try:
                over_obsah(obsah_souboru, pripona)
            except ChybaObsahu:
                obsah_souboru = None
        # Pripona mimo povoleny seznam se resi stejne jako soubor,
        # ktery v zaloze uz chybel - skutecna zaloha z appky jinou priponu
        # nikdy nema, tak radeji ohlasit "chybi", nez na disk pustit neco,
        # co nahravani nikdy nepusti.
        if obsah_souboru is None:
            chybejici_soubory.append(u.puvodni_nazev or "?")
        else:
            sken.soubor = f"{uuid.uuid4().hex}{pripona}"
            soubory_k_zapisu.append((sken, obsah_souboru))

        skeny.append(sken)
        sezeni.add(sken)

    for u, sken in zip(data.skeny, skeny, strict=True):
        if u.priloha_k is not None:
            sken.priloha_k = skeny[u.priloha_k]
    return _ObnoveneSkeny(skeny, soubory_k_zapisu, chybejici_soubory)


def _obnov_prijmy(sezeni: Session, akce: Akce, data: Zaloha, ciselniky: _Ciselniky) -> None:
    for u in data.prijmy:
        clovek = ciselniky.clovek(u.clovek)
        kdo = u.kdo
        if clovek is None and kdo is not None:
            # Starsi zaloha: volny text se priradi cloveku, ktery uz v ciselniku je
            # (stejne pravidlo jako v migraci). Nikoho noveho kvuli tomu nezaklada.
            clovek = ciselniky.existujici_clovek(kdo)
        if clovek is not None:
            kdo = None
        sezeni.add(
            Prijem(
                akce=akce,
                popis=u.popis,
                castka=u.castka,
                clovek=clovek,
                kdo=kdo,
                datum=u.datum,
                poradi=u.poradi,
                poradi_v_knize=u.poradi_v_knize,
            )
        )


def _obnov_ucastniky(sezeni: Session, akce: Akce, data: Zaloha) -> list[Ucastnik]:
    ucastnici = [
        Ucastnik(
            akce=akce,
            poradi=u.poradi,
            jmeno=u.jmeno,
            prijmeni=u.prijmeni,
            prezdivka=u.prezdivka,
            ulice=u.ulice,
            mesto=u.mesto,
            psc=u.psc,
            datum_narozeni=u.datum_narozeni,
            jednotka=u.jednotka,
            osobodny=u.osobodny,
            detodny=u.detodny,
            dochazka_json=u.dochazka_json,
            hotove=u.hotove,
            hotove_doklad=u.hotove_doklad,
            prevodem=u.prevodem,
            prevodem_doklad=u.prevodem_doklad,
            vouchery=u.vouchery,
            vouchery_doklad=u.vouchery_doklad,
            vratka_doklad=u.vratka_doklad,
        )
        for u in data.ucastnici
    ]
    sezeni.add_all(ucastnici)
    return ucastnici


def _obnov_cestaky(
    sezeni: Session,
    akce: Akce,
    data: Zaloha,
    skeny: list[Sken],
    slozky: list[Slozka],
    ciselniky: _Ciselniky,
) -> list[Cestak]:
    cestaky = []
    for u in data.cestaky:
        cestak = Cestak(
            akce=akce,
            ucel_cesty=u.ucel_cesty,
            misto=u.misto,
            spolucestujici=u.spolucestujici,
            poznamka=u.poznamka,
            jednotka=u.jednotka,
            cestujici=ciselniky.clovek(u.cestujici),
            # Adresa a kontakt se berou ze zalohy cestaku, ne z
            # ciselniku - je to otisk, ktery sel na papir.
            adresa=u.adresa,
            kontakt=u.kontakt,
            vozidlo=ciselniky.vozidlo(u.vozidlo),
            spotreba=u.spotreba,
            palivova_uctenka=(
                skeny[u.palivova_uctenka] if u.palivova_uctenka is not None else None
            ),
            puvodni_slozka_palivovky=(
                slozky[u.puvodni_slozka_palivovky]
                if u.puvodni_slozka_palivovky is not None
                else None
            ),
            cena_za_litr=u.cena_za_litr,
            opotrebeni=u.opotrebeni,
            poradi=u.poradi,
        )
        sezeni.add(cestak)
        cestaky.append(cestak)
        for j in u.jizdy:
            sezeni.add(
                Jizda(
                    cestak=cestak,
                    datum=j.datum,
                    odkud=j.odkud,
                    kam=j.kam,
                    prostredek=j.prostredek,
                    km=j.km,
                    jizdne=j.jizdne,
                    z_lat=j.z_lat,
                    z_lon=j.z_lon,
                    do_lat=j.do_lat,
                    do_lon=j.do_lon,
                    poradi=j.poradi,
                )
            )
    return cestaky


def _obnov_pokladni_radky(
    sezeni: Session,
    akce: Akce,
    data: Zaloha,
    ucastnici: list[Ucastnik],
    cestaky: list[Cestak],
) -> None:
    for u in data.pokladni_radky:
        sezeni.add(
            PokladniRadek(
                akce=akce,
                datum=u.datum,
                ucel=u.ucel,
                smer=u.smer,
                castka=u.castka,
                poradi=u.poradi,
                poradi_v_knize=u.poradi_v_knize,
                ucastnik=(ucastnici[u.ucastnik] if u.ucastnik is not None else None),
                cestak=(cestaky[u.cestak] if u.cestak is not None else None),
            )
        )


def _obnov_obdarovane(sezeni: Session, akce: Akce, data: Zaloha) -> list[Obdarovany]:
    obdarovani = [
        Obdarovany(
            akce=akce,
            poradi=o.poradi,
            jmeno=o.jmeno,
            prijmeni=o.prijmeni,
            prezdivka=o.prezdivka,
            ulice=o.ulice,
            mesto=o.mesto,
            psc=o.psc,
            datum_narozeni=o.datum_narozeni,
        )
        for o in data.obdarovani
    ]
    sezeni.add_all(obdarovani)
    return obdarovani


def _obnov_dary(
    sezeni: Session,
    akce: Akce,
    data: Zaloha,
    skeny: list[Sken],
    obdarovani: list[Obdarovany],
) -> None:
    for u in data.dary:
        sezeni.add(
            Dar(
                akce=akce,
                nazev=u.nazev,
                poradi=u.poradi,
                sken=skeny[u.sken] if u.sken is not None else None,
                obdarovany=(obdarovani[u.obdarovany] if u.obdarovany is not None else None),
            )
        )


def obnov(sezeni: Session, obsah_zipu: bytes, slozka_uploadu: Path) -> VysledekObnovy:
    """Slozi novou Akci ze zalohy a prida ji (i se vsemi potomky) do `sezeni`.

    Neuklada - `sezeni.commit()` je na volajicim (`app/routes/obnova.py`), stejne
    jako uklizeni `zapsane_soubory` z disku, kdyby commit prece jen selhal.
    """
    try:
        with _otevri_zip(obsah_zipu) as archiv:
            data = _precti_data(archiv)
            ciselniky = _Ciselniky(sezeni, data)

            nova_akce = _obnov_akce(sezeni, data.akce, ciselniky)
            slozky = _obnov_slozky(sezeni, nova_akce, data, ciselniky)
            skeny, soubory_k_zapisu, chybejici_soubory = _obnov_skeny(
                sezeni, nova_akce, data, slozky, ciselniky, archiv
            )
            _obnov_prijmy(sezeni, nova_akce, data, ciselniky)
            ucastnici = _obnov_ucastniky(sezeni, nova_akce, data)
            cestaky = _obnov_cestaky(sezeni, nova_akce, data, skeny, slozky, ciselniky)
            _obnov_pokladni_radky(sezeni, nova_akce, data, ucastnici, cestaky)
            obdarovani = _obnov_obdarovane(sezeni, nova_akce, data)
            _obnov_dary(sezeni, nova_akce, data, skeny, obdarovani)

            # Teprve tady, kdyz uz je jasne, ze se cela zaloha v poradku
            # poskladala, se soubory skenu doopravdy zapisou na disk.
            zapsane: list[str] = []
            try:
                for sken, obsah_souboru in soubory_k_zapisu:
                    (slozka_uploadu / sken.soubor).write_bytes(obsah_souboru)
                    zapsane.append(sken.soubor)
            except OSError:
                # Zapis n-teho souboru selhal (napr. plny disk) - predchozi uz
                # zapsane soubory se musi uklidit rucne, databaze jeste vubec
                # necommitla. `except Exception` v obnova.py pak jen
                # zaloguje a ukaze obecnou hlasku, disk uz je cisty.
                smaz_soubory(zapsane, slozka_uploadu)
                raise
    except (KeyError, IndexError, TypeError, ValueError) as chyba:
        # Po kontrole schematem by sem uz data.json nemel dojit. Kdyby ano,
        # uzivatel dostane obecnou hlasku; text vyjimky muze obsahovat hodnoty
        # ze zalohy, tak se loguje jen jeho typ.
        log.warning("Obnova ze zálohy: neočekávaný tvar dat (%s)", type(chyba).__name__)
        raise ChybaObnovy(OBECNA_HLASKA_SCHEMATU) from None

    return VysledekObnovy(nova_akce, zapsane, chybejici_soubory)
