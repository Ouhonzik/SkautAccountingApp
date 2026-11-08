"""Pomocnici, ktere pouzivaji vsechny routy."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping

from fastapi import HTTPException
from sqlalchemy.orm import Session
from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import Response

from app.db import Databaze  # noqa: F401 - routy ho berou odtud
from app.models import STAV_UZAVRENA, Akce, pripustne_narozeni
from app.sablony import adresa_odkud_prisel, oznam, zpet

# Nejvetsi cele cislo, ktere SQLite v INTEGER s ID rozumne vezme.
MAX_ID = 2_147_483_647


def nacti_akci(sezeni: Session, akce_id: int) -> Akce:
    akce = sezeni.get(Akce, akce_id)
    if akce is None:
        raise HTTPException(status_code=404, detail="Taková akce neexistuje.")
    return akce


HLASKA_AKCE_UZAVRENA = "Akce je uzavřená — nejdřív ji znovu otevři."


class AkceUzavrena(HTTPException):
    """Zapis do uzavrene akce. Podtrida `HTTPException`, aby ji nic nepustilo
    jako 500, ale `main.akce_je_uzavrena` z ni udela hlasku na strance:
    kdyz se vyjimka necha obsluhovat obecne, clovek skonci na samostatne chybove
    strance, nebo (prubezne ukladani) dostane JSON misto vety."""

    def __init__(self) -> None:
        super().__init__(status_code=409, detail=HLASKA_AKCE_UZAVRENA)


def over_otevrenou(akce: Akce) -> None:
    """Uzavrena akce je jen pro cteni: zapis do ni skonci vyjimkou `AkceUzavrena`
    (stav 409; obycejny formular dostane hlasku a navrat, prubezne ukladani text s vetou).

    Vola se v kazde zapisove route hned po nacteni akce (nebo zaznamu, ktery
    jí patri - `over_otevrenou(sken.akce)`). Vyjimka je jen znovuotevreni
    (`/akce/{id}/stav`); tiskne a stahuje se dal, protoze to jsou GET.
    Test automaticky test hlida, ze zadna routa nezustane bez kontroly.
    """
    if akce.stav == STAV_UZAVRENA:
        raise AkceUzavrena


def nacti_otevrenou_akci(sezeni: Session, akce_id: int) -> Akce:
    """`nacti_akci` pro zapisove routy: uzavrena akce skonci chybou 409."""
    akce = nacti_akci(sezeni, akce_id)
    over_otevrenou(akce)
    return akce


# Hlavicka, kterou posila js/prubezne.js pri prubeznem ukladani. Podle ni routa pozna,
# ze nema presmerovavat ani hlasit "Ulozeno" - stranka se totiz neprekresluje.
HLAVICKA_PRUBEZNE = "x-prubezne-ulozeni"


def hlaska_necitelnych(pocet: int) -> str:
    """Veta pro uzivatele, kdyz se v tabulce nepodarilo precist `pocet` poli."""
    pole = "pole" if pocet == 1 else "polí"
    return f"U {pocet} {pole} jsem nepřečetl číslo, nechal jsem původní hodnotu."


def hlaska_dat_narozeni(pocet: int) -> str:
    """Veta pro uzivatele, kdyz `pocet` dat narozeni nebylo pripustnych."""
    data = "datum" if pocet == 1 else "dat"
    return (
        f"U {pocet} {data} narození jsem nechal původní hodnotu — datum nemůže být "
        "po začátku akce ani před rokem 1900."
    )


def je_prubezne(request: Request) -> bool:
    """Prichazi pozadavek z prubezneho ukladani (fetch / sendBeacon)?"""
    # `sendBeacon` pri odchodu ze stranky neumi poslat vlastni hlavicku,
    # proto se stejny priznak prijima i z adresy.
    return bool(request.headers.get(HLAVICKA_PRUBEZNE) or request.query_params.get("prubezne"))


# Veta pro bublinu, kdyz prihlaseni mezitim skoncilo. Formular na strance zustava,
# takze si clovek v nove zalozce prihlasi a zmeny posle znovu.
HLASKA_PRIHLASENI_VYPRSELO = "Přihlášení vypršelo — přihlas se v nové záložce a pak to zkus znovu."


def odpoved_401(zprava: str = HLASKA_PRIHLASENI_VYPRSELO) -> Response:
    """Odpoved pro prubezne ukladani, kdyz clovek neni (uz) prihlaseny.

    Misto presmerovani na prihlaseni: `fetch` by ho nasledoval, dostal by
    prihlasovaci stranku se stavem 200 a bublina by hlasila "Ulozeno".
    """
    return Response(zprava, status_code=401, media_type="text/plain; charset=utf-8")


def prubezna_chyba(request: Request, zprava: str, status: int = 409) -> Response | None:
    """Odpoved se stavem a vetou pro prubezne ukladani, jinak None.

    Pro pripady, kdy se obycejny formular vrati na stranku s hlaskou: stranka
    se pri prubeznem ukladani neprekresluje, takze hlaska z `oznam()` by vyskocila
    az jinde a bublina by mezitim rekla "Ulozeno".
    """
    if je_prubezne(request):
        return Response(zprava, status_code=status, media_type="text/plain; charset=utf-8")
    return None


def zaskrtnuto(pole: Mapping[str, str], jmeno: str, dosavadni: bool, prubezne: bool) -> bool:
    """Zaskrtavatko z radku tabulky.

    Obycejne odeslani (bez JavaScriptu) posle cely formular a nezaskrtnute pole
    vynecha, takze chybi = nezaskrtnuto. Prubezne ukladani posila jen zmenena
    pole: chybi = beze zmeny, zaskrtnuto prijde jako "on" a odskrtnuto jako
    prazdna hodnota.
    """
    if jmeno in pole:
        return bool(pole[jmeno])
    return dosavadni if prubezne else False


def prubezne_ulozeni(
    request: Request, necitelnych: int = 0, nepripustnych_dat: int = 0
) -> Response | None:
    """Vrati odpoved pro prubezne ulozeni, nebo None kdyz slo o normalni odeslani.

    Kdyz se cast poli nepodarilo precist (`necitelnych`), ostatni pole jsou
    uz ulozena, ale odpoved je 422 s vetou pro uzivatele - js/prubezne.js z ni ukaze
    chybovou bublinu misto "Ulozeno".

    Pouziti v route:  `if (hotovo := prubezne_ulozeni(request)): return hotovo`
    """
    if je_prubezne(request):
        if necitelnych or nepripustnych_dat:
            hlasky = []
            if necitelnych:
                hlasky.append(hlaska_necitelnych(necitelnych))
            if nepripustnych_dat:
                hlasky.append(hlaska_dat_narozeni(nepripustnych_dat))
            return Response(
                " ".join(hlasky),
                status_code=422,
                media_type="text/plain; charset=utf-8",
            )
        return Response(status_code=204)
    return None


# Nejvic poli a nejdelsi pole ve formulari s tabulkou. Starlette by jinak
# vzal 1 MB na pole, a to je u textu, ktery se uklada do databaze i do logu,
# zbytecne moc. Ucastnici maji vic poli kvuli dochazce (az 62 dni
# u kazdeho z 1000 lidi), proto si o vyssi mez rikaji sami.
MAX_POLI = 5_000
MAX_POLI_UCASTNICI = 100_000
MAX_DELKA_POLE = 64 * 1024


async def nacti_formular(request: Request, max_poli: int = MAX_POLI) -> FormData:
    """`await request.form()` s mezemi. Pri prekroceni Starlette odpovi 400."""
    return await request.form(max_fields=max_poli, max_part_size=MAX_DELKA_POLE)


async def kam_dal(request: Request, vychozi: str) -> str:
    """Kam se vratit po odeslani formulare.

    Uctenku i prijem jde smazat ze dvou mist - ze svoji obrazovky a z pokladni
    knihy. Formular posle v poli `kam`, kam clovek patri zpatky; bez nej se jde
    na vychozi adresu. Prijima se jen adresa uvnitr aplikace, aby z toho neslo
    udelat odkaz nekam ven.
    """
    formular = await nacti_formular(request)
    kam = formular.get("kam")
    if isinstance(kam, str) and kam.startswith("/") and not kam.startswith("//"):
        return kam
    return vychozi


def adresa_zpet(request: Request) -> str:
    """Stranka, odkud formular prisel (hlavicka Referer), jinak uvodni stranka.

    Pouzije se po zamitnutem formulari. Prijima se jen adresa na stejnem
    serveru, aby z toho neslo udelat presmerovani nekam ven.
    """
    return adresa_odkud_prisel(request) or "/"


# Veta pro druhy klik na krizek (nebo zaznam, ktery mezitim smazal nekdo jiny).
HLASKA_UZ_SMAZANO = "Tohle už je smazané."


def uz_smazano(request: Request) -> Response:
    """Zaznam z adresy uz neexistuje: hlaska a navrat na stranku, odkud
    clovek prisel - ne tichy skok na seznam akci."""
    oznam(request, HLASKA_UZ_SMAZANO, "pozor")
    return zpet(adresa_zpet(request))


async def uz_smazano_kam(request: Request) -> Response:
    """Jako `uz_smazano` pro routy, jejichz formular posila pole `kam`."""
    oznam(request, HLASKA_UZ_SMAZANO, "pozor")
    return zpet(await kam_dal(request, adresa_zpet(request)))


def na_datum(text: str | None) -> dt.date | None:
    """Prevede hodnotu z HTML pole typu date. Prazdne pole znamena 'nevyplneno'."""
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def datum_narozeni_z_formulare(
    text: str | None, stavajici: dt.date | None, nejpozdeji: dt.date
) -> tuple[dt.date | None, bool]:
    """Datum narozeni z pole formulare: (hodnota k ulozeni, bylo pripustne).

    Prazdne pole datum maze. Nepripustne datum (nesmysl, po `nejpozdeji`, pred
    rokem 1900) se neulozi - zustane `stavajici` a druhy clen je False, aby
    volajici ukazal hlasku. Nezmenena stara hodnota hlasku nevyvola.
    """
    if not text:
        return None, True
    try:
        datum = dt.date.fromisoformat(text)
    except ValueError:
        return stavajici, False
    if datum == stavajici or pripustne_narozeni(datum, nejpozdeji):
        return datum, True
    return stavajici, False


def bez_koncovek(text: str) -> str:
    """Zapis cisla bez mezer a bez koncovek '420,-', '420,–' a '500 Kč'."""
    ocisteny = text.strip().replace(" ", "").replace(" ", "")
    if ocisteny.lower().endswith("kč"):
        ocisteny = ocisteny[:-2]
    for koncovka in (",-", ",–", ",—"):
        if ocisteny.endswith(koncovka):
            return ocisteny.removesuffix(koncovka)
    return ocisteny


def ocisti_cislo(text: str) -> str:
    """Zapis cisla z formulare ve tvaru, ktery cte `Decimal`.

    Zna to, co lide pisou na uctenky: '905,50', '1 234', '420,-', '420,–',
    '500 Kč' a '1.234,50' (tecky jsou tisice, kdyz za nimi stoji desetinna
    carka). Samotna tecka se nechava - '905.50' je 905,50.
    """
    ocisteny = bez_koncovek(text)
    if "," in ocisteny and "." in ocisteny and ocisteny.rfind(".") < ocisteny.rfind(","):
        ocisteny = ocisteny.replace(".", "")
    return ocisteny.replace(",", ".")


def na_desetinne(text: str | None):
    """Prevede desetinne cislo z formulare. Prijima '905,50' i '905.50' i '1 234'."""
    from decimal import Decimal, InvalidOperation

    if text is None:
        return None
    ocisteny = ocisti_cislo(text)
    if not ocisteny:
        return None
    try:
        return Decimal(ocisteny)
    except InvalidOperation:
        return None


def na_castku(text: str | None):
    """Prevede castku z formulare. Cte se stejne jako kazde jine desetinne
    cislo - vlastni jmeno ma proto, aby bylo na miste volani videt, ze jde
    o penize (typ Castka), a ne treba o spotrebu v litrech."""
    return na_desetinne(text)


def prazdne_na_none(text: str | None) -> str | None:
    return (text or "").strip() or None


def radky_z_formulare(formular: FormData, pole: Iterable[str]) -> dict[int, dict[str, str]]:
    """Seskupi pole tabulky podle ID radku: `{id: {"castka": "420", ...}}`.

    V tabulkach se pole jmenuji `<pole>_<id radku>`. Bere jen pole ze seznamu
    `pole` s ID z cislic (a ne vetsim nez `MAX_ID`), takze `nazev_abc` ani
    `nazev_99999999999999999999` nic nerozbiji. Soubor (UploadFile) podvrzeny
    pod jmenem pole se zahodi - radek pak zustane beze zmeny.
    """
    radky: dict[int, dict[str, str]] = {}
    jmena = sorted(pole, key=len, reverse=True)
    for klic, hodnota in formular.items():
        if not isinstance(hodnota, str):
            continue
        for jmeno in jmena:
            zbytek = klic.removeprefix(f"{jmeno}_")
            if zbytek != klic and zbytek.isascii() and zbytek.isdigit() and int(zbytek) <= MAX_ID:
                radky.setdefault(int(zbytek), {})[jmeno] = hodnota
                break
    return radky


def text_z_formulare(formular: FormData, klic: str) -> str:
    """Hodnota textoveho pole z formulare (`nacti_formular`). Chybejici pole i soubor
    (UploadFile podvrzeny pod jmenem textoveho pole) jsou prazdny text."""
    hodnota = formular.get(klic)
    return hodnota if isinstance(hodnota, str) else ""
