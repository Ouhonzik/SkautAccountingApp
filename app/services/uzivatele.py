"""Sprava uzivatelskych uctu.

Spolecna pravidla kolem uctu (nastaveni hesla, kontrola volneho e-mailu,
hledani uctu podle loginu/e-mailu, e-mail s odkazem, zalozeni uctu) ziji tady,
aby je kazda cesta (pozvanka, reset hesla, profil, /uzivatele, CLI prikaz
`uv run spravce`, migrace) pouzivala stejne - driv byla stejna pravidla
napsana na peti az ctyrech mistech zvlast (Kapitola 5 plan-oprav.md, K-084,
K-085, K-066). Routa `app/routes/uzivatele.py` uz jen prevadi formular
a ukazuje hlasku.
"""

from __future__ import annotations

import datetime as dt
import re
import secrets
import unicodedata
from email.utils import parseaddr
from typing import Literal

import bcrypt
import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, object_session

from app.cas import PRAHA
from app.config import nastaveni
from app.models import (
    CINNOST_POZVANKA,
    CINNOST_SMAZANI,
    ROLE,
    ROLE_PODEPISUJICI,
    ROLE_SPRAVCE,
    ROLE_UZIVATEL,
    UCEL_POZVANKA,
    UCEL_RESET_HESLA,
    UCEL_UPOZORNENI_EMAILU,
    UCEL_ZMENA_EMAILU,
    Clovek,
    OdeslanyEmail,
    PouzitiAI,
    TokenUzivatele,
    Uzivatel,
    ZaznamCinnosti,
)
from app.services import log_cinnosti, posta, tokeny
from app.services.omezeni_prihlaseni import Pravidlo, omezovac

# Odkazu na obnovu hesla nejvyse (kolik, za kolik sekund) na jeden ucet.
RESET_HESLA_NA_UCET = (3, 3600)

LOGIN_MIN, LOGIN_MAX = 3, 30
LOGIN_VZOR = re.compile(rf"^[a-z0-9._-]{{{LOGIN_MIN},{LOGIN_MAX}}}$")

# Znaky, ktere v jedne adrese nemaji co delat: mezera a bile znaky, oddelovace
# vice adresat (`,` `;`), zavorky a uvozovky z „Jan <a@b.cz>“ a zpetne lomitko.
_ZAKAZANE_V_EMAILU = r"\s,;<>()\[\]\\\""
EMAIL_VZOR = re.compile(
    rf"^[^{_ZAKAZANE_V_EMAILU}@]+@(?:[^{_ZAKAZANE_V_EMAILU}@.]+\.)+[^{_ZAKAZANE_V_EMAILU}@.]+$"
)

# Docasne heslo: 4 skupiny po 4 znacich z abecedy bez podobnych znaku (i, l, o, 0, 1),
# aby se dalo diktovat po telefonu. 31^16 = asi 79 bitu; online hadani brzdi
# omezovac a lhuta platnosti.
ABECEDA_DOCASNEHO_HESLA = "abcdefghjkmnpqrstuvwxyz23456789"
DOCASNE_HESLO_PLATI_DNI = 7


class ChybaPole(ValueError):
    """Chyba udaje noveho uctu s urcenym polem formulare: formular ji ukaze
    u pole `pole`. Zbytek kodu ji zachytava jako obycejny `ValueError`."""

    def __init__(self, zprava: str, pole: str) -> None:
        super().__init__(zprava)
        self.pole = pole


def over_login(text: str) -> str | None:
    """Zkontroluje tvar loginu. Vrati chybovou hlasku, nebo None kdyz je v poradku."""
    if not LOGIN_VZOR.match(text):
        return f"Přihlašovací jméno smí mít jen malá písmena, číslice, tečku, pomlčku a podtržítko ({LOGIN_MIN}–{LOGIN_MAX} znaků)."
    return None


def over_email(text: str) -> str | None:
    """Zkontroluje, ze `text` je jedna e-mailova adresa. Vrati chybovou hlasku,
    nebo None kdyz je v poradku (stejne jako `over_login`).

    Bez toho by pole s „ja@x.cz, cizi@y.cz“ rozeslalo zpravy z adresy strediska
    vice lidem (hlavicka `To` bere cely seznam) a novy radek v adrese by shodil
    ulozeni. Volajici predava uz oriznuty text - mezera na okraji je
    tady chyba. Bez nove knihovny: pydantic `EmailStr` by chtel balicek
    `email-validator`.
    """
    if len(text) > 254 or not EMAIL_VZOR.match(text) or ".." in text or parseaddr(text)[1] != text:
        return "Zadej jednu platnou e-mailovou adresu (bez mezer, čárek a dalších adres)."
    return None


