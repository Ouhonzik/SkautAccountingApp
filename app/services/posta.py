"""Odesilani e-mailu s evidenci.

Kazdy pokus o odeslani zalozi radek `OdeslanyEmail` se stavem - appka nikdy
neposila mail "naslepo" a Honza vidi, kdyz SMTP prestane fungovat. Telo mailu
se nikam neuklada (obsahuje odkaz s tokenem, viz app/services/tokeny.py).

Bez nastaveneho SMTP (vyvoj, testy) se mail misto odeslani ulozi jako .eml
soubor do data/posta/ - da se otevrit v libovolnem postovnim klientovi.
"""

from __future__ import annotations

import datetime as dt
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import getaddresses
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cas import PRAHA, ted
from app.config import nastaveni
from app.models import (
    CINNOST_EMAIL_OMEZEN,
    STAV_EMAILU_LOKALNE,
    STAV_EMAILU_ODESLANO,
    STAV_EMAILU_OMEZENO,
    STAV_EMAILU_SELHALO,
    OdeslanyEmail,
    Uzivatel,
    ZaznamCinnosti,
)
from app.services import log_cinnosti
from app.services.soubory import bezpecny_nazev

log = logging.getLogger(__name__)

# E-mail je prostý text: nepotřebuje url_for, hlášky ani filtry stránek, proto
# má vlastní Jinja prostředí jen nad app/templates/posta/ (a nezávisí na app.sablony).
_prostredi = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent.parent / "templates" / "posta"),
    keep_trailing_newline=True,
)

# Dodatek k hlasce o neodeslanem e-mailu. Text vyjimky ze smtplib (anglicka
# odpoved serveru, jmeno hostitele) se uzivateli neukazuje - zustava v logu
# serveru a v `OdeslanyEmail.chyba`.
HLASKA_SELHANI = "Zkus to později, podrobnosti o chybě jsou v žurnálu serveru."

# Dodatek k hlasce, kdyz neni nastavene SMTP a zprava se jen ulozila do
# data/posta/ (stav `lokalne`). Bez ni by appka tvrdila "odeslana" a nikdo by
# se nedozvedel, ze pozvany nic nedostane.
HLASKA_LOKALNE = "SMTP není nastavené, zpráva se jen uložila do data/posta/ — odkaz předej ručně."


# Dodatek k hlasce, kdyz se e-mail neodeslal kvuli limitu.
HLASKA_OMEZENO = "Za poslední dobu se odeslalo příliš mnoho e-mailů, zkus to za hodinu."

# Limity odesilani. Appka je posila v jejim jmene z adresy strediska a SMTP
# poskytovatel hodnoti pověst cele adresy - jeden ovladnuty ucet by ji mohl
# zkazit. Pocitaji se z `OdeslanyEmail`, takze prezijou restart serveru.
# Na cilovy ucet (`uzivatel_id`): u profilu je to uzivatel sam, u zapomenuteho
# hesla a pozvanky ucet, jemuz zprava jde.
EMAIL_NA_UCET_ZA_HODINU = 3
EMAIL_NA_UCET_ZA_DEN = 10
EMAIL_CELKEM_ZA_HODINU = 100


@dataclass
class Zprava:
    komu: str
    predmet: str
    text: str


def neodeslano(zaznam: OdeslanyEmail) -> bool:
    """Zprava neodesla - selhalo SMTP, nebo se narazilo na limit. Volajici
    ukaze hlasku s `hlaska_neodeslano`."""
    return zaznam.stav in (STAV_EMAILU_SELHALO, STAV_EMAILU_OMEZENO)


def hlaska_neodeslano(zaznam: OdeslanyEmail) -> str:
    """Dodatek k hlasce o neodeslanem e-mailu podle toho, proc se neodeslal."""
    return HLASKA_OMEZENO if zaznam.stav == STAV_EMAILU_OMEZENO else HLASKA_SELHANI


def _pocet_od(sezeni: Session, od: dt.datetime, uzivatel_id: int | None = None) -> int:
    dotaz = select(func.count(OdeslanyEmail.id)).where(OdeslanyEmail.vytvoreno >= od)
    if uzivatel_id is not None:
        dotaz = dotaz.where(OdeslanyEmail.uzivatel_id == uzivatel_id)
    return sezeni.scalar(dotaz) or 0


