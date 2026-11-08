"""Nocni kopie databaze na serveru.

Spousti ji zalohovaci skript na serveru z cronu: `python -m app.services.zaloha_databaze`.

Proc: Hetzner Backups kopiruje cely disk za behu a mohl by zachytit databazi
uprostred zapisu. Tahle kopie je konzistentni snimek (zalohovaci funkce SQLite,
stejna jako `sqlite3 .backup`), po vyrobeni se zkontroluje a teprve pak dostane
konecne jmeno - nejnovejsi `akce-*.db` je tedy vzdy overena. Diky ni jde vratit
jen databaze, bez vraceni celeho serveru.

Kopie obsahuje plne udaje deti: slozka i soubory jsou jen pro majitele a po
14 dnech se mazou. Stare kopie se mazou az po uspesne nove, selhani je nesmaze.
Selhani vzdy konci e-mailem spravcum (bez podrobnosti) a v appce se spravci
ukaze varovani, kdyz je nejnovejsi kopie starsi nez 2 dny (`varovani_zalohy`).
"""

import datetime as dt
import logging
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cas import dnes
from app.config import nastaveni
from app.formaty import mnozne
from app.models import ROLE_SPRAVCE, UCEL_ZALOHA_SELHALA, Uzivatel
from app.services import posta

log = logging.getLogger(__name__)

PREDPONA = "akce-"
UCHOVAT_DNI = 14
VAROVAT_PO_DNECH = 2


class ChybaKopie(Exception):
    """Kopie vznikla, ale nepresla kontrolou."""


def _dnes() -> dt.date:
    return dnes()


def _datum_kopie(cesta: Path) -> dt.date | None:
    """Datum z nazvu `akce-RRRR-MM-DD.db`; cizi soubory vrati None a nesahne se na ne."""
    if not (cesta.name.startswith(PREDPONA) and cesta.suffix == ".db"):
        return None
    try:
        return dt.date.fromisoformat(cesta.stem[len(PREDPONA) :])
    except ValueError:
        return None


def zalohuj(zdroj: Path, slozka: Path, dnes: dt.date | None = None) -> Path:
    """Vyrobi overenou kopii `zdroj` do `slozka/akce-RRRR-MM-DD.db`, smaze kopie
    starsi nez 14 dni a vrati cestu k nove kopii. Pri jakekoli chybe vyhodi
    vyjimku a po sobe nic nenecha (dobre stare kopie zustanou)."""
    dnes = dnes or _dnes()
    slozka.mkdir(mode=0o700, parents=True, exist_ok=True)
    cil = slozka / f"{PREDPONA}{dnes.isoformat()}.db"
    rozdelana = slozka / f".{cil.name}.tmp"
    rozdelana.unlink(missing_ok=True)
    try:
        # mode=ro: chybejici databaze je chyba, nevznikne prazdny soubor.
        with (
            closing(sqlite3.connect(f"{zdroj.as_uri()}?mode=ro", uri=True, timeout=30)) as ze,
            closing(sqlite3.connect(rozdelana)) as do,
        ):
            ze.backup(do)
            vysledek = do.execute("PRAGMA integrity_check").fetchall()
        if vysledek != [("ok",)]:
            raise ChybaKopie("Kopie nepresla kontrolou integrity.")
        os.chmod(rozdelana, 0o600)
        os.replace(rozdelana, cil)
    except BaseException:
        rozdelana.unlink(missing_ok=True)
        raise
    _smaz_stare(slozka, dnes)
    return cil


def _smaz_stare(slozka: Path, dnes: dt.date) -> None:
    hranice = dnes - dt.timedelta(days=UCHOVAT_DNI)
    for soubor in slozka.iterdir():
        den = _datum_kopie(soubor)
        if den is not None and den < hranice:
            soubor.unlink()


def stari_posledni_kopie(slozka: Path, dnes: dt.date | None = None) -> int | None:
    """Stari nejnovejsi kopie ve dnech, nebo None, kdyz zadna neni."""
    dnes = dnes or _dnes()
    dny = [d for s in slozka.glob(f"{PREDPONA}*.db") if (d := _datum_kopie(s)) is not None]
    return (dnes - max(dny)).days if dny else None


def varovani_zalohy(dnes: dt.date | None = None) -> str | None:
    """Text varovani pro spravce, kdyz nocni kopie chybi nebo je starsi nez 2 dny.
    Mimo server (vyvoj) vzdy None - na vyvojovem pocitaci zadna kopie nevznika."""
    if not nastaveni.je_server:
        return None
    stari = stari_posledni_kopie(nastaveni.data_dir / "zalohy", dnes)
    napoveda = "Podrobnosti: journalctl -t uctovani-zaloha (na serveru jako root)."
    if stari is None:
        return f"Noční kopie databáze: zatím žádná neexistuje. {napoveda}"
    if stari > VAROVAT_PO_DNECH:
        dni = mnozne(stari, "den", "dny", "dní")
        return f"Noční kopie databáze je stará {stari} {dni}, záloha asi nefunguje. {napoveda}"
    return None


def oznam_selhani(sezeni: Session) -> int:
    """Posle e-mail kazdemu aktivnimu spravci s adresou; vrati kolik zprav
    zpracovala posta. Text je zamerne bez podrobnosti o chybe."""
    spravci = sezeni.scalars(
        select(Uzivatel).where(
            Uzivatel.role == ROLE_SPRAVCE,
            Uzivatel.aktivni.is_(True),
            Uzivatel.email.is_not(None),
            Uzivatel.email != "",
        )
    ).all()
    for spravce in spravci:
        zprava = posta.Zprava(
            komu=spravce.email or "",
            predmet="Účtování akcí: noční záloha databáze selhala",
            text=(
                "Noční kopie databáze se nepovedla. Dobrá stará kopie zůstala, "
                "ale nová nevznikla.\n\n"
                "Podrobnosti na serveru (jako root): journalctl -t uctovani-zaloha\n"
            ),
        )
        posta.posli(sezeni, zprava, UCEL_ZALOHA_SELHALA, uzivatel_id=spravce.id)
    sezeni.commit()
    return len(spravci)


def main() -> int:
    """Vstup pro cron. Kod 1 = selhani (skript ho preda dal)."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from app.db import Sezeni  # az tady: import databaze zaklada slozky a engine

    try:
        kopie = zalohuj(nastaveni.soubor_databaze, nastaveni.data_dir / "zalohy")
    except Exception:
        log.exception("Nocni kopie databaze selhala")
        try:
            with Sezeni() as sezeni:
                oznam_selhani(sezeni)
        except Exception:
            log.exception("E-mail o selhani zalohy se nepodarilo poslat")
        return 1
    log.info("Kopie databaze ulozena: %s (%d kB)", kopie.name, kopie.stat().st_size // 1024)
    return 0


if __name__ == "__main__":
    sys.exit(main())
