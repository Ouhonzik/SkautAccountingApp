"""kilometry jizd na jedno desetinne misto

`jizda.km` byly cele kilometry. Rucne zadane "12,9" se tise useklo na 12, zatimco
stejna jizda z mapy se zaokrouhlila na 13. Od ted se km evidují na desetiny (12,4)
a v databazi jsou cele desetiny (typ `Desetiny` v app/models.py, stejny trik jako
`Setiny`): 12 km je v sloupci 120.

Sloupec zustava INTEGER, meni se jen jednotka, proto staci `UPDATE` - zadna
prestavba tabulky. Zpet (`downgrade`) se desetiny zaokrouhli na cele km
(124 -> 12, 125 -> 13), jinak by stara verze appky cetla desetkrat vetsi cisla.

Revision ID: f7c2a9d4e8b3
Revises: e5b9c7a2f4d1
Create Date: 2026-10-06 13:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7c2a9d4e8b3"
down_revision: str | Sequence[str] | None = "e5b9c7a2f4d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # POZOR: zadny batch_alter_table na tabulce jizda - sloupec se nemeni, jen data.
    op.execute("UPDATE jizda SET km = km * 10 WHERE km IS NOT NULL")


def downgrade() -> None:
    """Downgrade schema."""
    # Celociselne deleni v SQLite useka, proto +5 pro zaokrouhleni nahoru od pulky.
    op.execute("UPDATE jizda SET km = (km + 5) / 10 WHERE km IS NOT NULL")
