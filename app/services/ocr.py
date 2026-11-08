"""Cteni skenu uctenek pres Gemini.

Sken je jeden nahrany soubor (PDF nebo obrazek), ktery muze mit vic stranek.
Do modelu jde jako sada obrazku a zpatky se vraci strukturovany vysledek,
ne volny text - diky tomu jde rovnou ulozit do databaze.

Rozlisujeme tri ruzne tezke pripady, :
  a) bezna uctenka  - castka je na skenu vytistena
  b) arch jizdenek  - castka nikde neni, vznika spoctenim kusu
  c) palivova uctenka - podstatna je cena za litr, ne celkova castka
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from google import genai
from google.genai import types
from pydantic import AfterValidator, BaseModel, Field, ValidationError, model_validator

from app.config import nastaveni
from app.services import obrazky

# Casovy limit jednoho volani Gemini (ms). Bez nej klient ceka donekonecna
# a protoze routy cteni bezi v hlavni smycce, stala by pri vypadku Googlu
# cela appka. Cteni jedne uctenky trva 5-15 s.
CASOVY_LIMIT_MS = 90_000
# Strop vystupu vcetne "premysleni" modelu. Nejvetsi skutecne cteni mela
# ~750 vystupnich + ~4 000 "premyslecich" tokenu; tohle je zhruba trojnasobek.
# Zacykleny model by se jinak platil az do sveho maxima.
MAX_VYSTUPNICH_TOKENU = 16384

# Meze odpovedi. Zamerne se NEPISOU jako Field(max_length=...): ty by se dostaly
# do schematu, ktere jde do Gemini (nelze overit bez zivého volani, jestli je
# prijme), a hlavne by odmitly uz zaplacenou odpoved. Misto toho se prilis
# dlouhe texty a seznamy po precteni jen orizne.
MAX_DELKA_TEXTU = 200
MAX_RADKU_ROZPISU = 50
MAX_KATEGORII_V_ROZDELENI = 50
MAX_VECI_NA_UCTENCE = 100


# Rozumna castka z AI (Kc). Co je mimo, model nejspis necetl spravne (obrovske
# cislo z textu na fotce, zaporna hodnota). Zahazuje se az po precteni, ne
# pres Field(ge=, le=): ty by sly do schematu pro Gemini a odmitly by uz
# zaplacenou odpoved.
MAX_CASTKA_AI = 10_000_000
POZNAMKA_NESMYSL = "Část údajů z AI byla nesmyslná, appka ji zahodila."


def _rozumna(castka: float | None) -> bool:
    return castka is None or (math.isfinite(castka) and 0 <= castka <= MAX_CASTKA_AI)


def _kratky(text: str | None) -> str | None:
    return text[:MAX_DELKA_TEXTU] if isinstance(text, str) else text


def _nejvyse(pocet: int):
    def orizni(seznam: list) -> list:
        return seznam[:pocet]

    return AfterValidator(orizni)


Text = Annotated[str, AfterValidator(_kratky)]
TextNeboNic = Annotated[str | None, AfterValidator(_kratky)]


class PolozkaRozpisu(BaseModel):
    """Jeden radek rozpisu u archu nalepenych jizdenek.

    Bez rozpisu by Honza musel prepocitavat cely arch rucne - takhle si
    jen overi, ze na strane je opravdu deset jizdenek po 32 Kc.
    """

    strana: int = Field(description="Cislo stranky skenu, od 1")
    popis: Text = Field(description="Co je na strane, napr. 'jizdenka IDS JMK 32 Kc'")
    pocet_kusu: int
    cena_za_kus: float
    mezisoucet: float


class CastUctenky(BaseModel):
    """Kolik z uctenky patri ktere kategorii.

    Vyplnuje se jen u smisenych uctenek - typicky nakup jidla, na kterem je
    i toaletni papir nebo sampon. U ciste uctenky zustava seznam prazdny.
    """

    kategorie: Text = Field(description="Oznaceni kategorie ze seznamu v pokynech")
    castka: float = Field(description="Soucet za veci teto kategorie v Kc")
    co: TextNeboNic = Field(default=None, description="Cesky, kratce: 'toaletní papír, šampon'")


class VysledekCteni(BaseModel):
    """Co model precte z jednoho skenu."""

    druh: Literal["uctenka", "arch_jizdenek", "palivo", "cestovni_prikaz", "jine"]
    obchodnik: TextNeboNic = Field(default=None, description="Nazev obchodu nebo dopravce")
    datum: str | None = Field(default=None, description="Datum na dokladu ve tvaru RRRR-MM-DD")
    castka_celkem: float | None = Field(default=None, description="Celkova castka v Kc")
    mena: str = Field(default="CZK")
    cena_za_litr: float | None = Field(
        default=None, description="Jen u paliva: cena za litr v Kc, napr. 30.90"
    )
    navrh_kategorie: TextNeboNic = Field(
        default=None, description="Oznaceni kategorie ze seznamu v pokynech"
    )
    navrh_nazvu: TextNeboNic = Field(
        default=None, description="Nazev souboru, napr. 'Albert 758,-'"
    )
    rozpis: Annotated[list[PolozkaRozpisu], _nejvyse(MAX_RADKU_ROZPISU)] = Field(
        default_factory=list, description="Vyplnit jen u archu jizdenek"
    )
    rozdeleni: Annotated[list[CastUctenky], _nejvyse(MAX_KATEGORII_V_ROZDELENI)] = Field(
        default_factory=list,
        description="Vyplnit jen kdyz jsou na uctence veci z vice nez jedne kategorie",
    )
    jistota: Literal["vysoka", "stredni", "nizka"]
    poznamka: TextNeboNic = Field(
        default=None, description="Cesky, jednou vetou: co bylo necitelne nebo sporne"
    )

    @model_validator(mode="after")
    def _zahod_nesmysly(self) -> VysledekCteni:
        """Castky mimo meze zahodi (rozdeleni a rozpis jako celek) a sniz jistotu,
        aby je clovek zkontroloval. Odpoved se kvuli tomu neodmita."""
        zahozeno = False
        if not _rozumna(self.castka_celkem):
            self.castka_celkem, zahozeno = None, True
        if not _rozumna(self.cena_za_litr):
            self.cena_za_litr, zahozeno = None, True
        if not all(_rozumna(cast.castka) for cast in self.rozdeleni):
            self.rozdeleni, zahozeno = [], True
        if not all(_rozumna(p.cena_za_kus) and _rozumna(p.mezisoucet) for p in self.rozpis):
            self.rozpis, zahozeno = [], True
        if zahozeno:
            self.jistota = "nizka"
            self.poznamka = _kratky(
                f"{POZNAMKA_NESMYSL} {self.poznamka}" if self.poznamka else POZNAMKA_NESMYSL
            )
        return self


class VecZUctenky(BaseModel):
    """Jedna vec koupena na uctence za vecne dary."""

    nazev: Text = Field(description="Jak se veci rika lidsky, napr. 'modry fotbalovy mic'")
    pocet: int = Field(default=1, description="Kolik kusu te veci na uctence je")


class VysledekDaru(BaseModel):
    """Co se na uctence koupilo. Kazda vec se pak nekomu rozda."""

    veci: Annotated[list[VecZUctenky], _nejvyse(MAX_VECI_NA_UCTENCE)] = Field(default_factory=list)


SABLONA_POKYNU = """\
Ctes sken dokladu ze skautske akce. Vsechny castky jsou v ceskych korunach.