def zahesluj(heslo: str) -> str:
    return bcrypt.hashpw(heslo.encode(), bcrypt.gensalt()).decode()


def chyba_hesla(heslo: str, znovu: str) -> str | None:
    """Zkontroluje nove heslo, ktere si clovek sam voli (pozvanka, reset
    hesla, zmena v profilu, `uv run spravce`) - jedno misto pravidel misto
    ctyr kopii. Vraci chybovou hlasku, nebo None kdyz je v poradku."""
    if heslo != znovu:
        return "Hesla se neshodují."
    if len(heslo) < 8:
        return "Heslo musí mít aspoň 8 znaků."
    if len(heslo.encode()) > 72:
        # bcrypt 5 nad 72 bajty vyhazuje ValueError - cestina s
        # diakritikou ma znaky za 2 bajty, limit tak trefi uz kratsi heslo.
        return "Heslo je moc dlouhé — nejvýš 72 bajtů (asi 36–72 znaků, podle diakritiky)."
    return None


def nastav_heslo(ucet: Uzivatel, heslo: str, *, docasne: bool = False) -> None:
    """Nastavi otisk hesla a zvysi „verzi hesla“ - to odhlasi kazdou drivejsi
    prihlasovaci session uctu (`auth.nacti_uzivatele`). Jedno misto pro vsech
    pet cest, kde se dnes hesla nastavuje; `verze_hesla or 0`
    opravuje pad na cerstve zalozenem uctu, kde je pred prvnim flush jeste
    None - sloupcovy `default=1` se dosadi az pri zapisu.

    Zneplatni take vsechny nepouzite odkazy uctu (reset hesla, pozvanka, zmena
    e-mailu): kdo si heslo meni, protoze tusi, ze se mu nekdo dostal k uctu nebo
    schrance, nechce, aby drive vyzadany odkaz platil dal. Ucet, ktery
    jeste neni v databazi, zadne odkazy nema."""
    ucet.otisk_hesla = zahesluj(heslo)
    ucet.musi_zmenit_heslo = docasne
    # Docasne heslo po case vyprsi; vlastni heslo lhutu nema.
    ucet.docasne_heslo_do = (
        dt.datetime.now(dt.UTC).replace(tzinfo=None) + dt.timedelta(days=DOCASNE_HESLO_PLATI_DNI)
        if docasne
        else None
    )
    ucet.verze_hesla = (ucet.verze_hesla or 0) + 1
    sezeni = object_session(ucet)
    if sezeni is not None and ucet.id is not None:
        tokeny.zneplatni_vse(sezeni, ucet)


def docasne_heslo() -> str:
    """Docasne heslo ve tvaru 'xxxx-xxxx-xxxx-xxxx' (viz ABECEDA_DOCASNEHO_HESLA)."""
    return "-".join(
        "".join(secrets.choice(ABECEDA_DOCASNEHO_HESLA) for _ in range(4)) for _ in range(4)
    )


def zaloz_prvniho_spravce(spojeni: Connection, login: str, otisk: str) -> int:
    """Vlozi prvniho spravce, ale jen kdyz je tabulka uzivatel uplne prazdna.

    Pouziva se z migrace `b42fe20a9964_uzivatele.py`, aby se po prechodu na
    vice uctu Honza prihlasil stejnymi udaji jako driv - a z testu, ktere to
    overuji bez nutnosti pouSet celou migraci. Tabulka se popisuje nalehko
    pres `sa.table()`, stejne jako v `e3a91c47b5d2` - migrace se nesmi rozbit,
    az `app.models.Uzivatel` dostane dalsi sloupec.

    Vraci pocet vlozenych radku (0 nebo 1).
    """
    uzivatel = sa.table(
        "uzivatel",
        sa.column("login", sa.String),
        sa.column("jmeno", sa.String),
        sa.column("otisk_hesla", sa.String),
        sa.column("role", sa.String),
        sa.column("aktivni", sa.Boolean),
        sa.column("musi_zmenit_heslo", sa.Boolean),
        sa.column("verze_hesla", sa.Integer),
    )
    if spojeni.execute(sa.select(sa.func.count()).select_from(uzivatel)).scalar():
        return 0
    spojeni.execute(
        uzivatel.insert().values(
            login=login,
            jmeno=login.capitalize(),
            otisk_hesla=otisk,
            role="spravce",  # literal, ne import konstanty - viz docstring vyse
            aktivni=True,
            musi_zmenit_heslo=False,
            verze_hesla=1,
        )
    )
    return 1


