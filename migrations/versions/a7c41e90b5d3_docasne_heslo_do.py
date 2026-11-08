"""docasne heslo do

Docasne heslo od spravce nikdy nevyprselo - kdo ho nestihl pouzit, nechal
v databazi ucet, ktery jde po tyden i mesice prevzit hadanim. Novy
sloupec `docasne_heslo_do` drzi lhutu; po ni se s heslem nejde prihlasit.

Ucty, ktere docasne heslo maji uz ted (`musi_zmenit_heslo = 1`), dostanou
lhutu 7 dni od migrace - i jejich (slabe, starsi) heslo tak casem prestane
platit. Kdo se do te doby neprihlasi, dostane od spravce nove.

Revision ID: a7c41e90b5d3
Revises: dd02c5cbbf97
Create Date: 2026-09-30 10:12:41.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c41e90b5d3"
down_revision: str | Sequence[str] | None = "dd02c5cbbf97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # POZOR: zadny batch_alter_table na tabulce uzivatel. Batch rezim tabulku
    # zahodi a zalozi znovu, a na `uzivatel` odkazuji cizi klice s kaskadou
    # (tokeny, clovek, log) - DROP TABLE by je smazal. Sloupec se pridava primo.
    op.execute("ALTER TABLE uzivatel ADD COLUMN docasne_heslo_do DATETIME")
    # datetime('now') je UTC, stejne jako naivni cas ukladany appkou.
    op.execute(
        "UPDATE uzivatel SET docasne_heslo_do = datetime('now', '+7 days') "
        "WHERE musi_zmenit_heslo = 1"
    )


def downgrade() -> None:
    """Downgrade schema."""
    # SQLite od verze 3.35 umi sloupec smazat primo, bez prestavby tabulky.
    op.execute("ALTER TABLE uzivatel DROP COLUMN docasne_heslo_do")