Rozhodni, o ktery druh jde:

1. "uctenka" - bezny paragon z obchodu. Vezmi CELKOVOU castku (radek Celkem),
   ne mezisoucty a ne jednotlive polozky.

2. "arch_jizdenek" - na strankach je nalepeno vic stejnych jizdenek nebo listku.
   Castka nikde napsana neni! Musis spocitat kusy na kazde strance zvlast,
   vyplnit pole "rozpis" (jeden zaznam na stranku) a castka_celkem je soucet
   vsech mezisouctu. Pocitej peclive - radeji stranku projdi po radcich.

3. "palivo" - uctenka za pohonne hmoty. Krome celkove castky OBAVAZNE vypln
   cena_za_litr (napr. u "16.18 l  30.90 CZK/l  500.00" je cena_za_litr 30.90).

4. "cestovni_prikaz" - vyplneny formular cestovniho prikazu.

5. "jine" - nic z toho.

DATUM
Vracej ve tvaru RRRR-MM-DD. Doklad je cesky, takze poradi je den, mesic, rok -
i kdyz je datum bez tecek. Sestimistne "230126" tedy znamena 23. 1. 2026,
NE 26. 1. 2023. Podobne "010724" je 1. 7. 2024.
Pozor na jizdenkach: "Tarif platny k ..." neni datum nakupu, to je platnost tarifu.
Kdyz datum nakupu nenajdes nebo si nejsi jisty, nech pole prazdne a dej jistotu "nizka".

