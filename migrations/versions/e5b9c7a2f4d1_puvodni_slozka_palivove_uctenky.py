"""puvodni slozka palivove uctenky u cestaku

Po odebrani cestaku z vydaju se palivova uctenka vraci do slozky, ve ktere lezela
pred pridanim do vydaju (tam se lepi na doklad cestaku a ze slozky vypadne).
Novy sloupec `cestak.puvodni_slozka_palivovky_id` si tu slozku pamatuje. Smazana
slozka sloupec vynuluje (ON DELETE SET NULL), takze se nemuze splest s novou,
ktera dostane stejne id.

Stavajici cestaky ve vydajich dostanou prazdnou hodnotu: kam jejich palivovka
patrila, se uz zjistit nedaji, a po odebrani z vydaju proto zustane nezarazena.

Revision ID: e5b9c7a2f4d1
Revises: b5e7a1c9d302
Create Date: 2026-10-06 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5b9c7a2f4d1"
down_revision: str | Sequence[str] | None = "b5e7a1c9d302"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # POZOR: zadny batch_alter_table na tabulce cestak. Batch rezim tabulku zahodi
    # a zalozi znovu a na `cestak` odkazuji cizi klice s kaskadou (jizdy, radek
    # pokladni knihy) - DROP TABLE by je smazal. Sloupec se pridava primo.
    op.execute(
        "ALTER TABLE cestak ADD COLUMN puvodni_slozka_palivovky_id "
        "INTEGER REFERENCES slozka(id) ON DELETE SET NULL"
    )


def downgrade() -> None:
    """Downgrade schema."""
    # SQLite od verze 3.35 umi sloupec smazat primo, bez prestavby tabulky.
    op.execute("ALTER TABLE cestak DROP COLUMN puvodni_slozka_palivovky_id")
