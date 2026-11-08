"""ucet nekdy aktivni

Ucet, ktery je `aktivni = 0`, byl dosud dvojiho druhu, ktere appka nerozlisila:
pozvany, ktery pozvanku jeste neprijal, a deaktivovany. Pozvanka pak fungovala
jako druha cesta k opetovne aktivaci deaktivovaneho uctu. Novy sloupec
`nekdy_aktivni` je rozlisuje.

Z dnesnich dat nejde spolehlive poznat, ktery neaktivni ucet uz nekdy aktivni
byl, proto se vsem stavajicim radkum nastavi 1 - ochrana pak plati pro vsechno,
co se deaktivuje od ted. Bezpecnejsi chyba nez oznacit deaktivovany ucet za
"jeste nikdy nepozvany".

Revision ID: dd02c5cbbf97
Revises: 0b2a0ca4ec49
Create Date: 2026-09-29 14:33:33.248668

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "dd02c5cbbf97"
down_revision: str | Sequence[str] | None = "0b2a0ca4ec49"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # POZOR: zadny batch_alter_table na tabulce uzivatel. Batch rezim tabulku
    # zahodi a zalozi znovu, a na `uzivatel` odkazuji cizi klice s kaskadou
    # (tokeny, clovek, log) - DROP TABLE by je smazal. Sloupec se pridava primo.
    op.execute("ALTER TABLE uzivatel ADD COLUMN nekdy_aktivni BOOLEAN NOT NULL DEFAULT 0")
    op.execute("UPDATE uzivatel SET nekdy_aktivni = 1")


def downgrade() -> None:
    """Downgrade schema."""
    # SQLite od verze 3.35 umi sloupec smazat primo, bez prestavby tabulky.
    op.execute("ALTER TABLE uzivatel DROP COLUMN nekdy_aktivni")