NAZEV SOUBORU (navrh_nazvu)
- cele koruny: "<Obchodnik> <castka>,-"   napr. "Albert 758,-"
- s haleri:     "<Obchodnik> <castka>"     napr. "Lidl 905,50"   (bez ",-")
- arch jizdenek: "Jizdne <pocet>x<cena>=<celkem>"  napr. "Jizdne 28x32=896"

ROZDELENI PODLE KATEGORII
Kdyz jsou na uctence veci z VICE kategorii - typicky nakup jidla, na kterem je
i toaletni papir, sampon nebo houbicky - vypln pole "rozdeleni": jeden zaznam
na kategorii, s castkou za vsechny veci te kategorie dohromady.
- Kdyz je cela uctenka jedna kategorie, nech "rozdeleni" PRAZDNE. Nevypisuj
  jeden jediny zaznam - to je zbytecne.
- Soucet castek v "rozdeleni" se MUSI rovnat castka_celkem. Slevy, zaokrouhleni
  a vratne obaly rozpocitej do kategorie, ke ktere patri; kdyz to nejde poznat,
  pridej je k te s nejvetsi castkou.
- Do "co" napis kratce cesky, co do te kategorie na uctence spadlo.

DALSI
- navrh_kategorie zvol z tohoto seznamu (vracej to, co je pred rovnitkem):
{kategorie}
- kategorie v "rozdeleni" vol z tehoz seznamu
- ZADNOU jinou kategorii nevymysli. Kdyz se uctenka nehodi do zadne,
  nech navrh_kategorie prazdne
- kdyz uctenku delis, nastav navrh_kategorie na tu s NEJVETSI castkou
- jistota "nizka" dej vzdy, kdyz jsi neco jen odhadoval nebo bylo neco necitelne
- poznamku pis jen kdyz je opravdu co rict

JAZYK
Vsechny texty pro cloveka - obchodnik, navrh_nazvu, popis v rozpisu, poznamka -
pis cesky VCETNE DIAKRITIKY: "Jizdne" je spatne, spravne je "Jízdné".
Nazev obchodu ale opis presne tak, jak je na dokladu.
"""


POKYNY_DARY = """\
Ctes uctenku za veci, ktere se na skautske akci rozdaly jako ceny nebo dary.
Vypis vsechno zbozi, ktere je na ni koupene.

NAZEV VECI
Piš, co to je, tak jak by tu věc pojmenoval člověk: "modrý fotbalový míč",
ne "MIC KOPACI SELECT BLU5 EAN 8590...". Vynech kódy, zkratky prodejce, čísla
šarží, hmotnosti balení a cenu. Barvu, velikost nebo druh nech, když jsou
z účtenky poznat — podle nich se dvě jinak stejné věci od sebe rozeznají.

POCET
Do "pocet" napiš, kolik kusů té věci na účtence je: "3x míč" je jedna položka
s počtem 3, ne tři položky.

CO NEVYPISOVAT
Slevy, zaokrouhlení, poplatek za tašku, vratné obaly, dopravné, součty, DPH
ani platební řádky. Jen věci, které se dají někomu dát do ruky.