def zkontroluj_zmenu(
    sezeni: Session,
    cil: Uzivatel,
    aktualni: Uzivatel,
    *,
    nova_role: str | None = None,
    novy_stav_aktivni: bool | None = None,
) -> str | None:
    """Overi zmenu role nebo aktivity uzivatele `cil`. Vraci chybovou hlasku,
    nebo None, kdyz je zmena v poradku.

    Dve pravidla, obe kolem toho, aby appka nikdy nezustala bez spravy:
    spravce nesmi odebrat prava sam sobe (o to musi pozadat jineho spravce,
    aby se nestalo, ze si nekdo omylem zabouchne dvere) a posledni aktivni
    spravce se nesmi degradovat ani deaktivovat vubec.
    """
    odebira_se_pravo = (
        nova_role is not None and nova_role != ROLE_SPRAVCE
    ) or novy_stav_aktivni is False
    if not (cil.role == ROLE_SPRAVCE and cil.aktivni and odebira_se_pravo):
        return None

    if cil.id == aktualni.id:
        return "Sám sobě nemůžeš odebrat správcovská práva ani se deaktivovat — požádej jiného správce."

    pocet_ostatnich = sezeni.scalar(
        select(func.count(Uzivatel.id)).where(
            Uzivatel.role == ROLE_SPRAVCE,
            Uzivatel.aktivni.is_(True),
            Uzivatel.id != cil.id,
        )
    )
    if not pocet_ostatnich:
        return f"„{cil.jmeno}“ je poslední aktivní správce — appka by zůstala bez správy uživatelů."
    return None


def zkontroluj_smazani(sezeni: Session, cil: Uzivatel, aktualni: Uzivatel) -> str | None:
    """Overi, ze se ucet `cil` smi smazat. Vraci chybovou hlasku, nebo None.

    Smazat jde jen deaktivovany ucet cizi osoby a nikdy posledni aktivni spravce -
    appka by zustala bez nekoho, kdo smi spravovat ucty. (Posledni spravce je pri
    deaktivovanem `cil` a aktivnim `aktualni` vzdy kryty, kontrola je tu jako pojistka.)
    """
    if cil.id == aktualni.id:
        return "Sám sebe smazat nemůžeš — požádej jiného správce."
    if cil.aktivni:
        return f"„{cil.jmeno}“ je aktivní. Smazat jde jen deaktivovaný účet — nejdřív ho deaktivuj."
    if cil.role == ROLE_SPRAVCE:
        zbyva_spravcu = sezeni.scalar(
            select(func.count(Uzivatel.id)).where(
                Uzivatel.role == ROLE_SPRAVCE,
                Uzivatel.aktivni.is_(True),
                Uzivatel.id != cil.id,
            )
        )
        if not zbyva_spravcu:
            return f"„{cil.jmeno}“ je poslední správce — appka by zůstala bez správy uživatelů."
    return None


def smaz_ucet(sezeni: Session, cil: Uzivatel, aktualni: Uzivatel) -> None:
    """Smaze ucet `cil` i vsechno, co je jen jeho: tokeny z odkazu, odeslane e-maily,
    evidenci cteni AI a zaznamy v logu cinnosti, kde je autorem nebo predmetem.
    Obchodni data (akce, doklady, uctenky) se nemaze. Jedina vazba, ktera zustava,
    je radek v ciselniku Lide (`Clovek.uzivatel_id`) - ten ztrati jen odkaz na ucet.

    Vsechno jsou hromadne SQL prikazy (ne `sezeni.delete`), protoze automaticky log by
    pri smazani ORM objektu zapsal radek s loginem smazaneho; misto toho se na konci
    zapise jeden neutralni zaznam bez udaju smazaneho. Zavolej po `zkontroluj_smazani`
    a po `db.zamkni_zapis`; commit dela volajici.
    """
    ucet_id, login, jmeno, email = cil.id, cil.login, cil.jmeno, cil.email

    sezeni.execute(sa.delete(TokenUzivatele).where(TokenUzivatele.uzivatel_id == ucet_id))
    sezeni.execute(sa.delete(PouzitiAI).where(PouzitiAI.uzivatel_id == ucet_id))
    # E-mail se hleda i bez vazby na ucet: zprava na jeho adresu mohla vzniknout bez nej.
    emaily = OdeslanyEmail.uzivatel_id == ucet_id
    if email:
        emaily = sa.or_(emaily, func.lower(OdeslanyEmail.komu) == email.lower())
    sezeni.execute(sa.delete(OdeslanyEmail).where(emaily))

    # Log: kde je autorem nebo predmetem (radek `uzivatel` s jeho id), a to, co o nem
    # napsal nekdo jiny do textu - pozvanka od spravce nese jmeno i e-mail (`posli_pozvanku`),
    # takze staci hledat e-mail. `instr` rozlisuje velka a mala pismena, `LIKE` ne.
    v_logu = [
        ZaznamCinnosti.uzivatel_id == ucet_id,
        sa.and_(ZaznamCinnosti.typ_objektu == "uzivatel", ZaznamCinnosti.objekt_id == ucet_id),
        sa.and_(
            ZaznamCinnosti.uzivatel_id.is_(None), ZaznamCinnosti.uzivatel_jmeno.in_((login, jmeno))
        ),
    ]
    if email:
        v_logu += [
            func.instr(ZaznamCinnosti.popis, email) > 0,
            func.instr(func.coalesce(ZaznamCinnosti.detail_json, ""), email) > 0,
        ]
    sezeni.execute(sa.delete(ZaznamCinnosti).where(sa.or_(*v_logu)))

    sezeni.execute(sa.update(Clovek).where(Clovek.uzivatel_id == ucet_id).values(uzivatel_id=None))
    sezeni.expunge(cil)
    sezeni.execute(sa.delete(Uzivatel).where(Uzivatel.id == ucet_id))

    log_cinnosti.zapis(
        sezeni,
        CINNOST_SMAZANI,
        popis="smazán uživatelský účet",
        uzivatel_id=aktualni.id,
        uzivatel_jmeno=aktualni.jmeno,
    )


