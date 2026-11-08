"""Prikazy pro prikazovou radku (`uv run spravce`).

Ctou klavesnici a pisou do terminalu, proto nesdili soubor s `app/auth.py`
(prihlaseni a opravneni) - ten nesmi nic vedet o konzoli.
"""

from __future__ import annotations

import getpass

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.auth import Aktor
from app.db import Sezeni
from app.models import ROLE_SPRAVCE, Uzivatel
from app.services.uzivatele import chyba_hesla, nastav_heslo, over_login


def vytvor_spravce() -> None:
    """Pomocnik pro prikaz `uv run spravce`.

    Nahrazuje puvodni `uv run heslo`. Driv appka mela jeden ucet v .env a
    prikaz jen vypsal radky k rucnimu zkopirovani; ucty ted ziji v databazi,
    takze prikaz rovnou zalozi nebo obnovi spravcovsky ucet - hodi se
    i na zachranu, kdyz uz se nikdo neprihlasi (napr. posledni spravce si
    zapomel heslo).
    """
    login = input("Přihlašovací jméno správce [honza]: ").strip().lower() or "honza"
    chyba = over_login(login)
    if chyba:
        print(f"\n{chyba}")
        return

    heslo = getpass.getpass("Nové heslo: ")
    znovu = getpass.getpass("Heslo ještě jednou: ")
    chyba = chyba_hesla(heslo, znovu)
    if chyba:
        print(f"\n{chyba}")
        return

    try:
        with Sezeni() as sezeni:
            # Bez aktera se automaticky nic neloguje (app/services/log_cinnosti/zapisovani.py)
            # - z prikazove radky ale k databazi pristupuje clovek u serveru,
            # ne anonym, tak at to v logu zustane videt.
            sezeni.info["aktor"] = Aktor(None, "příkazová řádka")
            uzivatel = sezeni.scalar(select(Uzivatel).where(Uzivatel.login == login))
            if uzivatel is None:
                # Cerstvy objekt jeste nema `verze_hesla` - sloupcovy default=1
                # se dosadi az pri flush. `nastav_heslo` na to bere
                # ohled, `+= 1` by tu spadlo na `TypeError`.
                uzivatel = Uzivatel(login=login, jmeno=login.capitalize())
                sezeni.add(uzivatel)
                zprava = f"Účet „{login}“ založen jako správce."
            else:
                zprava = f"Účet „{login}“ obnoven jako správce."
            nastav_heslo(uzivatel, heslo)
            uzivatel.role = ROLE_SPRAVCE
            uzivatel.aktivni = True
            uzivatel.nekdy_aktivni = True
            sezeni.commit()
    except OperationalError:
        print(
            "\nDatabáze ještě nemá tabulku uzivatel. Nejdřív spusť:\n  uv run alembic upgrade head"
        )
        return

    print(f"\n{zprava} Přihlas se novým heslem na /prihlaseni.")
