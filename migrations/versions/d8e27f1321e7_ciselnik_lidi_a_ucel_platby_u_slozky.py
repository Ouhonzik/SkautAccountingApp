"""Ciselnik lidi a ucel platby u slozky

Volny text "kdo platil" se meni na odkaz do ciselniku `clovek`. Jmena, ktera
uz v databazi jsou, se do ciselniku preleji - kazde jednou, bez ohledu na to,
u kolika uctenek stalo. Az potom se stary sloupec zahodi.

Poradi kroku je dulezite: `kdo_platil` se musi precist jeste **pred** tim, nez
ho batch_alter_table odstrani, jinak by se jmena ztratila.

Revision ID: d8e27f1321e7
Revises: 8e9f4b8d1043
Create Date: 2026-08-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d8e27f1321e7"
down_revision = "8e9f4b8d1043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clovek",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("jmeno", sa.String(length=120), nullable=False),
        sa.Column("poradi", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("jmeno"),
    )

    with op.batch_alter_table("slozka") as batch:
        batch.add_column(sa.Column("clovek_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("ucel_platby", sa.String(length=200), nullable=True))
        batch.create_foreign_key(
            "fk_slozka_clovek", "clovek", ["clovek_id"], ["id"], ondelete="SET NULL"
        )

    with op.batch_alter_table("sken") as batch:
        batch.add_column(sa.Column("clovek_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_sken_clovek", "clovek", ["clovek_id"], ["id"], ondelete="SET NULL"
        )

    _preved_jmena()

    with op.batch_alter_table("slozka") as batch:
        batch.drop_column("kdo_platil")
    with op.batch_alter_table("sken") as batch:
        batch.drop_column("kdo_platil")


def _preved_jmena() -> None:
    """Zalozi cloveka pro kazde jmeno, ktere v databazi uz stoji, a naveze ho."""
    spojeni = op.get_bind()

    jmena: list[str] = []
    for tabulka in ("slozka", "sken"):
        nalezena = spojeni.execute(
            sa.text(f"SELECT DISTINCT kdo_platil FROM {tabulka} WHERE kdo_platil IS NOT NULL")
        ).scalars()
        for jmeno in nalezena:
            ocistene = (jmeno or "").strip()
            if ocistene and ocistene not in jmena:
                jmena.append(ocistene)

    for poradi, jmeno in enumerate(sorted(jmena)):
        spojeni.execute(
            sa.text("INSERT INTO clovek (jmeno, poradi) VALUES (:jmeno, :poradi)"),
            {"jmeno": jmeno, "poradi": poradi},
        )

    # Porovnava se orezane jmeno, aby se "Sindelar " a "Sindelar" navazaly na tehoz.
    for tabulka in ("slozka", "sken"):
        spojeni.execute(
            sa.text(
                f"UPDATE {tabulka} SET clovek_id = "
                "(SELECT id FROM clovek WHERE clovek.jmeno = TRIM(kdo_platil)) "
                "WHERE kdo_platil IS NOT NULL"
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("sken") as batch:
        batch.add_column(sa.Column("kdo_platil", sa.String(length=120), nullable=True))
    with op.batch_alter_table("slozka") as batch:
        batch.add_column(sa.Column("kdo_platil", sa.String(length=120), nullable=True))

    spojeni = op.get_bind()
    for tabulka in ("slozka", "sken"):
        spojeni.execute(
            sa.text(
                f"UPDATE {tabulka} SET kdo_platil = "
                "(SELECT jmeno FROM clovek WHERE clovek.id = clovek_id) "
                "WHERE clovek_id IS NOT NULL"
            )
        )

    with op.batch_alter_table("sken") as batch:
        batch.drop_constraint("fk_sken_clovek", type_="foreignkey")
        batch.drop_column("clovek_id")
    with op.batch_alter_table("slozka") as batch:
        batch.drop_constraint("fk_slozka_clovek", type_="foreignkey")
        batch.drop_column("ucel_platby")
        batch.drop_column("clovek_id")

    op.drop_table("clovek")
