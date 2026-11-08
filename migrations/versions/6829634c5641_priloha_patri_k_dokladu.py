"""Priloha patri k dokladu

Sken dostal odkaz na jiny sken: `priloha_k_id` rika, ke ktere uctence priloha
patri. Nic se tim nikde neprepocitava - vazba se projevi jen na tiskopisu
PPD/VPD, kde se pod uctenkou vypisou papiry k ni nalepene.

Smazani uctenky prilohu nesmaze, jen ji odpoji (ON DELETE SET NULL).

Revision ID: 6829634c5641
Revises: c70828231bcb
Create Date: 2026-08-31 18:38:47.072638

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6829634c5641"
down_revision: str | Sequence[str] | None = "c70828231bcb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch_alter_table: SQLite neumi pridat cizi klic k hotove tabulce, takze
    # ji Alembic postavi znovu a data prekopiruje.
    with op.batch_alter_table("sken", schema=None) as batch_op:
        batch_op.add_column(sa.Column("priloha_k_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_sken_priloha_k_id_sken"),
            "sken",
            ["priloha_k_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("sken", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_sken_priloha_k_id_sken"), type_="foreignkey")
        batch_op.drop_column("priloha_k_id")
