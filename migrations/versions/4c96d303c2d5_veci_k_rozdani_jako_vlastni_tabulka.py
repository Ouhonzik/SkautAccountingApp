"""Veci k rozdani jako vlastni tabulka

Radek seznamu obdarovanych byl doted jeden dar - kdo dostal dve veci, byl
v seznamu dvakrat a do sloupce `vec` se psalo rucne. Ted je radek jeden clovek
a veci jsou vlastni tabulka `dar`: jeden kus = jeden radek, takze se daji
tahat mysi od cloveka k cloveku a rozhazovat mezi ne automaticky.

Prazdny `obdarovany_id` znamena "vec lezi v zasobniku a jeste nema majitele".

Co uz v seznamu bylo, se neztrati: vyplnene `vec` se pred zrusenim sloupce
prepise na radek v nove tabulce a zustane u sveho cloveka.

Revision ID: 4c96d303c2d5
Revises: 6829634c5641
Create Date: 2026-09-01 14:44:40.873835

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4c96d303c2d5"
down_revision: str | Sequence[str] | None = "6829634c5641"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dar",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("akce_id", sa.Integer(), nullable=False),
        sa.Column("sken_id", sa.Integer(), nullable=True),
        sa.Column("obdarovany_id", sa.Integer(), nullable=True),
        sa.Column("nazev", sa.String(length=200), nullable=False),
        sa.Column("poradi", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["akce_id"], ["akce.id"], name=op.f("fk_dar_akce_id_akce"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["obdarovany_id"],
            ["obdarovany.id"],
            name=op.f("fk_dar_obdarovany_id_obdarovany"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["sken_id"], ["sken.id"], name=op.f("fk_dar_sken_id_sken"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dar")),
    )

    # Prestehovani rucne vepsanych veci. Sken zustava prazdny - stara vec
    # z zadne uctenky neprisla, Honza ji napsal tuztickou.
    op.execute(
        """
        INSERT INTO dar (akce_id, sken_id, obdarovany_id, nazev, poradi)
        SELECT akce_id, NULL, id, vec, poradi
        FROM obdarovany
        WHERE vec IS NOT NULL AND TRIM(vec) <> ''
        """
    )

    # batch_alter_table: SQLite neumi zrusit sloupec u hotove tabulky, takze
    # ji Alembic postavi znovu a data prekopiruje.
    with op.batch_alter_table("obdarovany", schema=None) as batch_op:
        batch_op.drop_column("vec")


def downgrade() -> None:
    with op.batch_alter_table("obdarovany", schema=None) as batch_op:
        batch_op.add_column(sa.Column("vec", sa.VARCHAR(length=200), nullable=True))

    # Zpatky se vejde jen jedna vec na cloveka - sloupec je jeden. Vezme se
    # ta prvni; ostatni s tabulkou zaniknou.
    op.execute(
        """
        UPDATE obdarovany
        SET vec = (
            SELECT nazev FROM dar
            WHERE dar.obdarovany_id = obdarovany.id
            ORDER BY dar.poradi, dar.id
            LIMIT 1
        )
        """
    )

    op.drop_table("dar")