def navrhni_login(sezeni: Session, email: str) -> str:
    """Navrhne prihlasovaci jmeno z e-mailu - cast pred zavinacem prevedena
    na povoleny tvar (`over_login`). Pri kolizi s existujicim loginem
    pripoji cislo (2, 3, ...)."""
    zaklad = email.split("@", 1)[0].lower()
    zaklad = unicodedata.normalize("NFKD", zaklad).encode("ascii", "ignore").decode("ascii")
    zaklad = re.sub(r"[^a-z0-9._-]", "", zaklad)[:LOGIN_MAX]
    if len(zaklad) < LOGIN_MIN:
        zaklad = (zaklad + "ucet")[:LOGIN_MAX]

    login = zaklad
    pripona = 1
    while sezeni.scalar(select(Uzivatel).where(Uzivatel.login == login)):
        pripona += 1
        pripona_text = str(pripona)
        login = f"{zaklad[: LOGIN_MAX - len(pripona_text)]}{pripona_text}"
    return login


def nahodny_otisk_hesla() -> str:
    """Otisk hesla, ktere se nikam neuklada citelne a nikdo ho nezna - ucet
    pozvany e-mailem se poprve prihlasi az heslem, ktere si sam nastavi
    na /pozvanka."""
    return bcrypt.hashpw(secrets.token_urlsafe(32).encode(), bcrypt.gensalt()).decode()


def email_je_volny(sezeni: Session, email: str, *, krome_id: int | None = None) -> bool:
    """Overi, ze e-mail (bez ohledu na velikost pismen) nepouziva jiny ucet -
    jedno misto pravidla misto ctyr kopii."""
    dotaz = select(Uzivatel).where(func.lower(Uzivatel.email) == email.strip().lower())
    if krome_id is not None:
        dotaz = dotaz.where(Uzivatel.id != krome_id)
    return sezeni.scalar(dotaz) is None


def email_zmenen(
    sezeni: Session, ucet: Uzivatel, stary_email: str | None, *, kdo: str
) -> OdeslanyEmail | None:
    """Vola se PO zmene `ucet.email` (volajici sam nastavi i `email_overen`):
    zneplatni vsechny nepouzite odkazy uctu, protoze e-mail je to, kam chodi
    obnova hesla, a posle na PUVODNI adresu kratke oznameni bez odkazu, at se
    puvodni majitel dozvi, ze ji nekdo z uctu odebral.

    Vraci zaznam o odeslani, nebo None, kdyz nebylo komu psat (ucet e-mail
    predtim nemel, nebo se zmenila jen velikost pismen). Kdo funkci vola, musi
    pri `posta.neodeslano(zaznam)` ukazat hlasku s `posta.hlaska_neodeslano`, jako u kazdeho posli().
    `kdo` je text do zpravy: „správce Honza“ nebo „ty sám(a) ve svém profilu“.
    """
    tokeny.zneplatni_vse(sezeni, ucet)
    if not stary_email or stary_email.strip().lower() == (ucet.email or "").strip().lower():
        return None
    text = posta.vykresli(
        "zmena_emailu_upozorneni.txt",
        jmeno=ucet.jmeno,
        stary_email=stary_email,
        novy_email=ucet.email,
        kdo=kdo,
    )
    return posta.posli(
        sezeni,
        posta.Zprava(
            komu=stary_email,
            predmet="E-mail tvého účtu se změnil — Účtování akcí",
            text=text,
        ),
        UCEL_UPOZORNENI_EMAILU,
        uzivatel_id=ucet.id,
    )


