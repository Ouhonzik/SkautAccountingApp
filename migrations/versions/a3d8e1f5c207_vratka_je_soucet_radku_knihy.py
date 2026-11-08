"""vratka ucastnika je soucet radku knihy

Sloupec `ucastnik.vratka` byl opis castky z pokladni knihy. Druha vratka temuz
ucastnikovi nebo smazani jednoho radku ho rozesly s knihou, proto se sloupec
ruší a `Ucastnik.vratka` se pocita z radku `pokladni_radek.ucastnik_id`.
Sloupec byl jen kopie, radky knihy zustavaji, takze se nic neztraci.

Revision ID: a3d8e1f5c207
Revises: f7c2a9d4e8b3
Create Date: 2026-10-09 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3d8e1f5c207"
down_revision: str | Sequence[str] | None = "f7c2a9d4e8b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # POZOR: zadny batch_alter_table na tabulce ucastnik. Batch rezim tabulku
    # zahodi a zalozi znovu, a protoze app/db.py zapina cizi klice i pri
    # migraci, DROP TABLE by kaskadou smazal dochazku a nastavil
    # pokladni_radek.ucastnik_id na NULL. SQLite od 3.35 umi sloupec smazat primo.
    op.execute("ALTER TABLE ucastnik DROP COLUMN vratka")


def downgrade() -> None:
    """Downgrade schema."""
    # Bez prestavby tabulky ze stejneho duvodu jako v upgrade().
    op.execute("ALTER TABLE ucastnik ADD COLUMN vratka INTEGER")
    # Hodnota je soucet radku knihy v halerich (typ Castka), jak ji pocita aplikace.
    op.execute(
        "UPDATE ucastnik SET vratka = "
        "(SELECT SUM(castka) FROM pokladni_radek WHERE pokladni_radek.ucastnik_id = ucastnik.id)"
    )
