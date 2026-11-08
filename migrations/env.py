"""Nastaveni Alembicu - napojeni na nase tabulky a nasi databazi.

Pripojeni si nebereme z alembic.ini, ale z app/config.py, aby bylo na jednom
miste a fungovalo stejne v aplikaci i pri migracich.
"""

from logging.config import fileConfig

from alembic import context

import app.models  # noqa: F401  - import je nutny, jinak Alembic tabulky neuvidi
from app.db import Zaklad, engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Zaklad.metadata


# Cizi klice pridane prikazem ALTER TABLE ADD COLUMN: (tabulka, sloupec). Viz nize.
ZNAME_ROZDILY_CIZICH_KLICU = {
    ("clovek", "uzivatel_id"),
    ("cestak", "puvodni_slozka_palivovky_id"),
    ("prijem", "clovek_id"),
}


def _je_znamy_rozdil(object, type_, reflected) -> bool:
    """Cizi klice, ktere se v databazi a v modelu lisi jen vzhledem.

    Migrace f1a2b3c4d5e6 pridala `clovek.uzivatel_id` (a e5b9c7a2f4d1
    `cestak.puvodni_slozka_palivovky_id`, c9e4b7a1d352 `prijem.clovek_id`) prikazem ALTER TABLE ADD COLUMN,
    takze klic nema jmeno (SQLAlchemy ho cte bez jmena i bez ON DELETE) a model cekal
    jmeno z konvence. Chovani je stejne (ON DELETE SET NULL, hlida to test v
    automaticky test), jenom `alembic check` ohlasi rozdil a kazde dalsi
    `--autogenerate` by pak vyrobil jeho "opravu" - v SQLite jedine pres
    batch_alter_table, tedy zahozeni tabulky, na kterou odkazuji dalsi
    tabulky, a vynulovani jejich vazeb. Prejmenovat klic bez prestavby tabulky nejde.
    """
    if type_ != "foreign_key_constraint":
        return False
    sloupce = [sloupec.name for sloupec in object.columns]
    return len(sloupce) == 1 and (object.parent.name, sloupce[0]) in ZNAME_ROZDILY_CIZICH_KLICU


def include_object(object, name, type_, reflected, compare_to) -> bool:
    return not _je_znamy_rozdil(object, type_, reflected)


def run_migrations_offline() -> None:
    context.configure(
        url=str(engine.url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with engine.connect() as spojeni:
        # Batch rezim tabulku zahodi a zalozi znovu. Se zapnutymi cizimi klici by
        # zahozeni spustilo ON DELETE CASCADE/SET NULL a smazalo by vyuctovani
        # (na kopii ostre databaze tak po `downgrade` zmizelo vsech 79 skenu).
        # Proto je pro migraci vypneme - to je postup, ktery pro prestavbu tabulky
        # doporucuje SQLite. PRAGMA nefunguje uvnitr transakce, takze pred ni.
        # Aplikace sama bezi dal se zapnutymi klici (app/db.py).
        spojeni.exec_driver_sql("PRAGMA foreign_keys=OFF")
        spojeni.commit()
        try:
            _spust_migrace(spojeni)
        finally:
            spojeni.exec_driver_sql("PRAGMA foreign_keys=ON")
            spojeni.commit()


def _spust_migrace(spojeni) -> None:
    context.configure(
        connection=spojeni,
        target_metadata=target_metadata,
        include_object=include_object,
        # SQLite neumi menit sloupce prikazem ALTER TABLE. Batch rezim to
        # obejde tim, ze tabulku vytvori znovu a data prekopiruje.
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()
    # Bez cizich klicu nikdo nehlida, jestli migrace nenechala viset vazbu
    # na smazany radek - zkontrolujeme to sami. Vypisujeme jen nazvy tabulek,
    # nikdy hodnoty (v databazi jsou udaje deti).
    porusene = spojeni.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if porusene:
        tabulky = sorted({radek[0] for radek in porusene})
        raise RuntimeError(
            f"Po migraci je v databazi {len(porusene)} porusenych vazeb "
            f"(tabulky: {', '.join(tabulky)}). Obnov databazi z kopie a zjisti priciny."
        )


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