def najdi_ucet(sezeni: Session, prihlaseni: str) -> Uzivatel | None:
    """Najde ucet podle prihlasovaciho jmena, nebo (obsahuje-li „@“) podle
    e-mailu bez ohledu na velikost pismen - stejne pravidlo pro prihlaseni
    (`auth.overit`) i pro zapomenute heslo, driv zvlast na obou mistech
."""
    hledane = prihlaseni.strip().lower()
    if "@" in hledane:
        return sezeni.scalar(select(Uzivatel).where(func.lower(Uzivatel.email) == hledane))
    return sezeni.scalar(select(Uzivatel).where(Uzivatel.login == hledane))


# Sablona, predmet a cesta odkazu podle ucelu - `posli_odkaz` z nich slozi mail.
_ODKAZY: dict[str, tuple[str, str, str]] = {
    UCEL_POZVANKA: ("pozvanka.txt", "Pozvánka do aplikace Účtování akcí", "/pozvanka/{token}"),
    UCEL_RESET_HESLA: (
        "reset_hesla.txt",
        "Obnovení hesla — Účtování akcí",
        "/heslo/obnovit/{token}",
    ),
    UCEL_ZMENA_EMAILU: (
        "zmena_emailu.txt",
        "Potvrzení e-mailu — Účtování akcí",
        "/profil/email/potvrdit/{token}",
    ),
}


def posli_odkaz(
    sezeni: Session,
    ucet: Uzivatel,
    ucel: str,
    *,
    komu: str | None = None,
    nova_hodnota: str | None = None,
    **data: str,
) -> OdeslanyEmail:
    """Vyda jednorazovy token, posle e-mail s odkazem na nej a zapise vysledek
    (`OdeslanyEmail`) - spolecne pro pozvanku, reset hesla i zmenu e-mailu,
    driv byl kazdy z tech tri mailu napsany zvlast.

    `komu` je adresa, na kterou mail jde - u zmeny e-mailu to je nova (jeste
    neoverena) adresa, ne `ucet.email`. Platnost odkazu v textu mailu se bere
    z prave ulozeneho tokenu (`platnost_do`), ne se pocita znovu.
    """
    sablona, predmet, cesta = _ODKAZY[ucel]
    cil = komu if komu is not None else ucet.email
    assert cil is not None  # volajici (nize) posila jen uctum s e-mailem
    # Limit se kontroluje PRED vydanim tokenu: novy token ruší starší platný,
    # takze by odmitnuty e-mail jinak zneplatnil odkaz, ktery majitel uz ma.
    omezeno = posta.zaznam_omezeni(sezeni, ucel, ucet.id, cil, predmet)
    if omezeno is not None:
        return omezeno
    token = tokeny.vydej(sezeni, ucet, ucel, nova_hodnota)
    zaznam_tokenu = tokeny.najdi(sezeni, ucel, token)
    assert zaznam_tokenu is not None  # prave vydany token je vzdy platny
    platnost = zaznam_tokenu.platnost_do.replace(tzinfo=dt.UTC).astimezone(PRAHA)

    text = posta.vykresli(
        sablona,
        jmeno=ucet.jmeno,
        odkaz=f"{nastaveni.verejna_url}{cesta.format(token=token)}",
        platnost=platnost.strftime("%d. %m. %Y %H:%M"),
        **data,
    )
    return posta.posli(
        sezeni,
        posta.Zprava(komu=cil, predmet=predmet, text=text),
        ucel,
        uzivatel_id=ucet.id,
    )