def _prekroceny_limit(sezeni: Session, uzivatel_id: int | None) -> str | None:
    """Popis limitu, ktery uz je vycerpany, nebo None. Dotaz nejdriv zapise
    rozpracovane radky (autoflush), takze zapocita i e-maily z teze zadosti."""
    cas = ted()
    pred_hodinou = cas - dt.timedelta(hours=1)
    if uzivatel_id is not None:
        if _pocet_od(sezeni, pred_hodinou, uzivatel_id) >= EMAIL_NA_UCET_ZA_HODINU:
            return f"na účet ({EMAIL_NA_UCET_ZA_HODINU} za hodinu)"
        if _pocet_od(sezeni, cas - dt.timedelta(days=1), uzivatel_id) >= EMAIL_NA_UCET_ZA_DEN:
            return f"na účet ({EMAIL_NA_UCET_ZA_DEN} za den)"
    if _pocet_od(sezeni, pred_hodinou) >= EMAIL_CELKEM_ZA_HODINU:
        return f"celé appky ({EMAIL_CELKEM_ZA_HODINU} za hodinu)"
    return None


def zaznam_omezeni(
    sezeni: Session, ucel: str, uzivatel_id: int | None, komu: str = "", predmet: str = ""
) -> OdeslanyEmail | None:
    """Kdyz je vycerpany limit odesilani, vrati zaznam se stavem `omezeno`
    (do databaze se NEuklada - utok by tabulku zaplnil), jinak None.

    Vola se i pred vydanim tokenu (`uzivatele.posli_odkaz`): novy token zrusi
    starsi platny, takze by odmitnuty e-mail jinak zneplatnil odkaz, ktery
    majitel uz ve schrance ma. Spravce se o odmitnuti dozvi z logu cinnosti
    (nejvyse jeden radek za hodinu na ucet a limit, bez adresy)."""
    limit = _prekroceny_limit(sezeni, uzivatel_id)
    if limit is None:
        return None
    log.warning("E-mail se neodeslal: vycerpany limit %s (ucel %s)", limit, ucel)
    _zapis_omezeni_do_logu(sezeni, uzivatel_id, f"e-mail se neodeslal — limit {limit}")
    return OdeslanyEmail(
        uzivatel_id=uzivatel_id,
        komu=komu,
        predmet=predmet,
        ucel=ucel,
        stav=STAV_EMAILU_OMEZENO,
        chyba=f"Vyčerpaný limit {limit}.",
    )


def _zapis_omezeni_do_logu(sezeni: Session, uzivatel_id: int | None, popis: str) -> None:
    ucet = sezeni.get(Uzivatel, uzivatel_id) if uzivatel_id is not None else None
    dotaz = select(ZaznamCinnosti.id).where(
        ZaznamCinnosti.typ_cinnosti == CINNOST_EMAIL_OMEZEN,
        ZaznamCinnosti.popis == popis,
        ZaznamCinnosti.do >= ted() - dt.timedelta(hours=1),
    )
    if ucet is None:
        dotaz = dotaz.where(ZaznamCinnosti.uzivatel_id.is_(None))
    else:
        dotaz = dotaz.where(ZaznamCinnosti.uzivatel_id == ucet.id)
    uz_zapsano = sezeni.scalar(dotaz.limit(1))
    if uz_zapsano is None:
        log_cinnosti.zapis(
            sezeni,
            CINNOST_EMAIL_OMEZEN,
            popis=popis,
            uzivatel_id=ucet.id if ucet else None,
            uzivatel_jmeno=ucet.jmeno if ucet else "neznámé jméno",
        )


def _je_jedna_adresa(komu: str) -> bool:
    return "\r" not in komu and "\n" not in komu and len(getaddresses([komu])) == 1


def vykresli(nazev: str, **data) -> str:
    """Vykresli textovou sablonu z app/templates/posta/{nazev}."""
    return _prostredi.get_template(nazev).render(**data)


def _sestav(zprava: Zprava, odesilatel: str) -> EmailMessage:
    """Slozi zpravu se stejnymi hlavickami pro SMTP i pro ulozeni do .eml."""
    hotova = EmailMessage()
    hotova["From"] = odesilatel
    hotova["To"] = zprava.komu
    hotova["Subject"] = zprava.predmet
    hotova.set_content(zprava.text)
    return hotova


