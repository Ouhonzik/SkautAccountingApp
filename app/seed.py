"""Ukazkova data pro vyvoj a rucni overovani.

    DATA_DIR=data-vyvoj uv run python -m app.seed

Vyrobi novou databazi s vymyslenymi vyuctovanimi, aby se appka dala zkouset
(mazani akce, import ucastniku, obnova ze zalohy...) bez rizika pro ostra data
deti. Jmena, ucty i uctenky jsou smyslene; obrazky uctenek jsou bile listy
s nápisem UKAZKA.

Pojistka: seed se nikdy nepusti do vychozi slozky `data/` (ta je ostra) a do
databaze, ve ktere uz je aspon jedna akce nebo ucet. Smi jen do prazdne slozky
zadane pres `DATA_DIR`.

Heslo vsech ukazkovych uctu je z promenne prostredi `SEED_HESLO`, jinak se
vygeneruje nahodne a vypise se jednou na konci.
"""

import datetime as dt
import os
import secrets
import sqlite3
import sys
import unicodedata
import uuid
from decimal import Decimal
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import KOREN_PROJEKTU, nastaveni
from app.models import (
    DRUH_JINE,
    DRUH_PALIVO,
    PROSTREDEK_AUTO,
    PROSTREDEK_VLAK,
    ROLE_PODEPISUJICI,
    ROLE_REVIZOR,
    ROLE_SPRAVCE,
    ROLE_UZIVATEL,
    SMER_VYDEJ,
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
    Uzivatel,
    Vozidlo,
)
from app.models.konstanty import PEVNE_KATEGORIE
from app.services import cestaky
from app.services.uzivatele import chyba_hesla, nastav_heslo

# Devet pevnych kategorii (`PEVNE_KATEGORIE`) zaklada uz migrace, kterou seed pousti jako
# prvni; tady se jen najdou podle klice (a kdyby chybely, zalozi se stejne).
KATEGORIE = list(PEVNE_KATEGORIE)

UCTY: list[tuple[str, str, str]] = [
    ("spravce", "Správce (ukázka)", ROLE_SPRAVCE),
    ("uzivatel", "Uživatel (ukázka)", ROLE_UZIVATEL),
    ("revizor", "Revizor (ukázka)", ROLE_REVIZOR),
    ("podepisujici", "Podepisující (ukázka)", ROLE_PODEPISUJICI),
]


class SeedOdmitnut(Exception):
    """Cil neni vhodny pro ukazkova data; text je hlaska pro Honzu."""


def over_cil(slozka: Path) -> None:
    """Pusti jen prazdnou slozku (nebo databazi bez akci a uctu), nikdy ostrou `data/`."""
    if slozka.resolve() == (KOREN_PROJEKTU / "data").resolve():
        raise SeedOdmitnut(
            f"Složka {slozka} je ostrá — jsou v ní skutečná vyúčtování a údaje dětí. "
            "Ukázková data patří do jiné složky, např. DATA_DIR=data-vyvoj."
        )
    soubor = slozka / "akce.db"
    if soubor.is_file() and _ma_data(soubor):
        raise SeedOdmitnut(
            f"Databáze {soubor} je neprázdná (obsahuje akce nebo účty). "
            "Seed jí nic nepřidá; použij prázdnou složku."
        )