def posli_obnovu_hesla(sezeni: Session, prihlaseni: str) -> OdeslanyEmail | None:
    """Najde aktivni ucet s e-mailem a posle mu odkaz na obnoveni hesla.

    Vraci None bez jakekoli chyby, kdyz ucet neexistuje, je neaktivni, nema
    e-mail nebo uz dostal `RESET_HESLA_NA_UCET` odkazu za hodinu - volajici
    (`/zapomenute-heslo`, B-020) na tom nesmi rozeznat, ktery pripad nastal,
    appka vzdy odpovi stejnou hlaskou.

    Limit je podle uctu, ne podle napsaneho textu: login a e-mail jsou dve
    jmena jednoho uctu. Bez nej by kdokoli bez prihlaseni mohl sypat mail
    obeti do schranky a kazdy novy odkaz by zneplatnil predchozi, takze by
    majitel heslo neobnovil.
    """
    ucet = najdi_ucet(sezeni, prihlaseni)
    if ucet is None or not ucet.aktivni or not ucet.email:
        return None
    if omezovac.zablokovano(Pravidlo(f"reset-ucet:{ucet.id}", *RESET_HESLA_NA_UCET)):
        return None
    omezovac.zaznamenej_neuspech(f"reset-ucet:{ucet.id}")
    return posli_odkaz(sezeni, ucet, UCEL_RESET_HESLA)


def zaloz_ucet(
    sezeni: Session,
    *,
    login: str,
    jmeno: str,
    email: str | None,
    role: str,
    zpusob: Literal["pozvanka", "docasne_heslo"],
    pozval: Uzivatel,
) -> tuple[Uzivatel, str | None, OdeslanyEmail | None]:
    """Zalozi novy ucet - pozvankou e-mailem (neaktivni, heslo si nastavi sam
    na /pozvanka), nebo docasnym heslem od spravce (aktivni rovnou, ale po
    prvnim prihlaseni musi heslo zmenit). Driv mel kazdy zpusob vlastni sadu
    kontrol a vychozich hodnot.

    Vraci `(ucet, docasne_heslo, OdeslanyEmail)` - podle `zpusob` je vzdy
    vyplnene prave jedno z druheho a tretiho mista, nikdy obe.

    Chyby (neplatny login, prazdne jmeno, obsazeny login/e-mail, pozvanka bez
    e-mailu) hlasi vyjimkou `ChybaPole` (je to `ValueError`) s hlaskou pro cloveka - volajici
    (routa) ji zachyti a ukaze pres `oznam(..., "chyba")`.
    """
    login = login.strip().lower()
    jmeno = jmeno.strip()
    email = (email or "").strip() or None

    _over_udaje_noveho_uctu(sezeni, login, jmeno, email, zpusob)
    if role not in ROLE:
        role = ROLE_UZIVATEL

    if zpusob == "pozvanka":
        novy = Uzivatel(
            login=login,
            jmeno=jmeno,
            email=email,
            otisk_hesla=nahodny_otisk_hesla(),
            role=role,
            aktivni=False,
        )
        sezeni.add(novy)
        sezeni.flush()  # potrebuje pridelene id pro token
        zaznam = posli_pozvanku(sezeni, novy, pozval)
        return novy, None, zaznam

    heslo = docasne_heslo()
    # Ucet s docasnym heslem je aktivni hned, pozvanka se u nej nikdy nepouzije.
    novy = Uzivatel(login=login, jmeno=jmeno, email=email, role=role, nekdy_aktivni=True)
    nastav_heslo(novy, heslo, docasne=True)
    sezeni.add(novy)
    return novy, heslo, None


def _over_udaje_noveho_uctu(
    sezeni: Session, login: str, jmeno: str, email: str | None, zpusob: str
) -> None:
    """Pravidla pro zalozeni uctu; porusene pravidlo = `ValueError` s hlaskou.
    Poradi kontrol urcuje, kterou hlasku clovek uvidi jako prvni."""
    chyba = over_login(login)
    if chyba:
        raise ChybaPole(chyba, "login")
    if not jmeno:
        raise ChybaPole("Vyplň i jméno.", "jmeno")
    if sezeni.scalar(select(Uzivatel).where(Uzivatel.login == login)):
        raise ChybaPole(f"Přihlašovací jméno „{login}“ už někdo používá.", "login")
    if email:
        chyba = over_email(email)
        if chyba:
            raise ChybaPole(chyba, "email")
    if email and not email_je_volny(sezeni, email):
        raise ChybaPole("Tenhle e-mail už používá jiný účet.", "email")
    if zpusob == "pozvanka" and not email:
        raise ChybaPole("Pro pozvánku e-mailem musíš vyplnit e-mail.", "email")


def chyba_hesla_u_pole(heslo: str, znovu: str) -> dict[str, str]:
    """Chyba nového hesla s polem, ke kterému patří (`chyba_hesla` je text bez pole):
    „hesla se neshodují“ patří k opakování, ostatní k prvnímu heslu. Prázdné = v pořádku."""
    chyba = chyba_hesla(heslo, znovu)
    if chyba is None:
        return {}
    return {"znovu_heslo" if heslo != znovu else "heslo": chyba}


