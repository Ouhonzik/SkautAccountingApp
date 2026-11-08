"""limit mista na akci

Kolik MB souboru smi mit akce dohromady - prazdne znamena vychozi
hodnotu z .env, meni jen spravce u konkretni akce. Obycejny ALTER TABLE, ne
batch_alter_table: akce je tabulka, na kterou se odkazuje spousta cizich
klicu (sken, slozka, prijem...) a batch rezim by ji zahodil a zalozil znovu -
DROP TABLE by spustil kaskadu a smazal vsechno, co na akci visi.

Revision ID: 0b2a0ca4ec49
Revises: f1a2b3c4d5e6
Create Date: 2026-09-27 16:34:41.446626

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0b2a0ca4ec49"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TABLE akce ADD COLUMN misto_mb_limit INTEGER")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE akce DROP COLUMN misto_mb_limit")
