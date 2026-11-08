"""pokusy cteni u skenu

Ucenka, kterou nejde precist (nebo ze ktere AI nevytahne zadne veci), zustavala
ve fronte a pri kazdem kliknuti se cetla znovu - u placeneho klice za penize
. Dva nove sloupce `sken`: pocitadlo neuspechu a priznak "veci uz
vytazene".

Stavajici skeny dostanou 0 / ne - nic se tim nemeni, jen se pocitadlo zacne
pocitat odted. Dary, ktere uz ze skenu vznikly, nic nemeni: stranka je pozna
podle radku `dar`.

Revision ID: c3d81f5a2e47
Revises: a7c41e90b5d3
Create Date: 2026-10-02 09:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d81f5a2e47"
down_revision: str | Sequence[str] | None = "a7c41e90b5d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # POZOR: zadny batch_alter_table na tabulce sken. Batch rezim tabulku zahodi
    # a zalozi znovu, a na `sken` odkazuji cizi klice s kaskadou (casti, dary,
    # priloha_k_id) - DROP TABLE by je smazal. Sloupce se pridavaji primo.
    op.execute("ALTER TABLE sken ADD COLUMN pokusy_cteni INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE sken ADD COLUMN dary_precteny BOOLEAN NOT NULL DEFAULT 0")


def downgrade() -> None:
    """Downgrade schema."""
    # SQLite od verze 3.35 umi sloupec smazat primo, bez prestavby tabulky.
    op.execute("ALTER TABLE sken DROP COLUMN dary_precteny")
    op.execute("ALTER TABLE sken DROP COLUMN pokusy_cteni")