def _odesli_smtp(zprava: Zprava) -> None:
    """Skutecne odeslani pres SMTP. Vyhazuje vyjimku pri jakemkoli selhani -
    volajici (`posli`) ji chyta a preklada na stav `selhalo`.

    Vlastni funkce, aby ji testy sly snadno nahradit (monkeypatch) a nemusely
    behem testu skutecne otevirat sitove spojeni.
    """
    zprava_k_odeslani = _sestav(zprava, nastaveni.smtp_odesilatel)

    if nastaveni.smtp_sifrovani == "ssl":
        # Port 465: spojeni je sifrovane od prvniho bajtu, STARTTLS tu nepatri.
        spojeni_ssl = smtplib.SMTP_SSL(nastaveni.smtp_host, nastaveni.smtp_port, timeout=10)
        with spojeni_ssl as spojeni:
            spojeni.login(nastaveni.smtp_user, nastaveni.smtp_password)
            spojeni.send_message(zprava_k_odeslani)
        return

    with smtplib.SMTP(nastaveni.smtp_host, nastaveni.smtp_port, timeout=10) as spojeni:
        spojeni.starttls()
        spojeni.login(nastaveni.smtp_user, nastaveni.smtp_password)
        spojeni.send_message(zprava_k_odeslani)


def _uloz_eml(zprava: Zprava, ucel: str) -> None:
    zprava_k_ulozeni = _sestav(zprava, nastaveni.smtp_odesilatel or "uctovani@localhost")

    nastaveni.slozka_posty.mkdir(parents=True, exist_ok=True)
    cas = dt.datetime.now(PRAHA).strftime("%Y%m%d-%H%M%S")
    nazev = bezpecny_nazev(f"{cas}_{ucel}_{zprava.komu}") + ".eml"
    (nastaveni.slozka_posty / nazev).write_bytes(bytes(zprava_k_ulozeni))


def _dorucit(zaznam: OdeslanyEmail, zprava: Zprava, ucel: str) -> None:
    """Odesle zpravu pres SMTP, nebo (bez SMTP) ji ulozi do data/posta/, a na
    `zaznam` zapise vysledek. Vyjimku nepousti.

    Chytane vyjimky se lisi zamerne: ulozeni souboru muze selhat jen na disku
    (`OSError`, `ValueError` z nazvu souboru), kdezto SMTP vyhazuje cokoli
    (sitove, TLS, protokolove chyby), tak se tam chyta vsechno."""
    lokalne = not nastaveni.smtp_nastaveno
    chytane = (OSError, ValueError) if lokalne else Exception
    try:
        if lokalne:
            _uloz_eml(zprava, ucel)
        else:
            _odesli_smtp(zprava)
            zaznam.stav = STAV_EMAILU_ODESLANO
        zaznam.odeslano = ted()
    except chytane as chyba:
        zaznam.stav = STAV_EMAILU_SELHALO
        zaznam.chyba = str(chyba)[:500]
        if lokalne:
            log.exception("Ulozeni e-mailu do data/posta/ selhalo")
        else:
            log.exception("Odeslani e-mailu pres SMTP selhalo")


def posli(
    sezeni: Session,
    zprava: Zprava,
    ucel: str,
    uzivatel_id: int | None = None,
) -> OdeslanyEmail:
    """Odesle e-mail (nebo ho ulozi lokalne bez SMTP) a zapise vysledek.

    Nikdy nevyhazuje vyjimku - volajici se pozna vysledek podle `stav` na
    vracenem zaznamu a muze na nej reagovat (napr. ukazat flash zpravu
    s chybou), misto aby appka spadla na neodeslanem mailu.

    Pred odeslanim ulozi (`commit`), co volajici rozepsal: SMTP trva az 10 s
    a rozepsany zapis drzi zamek databaze vsem ostatnim (u `async` routy
    stoji cely server). Vysledek odeslani se zapise az druhym, kratkym
    zapisem - ten commit dela volajici jako dosud.
    """
    omezeno = zaznam_omezeni(sezeni, ucel, uzivatel_id, zprava.komu, zprava.predmet)
    if omezeno is not None:
        return omezeno

    zaznam = OdeslanyEmail(
        uzivatel_id=uzivatel_id,
        komu=zprava.komu,
        predmet=zprava.predmet,
        ucel=ucel,
        stav=STAV_EMAILU_LOKALNE,
    )

    if not _je_jedna_adresa(zprava.komu):
        # Pojistka pro adresy, ktere se do databaze dostaly jinou cestou nez
        # pres `over_email` (starsi data, obnova ze zalohy): seznam adres v hlavicce
        # `To` by zpravu poslal vsem.
        zaznam.stav = STAV_EMAILU_SELHALO
        zaznam.chyba = "Adresa příjemce není jedna platná e-mailová adresa, zpráva se neodeslala."
        log.warning("E-mail se neodeslal: adresa prijemce neni jedna adresa (ucel %s)", ucel)
    else:
        sezeni.commit()
        _dorucit(zaznam, zprava, ucel)

    sezeni.add(zaznam)
    return zaznam