def _ma_data(soubor: Path) -> bool:
    """Je v databazi aspon jedna akce nebo jeden ucet? Jen cte, nic nemeni."""
    try:
        spojeni = sqlite3.connect(f"{soubor.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return True
    try:
        for tabulka in ("akce", "uzivatel"):
            try:
                if spojeni.execute(f"SELECT 1 FROM {tabulka} LIMIT 1").fetchone():
                    return True
            except sqlite3.OperationalError:
                continue  # tabulka tam jeste neni
    except sqlite3.DatabaseError:
        return True  # nejde precist - radsi nesahat
    finally:
        spojeni.close()
    return False


# --- vyroba dat ---


def _bez_diakritiky(text: str) -> str:
    rozlozeny = unicodedata.normalize("NFD", text)
    return "".join(znak for znak in rozlozeny if not unicodedata.combining(znak))


def _uctenka_na_disk(slozka_uploadu: Path, obchodnik: str, castka: Decimal, den: dt.date) -> str:
    """Bily list A6 s napisem UKAZKA, obchodnikem a castkou. Vrati nahodne jmeno
    souboru - stejne jako ho dava nahrani v app/routes/skeny.py."""
    obraz = Image.new("RGB", (420, 595), "white")
    kreslic = ImageDraw.Draw(obraz)
    try:
        pismo = ImageFont.load_default(size=26)
    except OSError, TypeError:
        pismo = ImageFont.load_default()
    kreslic.rectangle((10, 10, 409, 584), outline="black", width=3)
    # Vychozi pismo Pillow nema hacky ani carky (chybejici znak by byl ctvereček),
    # a obrazek je jen kulisa - kresli se proto bez diakritiky.
    kreslic.text((40, 60), "UKAZKA", fill="black", font=pismo)
    kreslic.text((40, 160), _bez_diakritiky(obchodnik), fill="black", font=pismo)
    kreslic.text((40, 260), f"Celkem {castka} Kc", fill="black", font=pismo)
    kreslic.text((40, 360), den.strftime("%d. %m. %Y"), fill="black", font=pismo)
    slozka_uploadu.mkdir(parents=True, exist_ok=True)
    nazev = f"{uuid.uuid4().hex}.png"
    obraz.save(slozka_uploadu / nazev, "PNG")
    return nazev


def naplnit(sezeni: Session, slozka_uploadu: Path, heslo: str) -> None:
    """Zalozi vsechna ukazkova data. Necommituje - to dela volajici.

    Bez `Session.info["aktor"]` se do logu cinnosti nic nezapisuje, takze
    ukazkova databaze zacina s prazdnym logem.
    """
    kategorie = _kategorie(sezeni)
    lide = _lide(sezeni)
    auto = _vozidlo(sezeni)
    _ucty(sezeni, heslo)
    _tabor(sezeni, slozka_uploadu, kategorie, lide, auto)
    _vikendovka(sezeni, slozka_uploadu, kategorie, lide)
    sezeni.flush()


def _kategorie(sezeni: Session) -> dict[str, Kategorie]:
    hotove = {k.klic: k for k in sezeni.scalars(select(Kategorie)) if k.klic}
    for poradi, (klic, nazev) in enumerate(KATEGORIE):
        if klic not in hotove:
            hotove[klic] = Kategorie(klic=klic, nazev=nazev, poradi=poradi)
            sezeni.add(hotove[klic])
    sezeni.flush()
    return hotove


def _lide(sezeni: Session) -> dict[str, Clovek]:
    lide = {
        "jana": Clovek(
            jmeno="Jana Nováková",
            adresa="Ukázková 12, 602 00 Brno",
            kontakt="jana@example.invalid",
            poradi=0,
        ),
        "petr": Clovek(jmeno="Petr Svoboda", poradi=1),
        "marie": Clovek(jmeno="Marie Dvořáková", poradi=2),
        "tomas": Clovek(jmeno="Tomáš Černý", poradi=3),
    }
    sezeni.add_all(lide.values())
    sezeni.flush()
    return lide


def _vozidlo(sezeni: Session) -> Vozidlo:
    auto = Vozidlo(nazev="Škoda Octavia (ukázka)", spz="1AB 2345", spotreba=Decimal("6.10"))
    sezeni.add(auto)
    sezeni.flush()
    return auto


def _ucty(sezeni: Session, heslo: str) -> None:
    for login, jmeno, role in UCTY:
        ucet = Uzivatel(login=login, jmeno=jmeno, role=role, aktivni=True, nekdy_aktivni=True)
        nastav_heslo(ucet, heslo)
        sezeni.add(ucet)
    sezeni.flush()


def _sken(
    sezeni: Session,
    akce: Akce,
    slozka_uploadu: Path,
    poradi: int,
    obchodnik: str,
    castka: Decimal,
    den: dt.date,
    **pole,
) -> Sken:
    sken = Sken(
        akce=akce,
        soubor=_uctenka_na_disk(slozka_uploadu, obchodnik, castka, den),
        puvodni_nazev=f"ukazka-{poradi + 1}.png",
        nazev=pole.pop("nazev", obchodnik),
        obchodnik=obchodnik,
        datum=den,
        castka=castka,
        jistota=pole.pop("jistota", "vysoka"),
        zkontrolovano=pole.pop("zkontrolovano", True),
        poradi=poradi,
        **pole,
    )
    sezeni.add(sken)
    return sken


UCASTNICI: list[tuple[str, str, str | None, dt.date | None, str, str]] = [
    # jmeno, prijmeni, prezdivka, narozeni, mesto, jednotka
    ("Anička", "Králová", "Anča", dt.date(2015, 3, 14), "Brno", "Vlčata"),
    ("Matyáš", "Procházka", "Maty", dt.date(2014, 8, 2), "Brno", "Vlčata"),
    ("Tereza", "Veselá", None, dt.date(2013, 11, 30), "Brno", "Světlušky"),
    ("Jakub", "Horák", "Kuba", dt.date(2013, 5, 9), "Šlapanice", "Skauti"),
    ("Eliška", "Němcová", "Ela", dt.date(2012, 12, 1), "Brno", "Skautky"),
    ("David", "Marek", None, dt.date(2012, 7, 21), "Modřice", "Skauti"),
    ("Lucie", "Pospíšilová", "Lucka", dt.date(2014, 1, 17), "Brno", "Světlušky"),
    ("Filip", "Kučera", None, dt.date(2015, 9, 5), "Brno", "Vlčata"),
    ("Jana", "Nováková", "Janka", dt.date(1988, 4, 22), "Brno", "Vedení"),
    ("Petr", "Svoboda", "Péťa", dt.date(1991, 10, 3), "Brno", "Vedení"),
    ("Marie", "Dvořáková", "Maruška", dt.date(1999, 6, 15), "Brno", "Vedení"),
    ("Ondřej", "Beneš", "Ondra", None, "Brno", "Vedení"),
]


def _ucastnici(sezeni: Session, akce: Akce) -> list[Ucastnik]:
    vysledek = []
    for poradi, (jmeno, prijmeni, prezdivka, narozeni, mesto, jednotka) in enumerate(UCASTNICI):
        ucastnik = Ucastnik(
            akce=akce,
            poradi=poradi,
            jmeno=jmeno,
            prijmeni=prijmeni,
            prezdivka=prezdivka,
            ulice=f"Ukázková {poradi + 1}",
            mesto=mesto,
            psc="602 00",
            datum_narozeni=narozeni,
            jednotka=jednotka,
        )
        # platby: deti platily hotove nebo prevodem, vedouci nic
        if poradi < 8:
            if poradi % 2 == 0:
                ucastnik.hotove = Decimal(2500)
                ucastnik.hotove_doklad = f"PPD {poradi + 1:02d}"
            else:
                ucastnik.prevodem = Decimal(2500)
                ucastnik.prevodem_doklad = f"VB {poradi + 1:02d}"
        sezeni.add(ucastnik)
        vysledek.append(ucastnik)
    # Jedno dite bylo jen cast tabora - mrizka dochazky ma videt vyjimku.
    vysledek[2].zapis_chybejici(akce.dny[6:10])
    sezeni.flush()
    return vysledek


def _tabor(
    sezeni: Session,
    slozka_uploadu: Path,
    kategorie: dict[str, Kategorie],
    lide: dict[str, Clovek],
    auto: Vozidlo,
) -> Akce:
    od = dt.date(2026, 7, 4)
    akce = Akce(
        nazev="Letní tábor (ukázka)",
        datum_od=od,
        datum_do=od + dt.timedelta(days=9),
        datum_vyuctovani=dt.date(2026, 7, 20),
        prefix_dokladu="T26",
        pokladnik_clovek=lide["jana"],
    )
    sezeni.add(akce)
    sezeni.flush()

    jidlo = Slozka(
        akce=akce,
        nazev=f"{kategorie['jidlo'].nazev} – Jana Nováková",
        kategorie=kategorie["jidlo"],
        clovek=lide["jana"],
        poradi=0,
    )
    material = Slozka(
        akce=akce,
        nazev=f"{kategorie['material'].nazev} – Petr Svoboda",
        kategorie=kategorie["material"],
        clovek=lide["petr"],
        poradi=1,
    )
    jizdne = Slozka(
        akce=akce,
        nazev=f"{kategorie['jizdne'].nazev} – Marie Dvořáková",
        kategorie=kategorie["jizdne"],
        clovek=lide["marie"],
        poradi=2,
    )
    sezeni.add_all([jidlo, material, jizdne])
    sezeni.flush()

    def d(den: int) -> dt.date:
        return od + dt.timedelta(days=den)

    def sken(poradi, obchodnik, castka, den, slozka, kat, clovek, **pole):
        return _sken(
            sezeni,
            akce,
            slozka_uploadu,
            poradi,
            obchodnik,
            Decimal(castka),
            d(den),
            slozka=slozka,
            kategorie=kategorie[kat],
            clovek=lide[clovek],
            **pole,
        )

    sken(0, "Albert", "1240.50", 0, jidlo, "jidlo", "jana")
    sken(1, "Lidl", "2315.00", 3, jidlo, "jidlo", "jana")
    # Cteni AI si tu nebylo jisty - uctenka ceka na kontrolu.
    sken(2, "Penny", "876.20", 5, jidlo, "jidlo", "jana", zkontrolovano=False, jistota="nizka")
    hornbach = sken(3, "Hornbach", "1499.00", 1, material, "material", "petr")
    sken(4, "Papírnictví U Lva", "342.00", 2, material, "material", "petr", dary_precteny=True)
    rozdelena = sken(
        5, "Albert", "1000.00", 7, jidlo, "jidlo", "jana", nazev="Albert (jídlo + drogerie)"
    )
    sken(6, "České dráhy", "218.00", 9, jizdne, "jizdne", "marie", zkontrolovano=False)
    sezeni.flush()

    sezeni.add_all(
        [
            CastSkenu(
                sken=rozdelena,
                kategorie=kategorie["jidlo"],
                castka=Decimal("700.00"),
                popis="potraviny",
                poradi=0,
            ),
            CastSkenu(
                sken=rozdelena,
                kategorie=kategorie["material"],
                castka=Decimal("300.00"),
                popis="toaletní papír, šampon",
                poradi=1,
            ),
        ]
    )
    # Priloha pripnuta k uctence: dodaci list se vypise na dokladu pod ni.
    sken(
        7,
        "Dodací list Hornbach",
        "0.00",
        1,
        material,
        "material",
        "petr",
        nazev="Dodací list",
        druh=DRUH_JINE,
        je_priloha=True,
        priloha_k=hornbach,
    )
    # Palivova uctenka k cestaku: priloha, do souctu nevstupuje.
    palivo = sken(
        8,
        "Shell",
        "1450.00",
        1,
        None,
        "cestak",
        "petr",
        nazev="Shell – palivo",
        druh=DRUH_PALIVO,
        je_priloha=True,
        cena_za_litr=Decimal("38.90"),
    )
    sezeni.flush()

    # příjmy
    sezeni.add_all(
        [
            Prijem(
                akce=akce,
                popis="Účastnické poplatky",
                castka=Decimal("20000.00"),
                clovek=lide["jana"],
                datum=dt.date(2026, 6, 20),
                poradi=0,
            ),
            Prijem(
                akce=akce,
                popis="Příspěvek oddílu",
                castka=Decimal("5000.00"),
                clovek=lide["jana"],
                datum=dt.date(2026, 6, 25),
                poradi=1,
            ),
        ]
    )

    ucastnici = _ucastnici(sezeni, akce)

    # Vratka: radek knihy; castka u ucastnika je jejich soucet (viz Ucastnik.vratka).
    vratka = ucastnici[1]
    sezeni.add(
        PokladniRadek(
            akce=akce,
            ucastnik=vratka,
            datum=d(10),
            ucel=f"Vratka – {vratka.jmeno} {vratka.prijmeni}",
            smer=SMER_VYDEJ,
            castka=Decimal("300.00"),
            poradi=0,
        )
    )

    # Obdarovani opsani z ucastniku, 5 veci z uctenky z papirnictvi; jedna zustava v zasobniku.
    obdarovani = [
        Obdarovany.z_ucastnika(ucastnici[i], poradi) for poradi, i in enumerate((0, 1, 2))
    ]
    sezeni.add_all(obdarovani)
    sezeni.flush()
    papirnictvi = akce.skeny[4]
    veci = [
        ("míč", obdarovani[0]),
        ("pexeso", obdarovani[0]),
        ("odznak", obdarovani[1]),
        ("šátek", obdarovani[2]),
        ("skládačka", None),
    ]
    for poradi, (nazev, komu) in enumerate(veci):
        sezeni.add(Dar(akce=akce, sken=papirnictvi, obdarovany=komu, nazev=nazev, poradi=poradi))

    # Cestak se dvema jizdami (auto na km, vlak s jizdnym), zapnuty do vydaju.
    cestak = Cestak(
        akce=akce,
        ucel_cesty="Nákup materiálu a odvoz stanů",
        misto="Brno – Hornbach",
        cestujici=lide["petr"],
        adresa="Ukázková 3, 602 00 Brno",
        kontakt="petr@example.invalid",
        vozidlo=auto,
        spotreba=auto.spotreba,
        palivova_uctenka=palivo,
        cena_za_litr=Decimal("38.90"),
        opotrebeni=Decimal("1.50"),
        poradi=0,
    )
    cestak.jizdy = [
        Jizda(
            datum=d(1),
            odkud="Brno",
            kam="Hornbach Brno-Černovice",
            prostredek=PROSTREDEK_AUTO,
            km=42,
            poradi=0,
        ),
        Jizda(
            datum=d(11),
            odkud="Vranov",
            kam="Brno",
            prostredek=PROSTREDEK_VLAK,
            jizdne=Decimal("218.00"),
            poradi=1,
        ),
    ]
    sezeni.add(cestak)
    sezeni.flush()
    cestak.pokladni_radek = PokladniRadek(
        akce=akce,
        smer=SMER_VYDEJ,
        castka=cestaky.celkem(cestak),
        ucel=cestaky.ucel_v_knize(cestak),
        datum=cestaky.datum_konce(cestak),
        poradi=1,
    )
    sezeni.flush()
    return akce


def _vikendovka(
    sezeni: Session,
    slozka_uploadu: Path,
    kategorie: dict[str, Kategorie],
    lide: dict[str, Clovek],
) -> Akce:
    od = dt.date(2026, 3, 6)
    akce = Akce(
        nazev="Víkendovka (ukázka)",
        datum_od=od,
        datum_do=od + dt.timedelta(days=2),
        datum_vyuctovani=dt.date(2026, 3, 15),
        stav=STAV_UZAVRENA,
    )
    sezeni.add(akce)
    sezeni.flush()
    jidlo = Slozka(
        akce=akce,
        nazev=f"{kategorie['jidlo'].nazev} – Jana Nováková",
        kategorie=kategorie["jidlo"],
        clovek=lide["jana"],
    )
    ubytovani = Slozka(
        akce=akce,
        nazev=f"{kategorie['ubytovani'].nazev} – Tomáš Černý",
        kategorie=kategorie["ubytovani"],
        clovek=lide["tomas"],
        poradi=1,
    )
    sezeni.add_all([jidlo, ubytovani])
    sezeni.flush()
    for poradi, (obchodnik, castka, slozka, kat, clovek) in enumerate(
        [
            ("Kaufland", "1680.00", jidlo, "jidlo", "jana"),
            ("Albert", "720.40", jidlo, "jidlo", "jana"),
            ("Chata Pod Lesem", "3000.00", ubytovani, "ubytovani", "tomas"),
        ]
    ):
        _sken(
            sezeni,
            akce,
            slozka_uploadu,
            poradi,
            obchodnik,
            Decimal(castka),
            od + dt.timedelta(days=poradi % 3),
            slozka=slozka,
            kategorie=kategorie[kat],
            clovek=lide[clovek],
        )
    sezeni.add(
        Prijem(
            akce=akce,
            popis="Účastnické poplatky",
            castka=Decimal("6000.00"),
            clovek=lide["tomas"],
            datum=dt.date(2026, 3, 1),
        )
    )
    return akce


# --- spusteni z prikazove radky ---


def _vytvor_databazi() -> None:
    """`alembic upgrade head` nad databazi z DATA_DIR (Alembic bere pripojeni z app.db)."""
    from alembic import command
    from alembic.config import Config

    # Migrace b42fe20a9964 by jinak zalozila ucet z APP_PASSWORD_HASH v .env,
    # tedy skutecny Honzuv ucet - do ukazkove databaze nepatri.
    nastaveni.app_password_hash = ""
    command.upgrade(Config(str(KOREN_PROJEKTU / "alembic.ini")), "head")


def hlavni() -> int:
    # Nektere terminaly Windows neumi vsechna cesky znaky; seed kvuli tomu nesmi spadnout.
    for proud in (sys.stdout, sys.stderr):
        prenastav = getattr(proud, "reconfigure", None)
        if prenastav is not None:
            prenastav(errors="replace")
    try:
        over_cil(nastaveni.data_dir)
    except SeedOdmitnut as chyba:
        print(f"\nSeed odmítnut: {chyba}", file=sys.stderr)
        return 1

    zadane = os.environ.get("SEED_HESLO")
    heslo = zadane or secrets.token_urlsafe(12)
    problem = chyba_hesla(heslo, heslo)
    if problem:
        print(f"\nSEED_HESLO se nedá použít: {problem}", file=sys.stderr)
        return 1

    from app.db import Sezeni  # az tady: import zaklada slozky a engine

    nastaveni.zaloz_slozky()
    _vytvor_databazi()
    with Sezeni() as sezeni:
        naplnit(sezeni, nastaveni.slozka_uploadu, heslo)
        sezeni.commit()

    print(f"\nUkázková data jsou v {nastaveni.data_dir}.")
    print("Účty: " + ", ".join(login for login, _, _ in UCTY))
    if zadane:
        print("Heslo: z proměnné SEED_HESLO.")
    else:
        print(f"Heslo (zobrazí se jen teď): {heslo}")
    print(f"Spuštění appky: DATA_DIR={nastaveni.data_dir} uv run app")
    return 0


if __name__ == "__main__":
    sys.exit(hlavni())