JAZYK
Česky včetně diakritiky, malým písmenem na začátku: "modrý fotbalový míč".
"""


@dataclass
class PouzitiTokenu:
    """Kolik tokenu stalo jedno cteni - pro odhad utraty za placeny klic
    (viz app/services/tarif.py:odhad_czk). Cena se tu nepocita, jen tokeny -
    cenik se muze zmenit a historie ma prepocitat spravne."""

    model: str
    tokeny_vstup: int
    tokeny_vystup: int
    tokeny_mysleni: int


class ChybaOdpovediAI(Exception):
    """Gemini odpovedela, ale pouzitelnou odpoved nevratila (nic, oriznutou
    nebo nevalidni). Tokeny se i tak zaplatily, proto je chyba nese - volajici
    je zapise do utraty. `str(chyba)` je cesky text pro uzivatele."""

    def __init__(self, zprava: str, pouziti: PouzitiTokenu):
        super().__init__(zprava)
        self.pouziti = pouziti


def _zeptej_se(
    cesta: Path,
    pokyny: str,
    schema: type[BaseModel],
    api_klic: str,
) -> tuple[str, PouzitiTokenu]:
    """Posle sken do Gemini s danymi pokyny a vrati odpoved jako JSON text
    spolu s poctem spotrebovanych tokenu.

    Spolecna cast obou cteni - beznych udaju uctenky i veci na ni. Lisi se
    jen pokyny a tvar odpovedi. Kdyz Gemini nevrati pouzitelny text, vyhodi
    `ChybaOdpovediAI` s uz zjistenymi tokeny.
    """
    jpegy = obrazky.obrazky_ze_souboru(cesta)
    klient = genai.Client(api_key=api_klic, http_options=types.HttpOptions(timeout=CASOVY_LIMIT_MS))

    obsah: list[types.ContentUnionDict] = [pokyny]
    for jpeg in jpegy:
        obsah.append(types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"))

    odpoved = klient.models.generate_content(
        model=nastaveni.gemini_model,
        contents=obsah,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            max_output_tokens=MAX_VYSTUPNICH_TOKENU,
            # Nepouzivame volani funkci; bez tohohle SDK vypisuje varovani.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )
    metadata = odpoved.usage_metadata
    pouziti = PouzitiTokenu(
        model=nastaveni.gemini_model,
        tokeny_vstup=getattr(metadata, "prompt_token_count", None) or 0,
        tokeny_vystup=getattr(metadata, "candidates_token_count", None) or 0,
        tokeny_mysleni=getattr(metadata, "thoughts_token_count", None) or 0,
    )

    konec = odpoved.candidates[0].finish_reason if odpoved.candidates else None
    if konec == types.FinishReason.MAX_TOKENS:
        raise ChybaOdpovediAI("Odpověď AI byla příliš dlouhá, tak ji appka přerušila.", pouziti)
    if not odpoved.text:
        # Gemini odpoved zablokovala nebo nevratila nic.
        raise ChybaOdpovediAI("AI nevrátila žádný text.", pouziti)
    return odpoved.text, pouziti


def _over_json[T: BaseModel](schema: type[T], text: str, pouziti: PouzitiTokenu) -> T:
    try:
        return schema.model_validate_json(text)
    except ValidationError as chyba:
        raise ChybaOdpovediAI("AI vrátila odpověď, které appka nerozumí.", pouziti) from chyba


def pokyny_s_kategoriemi(kategorie: list[tuple[str, str]]) -> str:
    """Doplni do pokynu seznam kategorii z ciselniku.

    Kategorie se nesmi psat natvrdo do kodu - Honza si je v aplikaci meni
    a AI musi vybirat z tech, ktere ma opravdu zalozene. Kdyz je ciselnik
    prazdny, kategorii proste nenavrhujeme.
    """
    if not kategorie:
        return SABLONA_POKYNU.format(kategorie="  (zadne - nechej navrh_kategorie prazdne)")
    seznam = "\n".join(f"  {oznaceni} = {nazev}" for oznaceni, nazev in kategorie)
    return SABLONA_POKYNU.format(kategorie=seznam)


def precti_sken(
    cesta: Path,
    *,
    api_klic: str,
    kategorie: list[tuple[str, str]] | None = None,
) -> tuple[VysledekCteni, PouzitiTokenu]:
    """Precte jeden sken a vrati strukturovany vysledek spolu s poctem tokenu.

    `api_klic` vybira, kterym klicem se vola - bezplatnym, nebo placenym
    (viz `app/services/tarif.py`). Pozor na limity bezplatneho tarifu Google
    AI Studio: volej postupne, ne paralelne.

    `kategorie` je seznam (oznaceni, nazev) z ciselniku - AI vybira jen z nej.
    """
    text, pouziti = _zeptej_se(
        cesta, pokyny_s_kategoriemi(kategorie or []), VysledekCteni, api_klic
    )
    return _over_json(VysledekCteni, text, pouziti), pouziti


def precti_dary(cesta: Path, *, api_klic: str) -> tuple[VysledekDaru, PouzitiTokenu]:
    """Vypise veci koupene na uctence za vecne dary, spolu s poctem tokenu.

    Druhe, samostatne cteni tehoz skenu - uctenka uz jednou prectena byla,
    ale kvuli castce, ne kvuli polozkam. Zamerne se to nespojuje do jednoho
    dotazu: rozepisovat po polozkach uctenku za jidlo nebo jizdne nema smysl
    a platilo by se za to u kazdeho skenu.
    """
    text, pouziti = _zeptej_se(cesta, POKYNY_DARY, VysledekDaru, api_klic)
    return _over_json(VysledekDaru, text, pouziti), pouziti