def chyby_registrace(
    sezeni: Session, ucet: Uzivatel, login: str, jmeno: str, heslo: str, znovu: str
) -> dict[str, str]:
    """Zkontroluje udaje z formulare pozvanky (login uz musi byt `strip().lower()`,
    jmeno `strip()`). Vraci chyby podle pole formulare, prazdne = vse v poradku."""
    chyby: dict[str, str] = {}
    chyba = over_login(login)
    if (
        chyba is None
        and login != ucet.login
        and sezeni.scalar(select(Uzivatel).where(Uzivatel.login == login, Uzivatel.id != ucet.id))
    ):
        chyba = f"Přihlašovací jméno „{login}“ už někdo používá."
    if chyba is not None:
        chyby["login"] = chyba
    if not jmeno:
        chyby["jmeno"] = "Jméno nesmí být prázdné."
    chyby.update(chyba_hesla_u_pole(heslo, znovu))
    return chyby


def aktivuj_ucet(ucet: Uzivatel, login: str, jmeno: str, heslo: str) -> None:
    """Dokonci registraci z pozvanky: ulozi login, jmeno a heslo a ucet aktivuje."""
    ucet.login = login
    ucet.jmeno = jmeno
    nastav_heslo(ucet, heslo)
    ucet.aktivni = True
    ucet.nekdy_aktivni = True
    ucet.email_overen = True


def _over_ze_neni_deaktivovan(ucet: Uzivatel) -> None:
    """Pozvanka je jen pro ucet, ktery jeste nebyl aktivni. Deaktivovany ucet
    (aktivni=False, ale uz nekdy aktivni) by pozvanka oživila i se starou
    roli - treba spravcovskou - a obesla by tlacitko Aktivovat."""
    if ucet.nekdy_aktivni and not ucet.aktivni:
        raise ValueError(f"Účet „{ucet.jmeno}“ je deaktivovaný — aktivuj ho ručně na /uzivatele.")


def posli_pozvanku(sezeni: Session, ucet: Uzivatel, pozval: Uzivatel) -> OdeslanyEmail:
    """Vyda token a posle e-mail s pozvankou pro uz existujici (neaktivni) ucet.

    Chyby (deaktivovany ucet, ucet bez e-mailu) hlasi `ValueError` s hlaskou
    pro cloveka - volajici (routa) ji ukaze pres `oznam(..., "chyba")`.
    """
    _over_ze_neni_deaktivovan(ucet)
    if not ucet.email:
        raise ValueError("Účet nemá e-mail.")
    zaznam = posli_odkaz(sezeni, ucet, UCEL_POZVANKA, pozval=pozval.jmeno)
    # OdeslanyEmail se do logu cinnosti nezapisuje (obsahoval by spam radky
    # a otisky tokenu) - bez tohohle rucniho zaznamu by opakovana pozvanka
    # (na rozdil od te prvni, ktera zalozi novy ucet) nezanechala v logu stopu.
    log_cinnosti.zapis(
        sezeni,
        CINNOST_POZVANKA,
        uzivatel_id=pozval.id,
        uzivatel_jmeno=pozval.jmeno,
        popis=f"pozvánka pro {ucet.jmeno} ({ucet.email}) — {zaznam.stav}",
    )
    return zaznam


def posledni_pozvanky(sezeni: Session, uzivatel_ids: list[int]) -> dict[int, OdeslanyEmail | None]:
    """Posledni odeslana pozvanka kazdeho z uctu (None, kdyz zadna) - jednim dotazem.
    Pro stranky Uzivatele a Lide, kde se jinak ptalo na kazdy ucet zvlast."""
    posledni: dict[int, OdeslanyEmail | None] = dict.fromkeys(uzivatel_ids)
    if not uzivatel_ids:
        return posledni
    dotaz = (
        select(OdeslanyEmail)
        .where(OdeslanyEmail.uzivatel_id.in_(uzivatel_ids), OdeslanyEmail.ucel == UCEL_POZVANKA)
        .order_by(OdeslanyEmail.vytvoreno.desc(), OdeslanyEmail.id.desc())
    )
    for zaznam in sezeni.scalars(dotaz):
        if zaznam.uzivatel_id is not None and posledni[zaznam.uzivatel_id] is None:
            posledni[zaznam.uzivatel_id] = zaznam
    return posledni


