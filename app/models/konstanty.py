"""Ciselnikove konstanty (druhy skenu, stavy, role, druhy cinnosti, ucely e-mailu)."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

# --- Ciselniky (jako obycejne retezce, ne enumy - snadneji se ctou v databazi) ---

DRUH_UCTENKA = "uctenka"
DRUH_ARCH_JIZDENEK = "arch_jizdenek"
DRUH_PALIVO = "palivo"
DRUH_CESTOVNI_PRIKAZ = "cestovni_prikaz"
DRUH_JINE = "jine"
# Vsechny druhy skenu. `Literal` ve VysledekCteni (app/services/ocr.py) se z nich
# skladat nedá, hlida to test.
DRUHY_SKENU = (DRUH_UCTENKA, DRUH_ARCH_JIZDENEK, DRUH_PALIVO, DRUH_CESTOVNI_PRIKAZ, DRUH_JINE)

# Pevne (systemove) kategorie - devet druhu vydaju strediska (rozhodnuti Honzy). Nejdou
# smazat, prejmenovat ani zmenit jim klic; vlastni kategorie k nim stredisko
# muze dal pridavat. Poradi tady je poradi v ciselniku (poradi 0-8, vlastni az za nimi).
# Klic je stale oznaceni pro vystup AI i otisk kategorie pro obnovu ze zalohy: u
# kategorii, ktere appka znala uz drive, zustal puvodni klic (jidlo, material...),
# nove jsou jen "vybaveni" a "sluzby". Klic je ASCII, malymi pismeny, nejvic
# MAX_DELKA_KLICE_KATEGORII znaku. Nazev se zobrazuje presne takhle (male pocatecni pismeno).
KLIC_VECNE_DARY = "vecne dary"
PEVNE_KATEGORIE: tuple[tuple[str, str], ...] = (
    ("material", "materiál spotřební"),
    ("vybaveni", "vybavení k realizaci akcí/táborů (do 3000 Kč/kus)"),
    (KLIC_VECNE_DARY, "věcné dary (pro činovníky nebo partnerské subjekty)"),
    ("jidlo", "potraviny a stravné"),
    ("cestak", "cestovné (cestovní náhrady)"),
    ("ubytovani", "nájemné a ubytování"),
    ("jizdne", "jízdné/doprava osob a materiálu"),
    ("sluzby", "služby ostatní (vč. účast. poplatků na externí akce)"),
    ("jine", "jiné ostatní náklady"),
)
# Porovnava se bez ohledu na velikost pismen.
PEVNE_KLICE_KATEGORII = frozenset(klic for klic, _ in PEVNE_KATEGORIE)
# Dosavadni nazvy kategorii (bez diakritiky, malymi pismeny), ktere se pri obnove ze
# zalohy hlasi k pevne kategorii (migrace pouziva svoji vlastni kopii seznamu).
STARE_NAZVY_PEVNYCH_KATEGORII = {
    "jidlo": "jidlo",
    "material": "material",
    "ubytovani": "ubytovani",
    "cestak": "cestak",
    "jizdne": "jizdne",
    "jine": "jine",
    "vecne dary": KLIC_VECNE_DARY,
}
# Nejvic znaku, co se vejde do `Kategorie.klic`. SQLite delku sloupce `String(n)`
# nevynucuje, proto ji hlidaji model (`Kategorie`), formular a obnova ze zalohy.
MAX_DELKA_KLICE_KATEGORIE = 30

# Uctenka se rozdeluje po kategoriich az od dvou casti: jedina cast je tataz
# informace jako kategorie uctenky, jen zapsana podruhe. Pravidlo plati pro
# rozpis od AI, pro formular i pro `Sken.je_rozdelena`.
MIN_CASTI_ROZDELENI = 2

STAV_ROZPRACOVANA = "rozpracovana"
STAV_UZAVRENA = "uzavrena"

# Detoden se pocita do 15 let, presne do 14,99 roku (Honza podle hospodare).
# Vek se posuzuje k prvnimu dni akce.
VEK_DITETE = 15
# Nejstarsi datum narozeni, ktere appka prijme. Prekliep v roce
# (2062 misto 1962) nebo dvouciferny rok z exportu by jinak z dospeleho udelal
# "dite z budoucnosti" s detodny, ktere jdou do vyuctovani.
NEJDRIVE_NAROZENI = dt.date(1900, 1, 1)


def dnes() -> dt.date:
    """Dnesni den v Praze (server bezi v UTC, po pulnoci by jinak byl o den pozadu)."""
    return dt.datetime.now(ZoneInfo("Europe/Prague")).date()


def pripustne_narozeni(datum: dt.date, nejpozdeji: dt.date) -> bool:
    """Datum narozeni ma smysl, kdyz neni pred rokem 1900 a ani po `nejpozdeji`
    (zacatek akce, nebo dnesek)."""
    return NEJDRIVE_NAROZENI <= datum <= nejpozdeji


# Smer pohybu v pokladni knize. Dva sloupce vzoru (Prijem/Vydej) drzime jako
# jednu hodnotu, aby neslo omylem vyplnit obojí naráz.
SMER_PRIJEM = "prijem"
SMER_VYDEJ = "vydej"

# Dopravni prostredek u jedne jizdy na cestovnim prikazu. U vlastniho auta
# a motocyklu se zadavaji kilometry a nahrada se z nich spocita; u vlaku
# a autobusu se zadava rovnou zaplacena cena jizdenky - proto ma sloupec
# na papire hlavicku "Pocet km" i "Cena".
PROSTREDEK_AUTO = "auto"
PROSTREDEK_MOTOCYKL = "motocykl"
PROSTREDEK_AUTOBUS = "autobus"
PROSTREDEK_VLAK = "vlak"
NAZVY_PROSTREDKU = {
    PROSTREDEK_AUTO: "auto vlastní",
    PROSTREDEK_MOTOCYKL: "motocykl vlastní",
    PROSTREDEK_AUTOBUS: "autobus",
    PROSTREDEK_VLAK: "vlak",
}
PROSTREDKY_NA_KM = (PROSTREDEK_AUTO, PROSTREDEK_MOTOCYKL)

# Role uzivatele. Spravce vidi a resi vsechno vcetne spravy uctu, uzivatel
# beznou praci s akcemi, revizor smi jen cist a stahovat vystupy. Podepisujici
# nema k akcim pristup vubec - vidi jen sve doklady k podpisu (e-podpisy, zatim jen priprava).
ROLE_SPRAVCE = "spravce"
ROLE_UZIVATEL = "uzivatel"
ROLE_REVIZOR = "revizor"
ROLE_PODEPISUJICI = "podepisujici"
ROLE = (ROLE_SPRAVCE, ROLE_UZIVATEL, ROLE_REVIZOR, ROLE_PODEPISUJICI)
NAZVY_ROLI = {
    ROLE_SPRAVCE: "Správce",
    ROLE_UZIVATEL: "Uživatel",
    ROLE_REVIZOR: "Revizor (jen čtení)",
    ROLE_PODEPISUJICI: "Podepisující (jen podpisy dokladů)",
}

# Typy zaznamu v logu cinnosti (app/services/log_cinnosti/zapisovani.py).
CINNOST_PRIHLASENI = "prihlaseni"
CINNOST_ODHLASENI = "odhlaseni"
CINNOST_PRIHLASENI_NEUSPESNE = "prihlaseni_neuspesne"
CINNOST_PRIDANI = "pridani"
CINNOST_UPRAVA = "uprava"
CINNOST_SMAZANI = "smazani"
CINNOST_OBNOVA = "obnova_zalohy"
CINNOST_REGISTRACE = "registrace"
CINNOST_RESET_HESLA = "reset_hesla"
CINNOST_RESET_HESLA_ZADOST = "reset_hesla_zadost"
CINNOST_POZVANKA = "pozvanka"
CINNOST_STAZENI = "stazeni"
# E-mail se neodeslal kvuli limitu na ucet nebo na celou appku.
CINNOST_EMAIL_OMEZEN = "email_omezen"


# Ucel odeslaneho e-mailu - podle nej se vybira sablona a filtruje se v `/log`.
UCEL_POZVANKA = "pozvanka"
UCEL_RESET_HESLA = "reset_hesla"
UCEL_ZMENA_EMAILU = "zmena_emailu"
# Zprava bez odkazu na puvodni adresu: e-mail uctu se zmenil.
UCEL_UPOZORNENI_EMAILU = "upozorneni_emailu"
# Nocni kopie databaze selhala; zprava jde spravcum.
UCEL_ZALOHA_SELHALA = "zaloha_selhala"
# Upozorneni spravcum na chybu 500 (app/chyby.py), bez dat z pozadavku.
UCEL_CHYBA_APPKY = "chyba_appky"

STAV_EMAILU_ODESLANO = "odeslano"
STAV_EMAILU_SELHALO = "selhalo"
STAV_EMAILU_LOKALNE = "lokalne"
# Nad limitem odesilani: zprava se neodeslala a zaznam se neulozil, jen se
# vratil volajicimu, aby vedel, co ukazat.
STAV_EMAILU_OMEZENO = "omezeno"
