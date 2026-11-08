"""indexy na cizich klicich a unikatni cestak v pokladni knize

K-033: SQLite index na cizim klici nevyrabi sam, takze se kazde nacteni
"skenu akce" a kazde smazani rodice (ON DELETE CASCADE/SET NULL musi najit
deti) prochazelo celou tabulku. K-031: jeden cestak smi mit v pokladni knize
nejvyse jeden radek.

POZOR: jen CREATE INDEX, zadny batch_alter_table - batch by tabulku zahodil
a zalozil znovu a se zapnutymi cizimi klici by to smazalo data.

Revision ID: b5e7a1c9d302
Revises: d4e92a6b7c18
Create Date: 2026-10-04 16:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5e7a1c9d302"
down_revision: str | Sequence[str] | None = "d4e92a6b7c18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (jmeno indexu, tabulka, sloupce) - jmena podle konvence `ix_<tabulka>_<sloupec>`
# z app/db.py, jinak by `alembic check` hlasil rozdil proti modelu.
INDEXY: list[tuple[str, str, str]] = [
    ("ix_slozka_akce_id", "slozka", "akce_id"),
    ("ix_sken_akce_id", "sken", "akce_id"),
    ("ix_prijem_akce_id", "prijem", "akce_id"),
    ("ix_pokladni_radek_akce_id", "pokladni_radek", "akce_id"),
    ("ix_ucastnik_akce_id", "ucastnik", "akce_id"),
    ("ix_obdarovany_akce_id", "obdarovany", "akce_id"),
    ("ix_dar_akce_id", "dar", "akce_id"),
    ("ix_cestak_akce_id", "cestak", "akce_id"),
    ("ix_sken_slozka_id", "sken", "slozka_id"),
    ("ix_sken_priloha_k_id", "sken", "priloha_k_id"),
    ("ix_cast_skenu_sken_id", "cast_skenu", "sken_id"),
    ("ix_jizda_cestak_id", "jizda", "cestak_id"),
    ("ix_dar_obdarovany_id", "dar", "obdarovany_id"),
    ("ix_pouziti_ai_kdy", "pouziti_ai", "kdy"),
    ("ix_zaznam_cinnosti_objekt", "zaznam_cinnosti", "typ_objektu, objekt_id, do"),
    (
        "ix_odeslany_email_uzivatel_ucel",
        "odeslany_email",
        "uzivatel_id, ucel, vytvoreno",
    ),
]


def upgrade() -> None:
    """Upgrade schema."""
    # Nejdriv zkontrolovat duplicity - migrace radeji skonci srozumitelnou
    # chybou, nez aby nejaky radek tise mazala nebo prepisovala.
    spojeni = op.get_bind()
    duplicity = spojeni.execute(
        sa.text(
            "SELECT cestak_id, COUNT(*) FROM pokladni_radek "
            "WHERE cestak_id IS NOT NULL GROUP BY cestak_id HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if duplicity:
        seznam = ", ".join(f"cestak {radek[0]} ({radek[1]}×)" for radek in duplicity)
        raise RuntimeError(
            "Migrace b5e7a1c9d302 nejde spustit: v pokladni knize je jeden cestak "
            f"vickrat: {seznam}. Nech jen jeden radek (ostatni smaz v appce nebo "
            "v databazi) a spust migraci znovu."
        )

    op.execute(
        "CREATE UNIQUE INDEX uq_pokladni_radek_cestak ON pokladni_radek(cestak_id) "
        "WHERE cestak_id IS NOT NULL"
    )
    for jmeno, tabulka, sloupce in INDEXY:
        op.execute(f"CREATE INDEX {jmeno} ON {tabulka}({sloupce})")


def downgrade() -> None:
    """Downgrade schema."""
    for jmeno, _tabulka, _sloupce in reversed(INDEXY):
        op.execute(f"DROP INDEX {jmeno}")
    op.execute("DROP INDEX uq_pokladni_radek_cestak")