def _propoj_podle_emailu(sezeni: Session, clovek: Clovek, email: str) -> Uzivatel | None:
    """Najde ucet se stejnym e-mailem a napoji ho na `clovek`; kdyz zadny neni,
    vrati None. Ucet napojeny na JINEHO cloveka se odmitne (`ValueError`)."""
    existujici = sezeni.scalar(select(Uzivatel).where(Uzivatel.email == email))
    if existujici is None:
        return None
    jiny_clovek = sezeni.scalar(
        select(Clovek).where(Clovek.uzivatel_id == existujici.id, Clovek.id != clovek.id)
    )
    if jiny_clovek is not None:
        raise ValueError(
            f"Tenhle e-mail už má appka spárovaný s „{jiny_clovek.jmeno}“ "
            "v číselníku Lidé — jeden účet nejde napojit na dva lidi."
        )
    clovek.uzivatel_id = existujici.id
    return existujici


def pozvi_cloveka(
    sezeni: Session, clovek: Clovek, pozval: Uzivatel
) -> tuple[Uzivatel, OdeslanyEmail | None]:
    """Pozve cloveka z ciselniku Lide e-mailem, aby si zalozil ucet role
    Podepisujici (zaklad pro budouci e-podpisy).

    Kdyz uz existuje ucet se stejnym e-mailem (aktivni i neaktivni) a neni
    napojeny na jineho cloveka, jen ho propoji, misto aby zalozil druhy se
    stejnym e-mailem - to by narazilo na unikatni index `uq_uzivatel_email`.
    U aktivniho uctu (typicky plátce, ktery uz je zaroven beznym uzivatelem
    appky) se dalsi mail neposila, u neaktivniho se posle/znovu posle
    pozvanka na ten uz existujici ucet. Je-li ale ten samy e-mail uz napojeny
    na JINEHO cloveka (dva radky v ciselniku omylem se stejnym e-mailem),
    appka radeji odmitne nez aby jeden ucet napojila na dva lidi - to by
    narazilo na `uq_clovek_uzivatel` (ucet smi patrit nejvyse jednomu clovku).
    Opakovane pozvani cloveka s neaktivnim uctem posle novou pozvanku - stary
    odkaz zneplatni `tokeny.vydej` sam.

    Pozvat se da jen ucet, ktery jeste nikdy nebyl aktivni (`nekdy_aktivni`).
    Deaktivovany ucet se nepozve - pozvanka by ho oživila i se starou roli
    a obesla tlacitko Aktivovat. Existujici neaktivni ucet s jinou
    roli nez Podepisujici (pozvanka z Lidi dava vzdy tuhle roli, SPEC §19)
    se jen propoji a mail se neposila. E-mail uctu se pred pozvanku prepise
    e-mailem cloveka z Lidi, aby pozvanka i hlaska mirily na tutez, aktualni
    adresu - proto to nesmi fungovat na deaktivovanem uctu, jinak by
    kdokoli, kdo smi upravit Lidi, presmeroval e-mail deaktivovaneho spravce.

    Chyby hlasi `ValueError`; volajici je pri ni vrati zpet (`rollback`).
    """
    email = (clovek.email or "").strip()
    if not email:
        raise ValueError("Nejdřív vyplň e-mail.")
    chyba = over_email(email)
    if chyba:
        raise ValueError(f"E-mail u „{clovek.jmeno}“ nejde použít. {chyba}")

    ucet = sezeni.get(Uzivatel, clovek.uzivatel_id) if clovek.uzivatel_id else None
    if ucet is not None and ucet.aktivni:
        raise ValueError(f"„{clovek.jmeno}“ už má aktivní účet.")

    if ucet is None:
        ucet = _propoj_podle_emailu(sezeni, clovek, email)
        if ucet is None:
            # Novy ucet zalozi a rovnou pozve `zaloz_ucet` - potrebuje
            # pridelene id pro vazbu z Clovek, tak se propoji az po navratu.
            ucet, _heslo, zaznam = zaloz_ucet(
                sezeni,
                login=navrhni_login(sezeni, email),
                jmeno=clovek.jmeno,
                email=email,
                role=ROLE_PODEPISUJICI,
                zpusob="pozvanka",
                pozval=pozval,
            )
            clovek.uzivatel_id = ucet.id
            return ucet, zaznam
        if ucet.aktivni:
            return ucet, None

    # Odsud je `ucet` existujici neaktivni ucet (napojeny, nebo nalezeny podle e-mailu).
    _over_ze_neni_deaktivovan(ucet)
    if ucet.role != ROLE_PODEPISUJICI:
        return ucet, None

    if not email_je_volny(sezeni, email, krome_id=ucet.id):
        raise ValueError("Tenhle e-mail už používá jiný účet.")
    ucet.email = email

    zaznam = posli_pozvanku(sezeni, ucet, pozval)
    return ucet, zaznam
