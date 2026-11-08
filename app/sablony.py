"""Spolecny zaklad pro HTML stranky.

Je to zvlast od main.py, aby na to mohly sahat routy bez kruhoveho importu.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app import formaty
from app.chyby_poli import StavFormulare
from app.models.konstanty import STAV_UZAVRENA

SLOZKA_APP = Path(__file__).resolve().parent
sablony = Jinja2Templates(directory=str(SLOZKA_APP / "templates"))

KLIC_OZNAMENI = "oznameni"


def verze(nazev: str) -> str:
    """Razitko podle casu upravy souboru, pripojene k adrese stylu.

    Bez nej prohlizec drzi starou verzi CSS v pameti a zmeny se neprojevi -
    ani lokalne, ani po nasazeni na server.
    """
    try:
        return str(int((SLOZKA_APP / "static" / nazev).stat().st_mtime))
    except OSError:
        return "0"


# Filtry jsou cista pravidla z app/formaty.py (cas v databazi je UTC, na
# obrazovce se prevadi na prazsky - viz app/cas.py).
for _nazev in (
    "koruny",
    "cele_koruny",
    "desetinne",
    "km",
    "datum",
    "mnozne",
    "cas",
    "den_slovy",
    "inicialy",
    "z_json",
):
    sablony.env.filters[_nazev] = getattr(formaty, _nazev)
sablony.env.globals["verze"] = verze


def oznam(request: Request, text: str, druh: str = "ok") -> None:
    """Ulozi hlasku, ktera se ukaze na nasledujici strance.

    DO TEXTU NEPATRI E-MAIL ANI JMENO: hlaska lezi v session a ta je
    v cookie jen podepsana, ne sifrovana - kdo cookie vidi (sdileny pocitac,
    rozsireni prohlizece, log proxy), precte text bez klice. Staci obecna veta
    ("Pozvanka odeslana."), jmeno jen tam, kde bez nej neni jasne, ktery radek
    hromadne tabulky je spatne.

    Zapisuje se primo pres `request.session[...] = ...`, ne
    `.setdefault(...).append(...)` - Starlette `Session` (podtrida `dict`)
    pozna zmenu jen pres `__setitem__`/`__delitem__`/`clear`/`update`, a
    `setdefault` ji hlasi jen kdyz klic jeste neexistoval. Kdyby uz v
    session lezela nevyzvednuta hlaska z predchoziho pozadavku (napr. dve
    POST za sebou, driv nez prohlizec dojde na presmerovani), pouhy
    `.append()` na uz existujici seznam by appka poznala jako "session se
    nezmenila" a novou hlasku by vubec neposlala v Set-Cookie - tise by
    zmizela.
    """
    zpravy = request.session.get(KLIC_OZNAMENI, [])
    zpravy.append({"text": text, "druh": druh})
    request.session[KLIC_OZNAMENI] = zpravy


def strana(request: Request, nazev: str, *, status_code: int = 200, **kontext):
    kontext.setdefault("oznameni", request.session.pop(KLIC_OZNAMENI, []))
    # Stranka s formularem bez chyby u pole ma prazdny stav.
    kontext.setdefault("stav_formulare", StavFormulare())
    kontext.setdefault("uzivatel", getattr(request.state, "uzivatel", None))
    # Uzavrena akce je jen pro cteni: sablony stranek akce podle toho schovaji
    # zapisova tlacitka (`muze_psat`) a zamknou policka (`pole_zamcena` se vypise
    # do znacky pole). Server zapis odmita tak jako tak (`over_otevrenou`) - tohle je
    # jen pohodli, at clovek nepise do pole, jehoz obsah stejne neulozi.
    akce_uzavrena = getattr(kontext.get("akce"), "stav", None) == STAV_UZAVRENA
    kontext.setdefault("akce_uzavrena", akce_uzavrena)
    kontext.setdefault("pole_zamcena", "disabled" if akce_uzavrena else "")
    uzivatel = kontext["uzivatel"]
    kontext.setdefault(
        "muze_psat",
        bool(uzivatel is not None and uzivatel.muze_zapisovat) and not akce_uzavrena,
    )
    # Varovani, ze nocni kopie databaze neprobiha, vidi jen spravce.
    if uzivatel is not None and uzivatel.je_spravce:
        # Import tady, ne nahore: sluzba pres `posta` sama importuje tenhle modul.
        from app.services import stav_migraci, zaloha_databaze

        kontext.setdefault("varovani_zalohy", zaloha_databaze.varovani_zalohy())
        # Databaze nemigrovana na posledni revizi; spocitano jednou pri startu.
        kontext.setdefault("varovani_migraci", stav_migraci.varovani_migraci())
    return sablony.TemplateResponse(request, nazev, kontext, status_code=status_code)


def adresa_odkud_prisel(request: Request) -> str | None:
    """Stranka, odkud formular prisel (hlavicka Referer), nebo None.

    Prijima se jen adresa na stejnem serveru (cesta od `/`, ne `//`), aby z toho
    nesla udelat adresa nekam ven. Pouziva ji `adresa_zpet` (navrat po zamitnuti)
    a `strana_s_chybou` (adresa v adresnim radku po chybe u pole)."""
    odkaz = request.headers.get("referer")
    if not odkaz:
        return None
    casti = urlsplit(odkaz)
    if (
        casti.netloc == request.url.netloc
        and casti.path.startswith("/")
        and not casti.path.startswith("//")
    ):
        return casti.path + (f"?{casti.query}" if casti.query else "")
    return None


def strana_s_chybou(request: Request, nazev: str, stav: StavFormulare, **kontext):
    """Stranka s formularem, ve kterem je neco spatne: stav 422, hodnoty
    zustanou ve formulari, chyba je u pole a souhrn i v hlasce vpravo dole.
    Do databaze se pred tim nic nezapsalo. Hlaska jde rovnou do stranky, ne pres
    session: nemusi se zapisovat do cookie, kdyz ji stejny pozadavek hned zobrazi."""
    oznameni = [*request.session.pop(KLIC_OZNAMENI, []), {"text": stav.souhrn, "druh": "chyba"}]
    # Stranka s chybou je odpoved na POST: v adresnim radku by zustala adresa POST
    # (obnoveni stranky by nabidlo odeslat formular znovu, otevreni adresy by
    # skoncilo chybou 405). `js/spolecne.js` proto adresu nahradi adresou stranky,
    # odkud formular prisel; bez Refereru se nic nemeni.
    kontext.setdefault("adresa_po_chybe", adresa_odkud_prisel(request))
    return strana(
        request, nazev, status_code=422, stav_formulare=stav, oznameni=oznameni, **kontext
    )


def zpet(cesta: str) -> RedirectResponse:
    """Po ulozeni vzdy presmerovat, at obnoveni stranky neodesle formular znovu."""
    return RedirectResponse(cesta, status_code=303)
