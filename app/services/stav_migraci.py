"""Kontrola, ze databaze je na posledni migraci Alembicu.

Proc: kdyz po aktualizaci kodu nikdo nespustil `alembic upgrade head`, chybi v databazi
nove sloupce a prvni stranka, ktera je cte, skonci chybou 500 "Neco se pokazilo" - bez
napovedy, co s tim. Appka proto pri startu porovna revizi v tabulce `alembic_version`
s `head` ve slozce `migrations/`, rozdil zapise do logu a ukaze ho spravci v appce.

Kontrola jen cte. Databazi NIKDY nemigruje sama - jsou v ni hotova vyuctovani a pred
migraci si Honza ma zkopirovat `data/akce.db`. Vysledek se spocita jednou pri startu
a drzi se v pameti, takze bezne pozadavky nestoji nic navic. Po `upgrade head` staci
appku restartovat (stejne by bezel stary kod).
"""

import logging

from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text

from app.config import KOREN_PROJEKTU

log = logging.getLogger(__name__)

# Text pro spravce v appce (jedna veta) a pro log serveru. Zadne osobni udaje,
# jen cislo revize (to je nahodny retezec z nazvu migrace).
PRIKAZ = "uv run alembic upgrade head"
HLASKA_APP = (
    "Databáze není na poslední migraci, stránky se mohou rozbít. "
    f"Nejdřív si zkopíruj data/akce.db jako zálohu, pak spusť {PRIKAZ} a restartuj appku."
)

# Vysledek kontroly ze startu; None = vse v poradku (nebo se jeste nekontrolovalo).
_varovani: str | None = None


def hlavy_migraci() -> set[str]:
    """Posledni revize (head) ze skriptu ve slozce `migrations/`."""
    return set(ScriptDirectory(str(KOREN_PROJEKTU / "migrations")).get_heads())


def revize_databaze(engine: Engine) -> set[str] | None:
    """Revize zapsane v databazi; None, kdyz tabulka `alembic_version` neexistuje.
    Jen cte."""
    with engine.connect() as spojeni:
        if not inspect(spojeni).has_table("alembic_version"):
            return None
        radky = spojeni.execute(text("SELECT version_num FROM alembic_version")).scalars()
        return set(radky)


def zjisti_rozdil(engine: Engine) -> str | None:
    """Popis rozdilu mezi databazi a posledni migraci pro log, nebo None, kdyz sedi
    (nebo je databaze uplne prazdna - tu hlasi uz kontrola tabulky `uzivatel`)."""
    nove = hlavy_migraci()
    v_databazi = revize_databaze(engine)
    if v_databazi is None:
        with engine.connect() as spojeni:
            if not inspect(spojeni).get_table_names():
                return None
        return f"databáze nemá záznam o žádné migraci (poslední je {', '.join(sorted(nove))})"
    if v_databazi == nove:
        return None
    return (
        f"databáze je na migraci {', '.join(sorted(v_databazi)) or '(žádná)'}, "
        f"poslední je {', '.join(sorted(nove))}"
    )


def zkontroluj_pri_startu(engine: Engine) -> str | None:
    """Spocita a zapamatuje varovani; nesmi shodit start, proto chyby jen zaloguje.
    Vraci text varovani pro spravce (stejny jako `varovani_migraci`)."""
    global _varovani
    _varovani = None
    try:
        rozdil = zjisti_rozdil(engine)
    except Exception:
        log.exception("Kontrola migrací databáze se nepovedla")
        return None
    if rozdil is not None:
        _varovani = HLASKA_APP
        log.warning(
            "Databáze není na poslední migraci (%s). Nejdřív zkopíruj data/akce.db jako "
            "zálohu, pak spusť: %s a appku restartuj. Appka databázi sama nemigruje.",
            rozdil,
            PRIKAZ,
        )
    return _varovani


def varovani_migraci() -> str | None:
    """Varovani pro spravce spocitane pri startu, nebo None (levne, jen cte promennou)."""
    return _varovani
