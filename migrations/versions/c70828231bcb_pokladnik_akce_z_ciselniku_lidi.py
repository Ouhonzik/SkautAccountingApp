"""Pokladnik akce z ciselniku lidi

Stejny prevod jako u "kdo platil" v migraci d8e27f1321e7: volny text se meni
na odkaz do ciselniku `clovek`. Jmeno pokladnika, ktere uz v databazi je, se
do ciselniku dopini - jen kdyz tam jeste neni, aby se clovek nezalozil dvakrat.

Revision ID: c70828231bcb
Revises: d8e27f1321e7
Create Date: 2026-08-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c70828231bcb"
down_revision = "d8e27f1321e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("akce") as batch:
        batch.add_column(sa.Column("pokladnik_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_akce_pokladnik", "clovek", ["pokladnik_id"], ["id"], ondelete="SET NULL"
        )

    spojeni = op.get_bind()
    jmena = [
        (j or "").strip()
        for j in spojeni.execute(
            sa.text("SELECT DISTINCT pokladnik FROM akce WHERE pokladnik IS NOT NULL")
        ).scalars()
    ]
    uz_v_ciselniku = set(spojeni.execute(sa.text("SELECT jmeno FROM clovek")).scalars())
    poradi = spojeni.execute(sa.text("SELECT COALESCE(MAX(poradi), -1) FROM clovek")).scalar()

    for jmeno in sorted(set(jmena) - uz_v_ciselniku):
        if not jmeno:
            continue
        poradi += 1
        spojeni.execute(
            sa.text("INSERT INTO clovek (jmeno, poradi) VALUES (:jmeno, :poradi)"),
            {"jmeno": jmeno, "poradi": poradi},
        )

    spojeni.execute(
        sa.text(
            "UPDATE akce SET pokladnik_id = "
            "(SELECT id FROM clovek WHERE clovek.jmeno = TRIM(pokladnik)) "
            "WHERE pokladnik IS NOT NULL"
        )
    )

    with op.batch_alter_table("akce") as batch:
        batch.drop_column("pokladnik")


def downgrade() -> None:
    with op.batch_alter_table("akce") as batch:
        batch.add_column(sa.Column("pokladnik", sa.String(length=120), nullable=True))

    op.get_bind().execute(
        sa.text(
            "UPDATE akce SET pokladnik = "
            "(SELECT jmeno FROM clovek WHERE clovek.id = pokladnik_id) "
            "WHERE pokladnik_id IS NOT NULL"
        )
    )

    with op.batch_alter_table("akce") as batch:
        batch.drop_constraint("fk_akce_pokladnik", type_="foreignkey")
        batch.drop_column("pokladnik_id")
