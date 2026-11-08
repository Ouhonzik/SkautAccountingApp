"""limity cteni ai u uzivatele

Pristup k placenemu klici ke Gemini mel kazdy Uzivatel a cteni nemelo zadny
limit - skript pres noc mohl protocit klic dokola. Tri nove sloupce
`uzivatel`: nejvyssi pocet cteni za den a nejvyssi utrata v Kc za den a za rok.
Kazdemu uctu je nastavuje spravce na /uzivatele.

Stavajici ucty dostanou vychozi hodnoty 300 cteni / 100 Kc za den / 500 Kc
za rok - tabor s 55 uctenkami se do nich vejde.

Revision ID: d4e92a6b7c18
Revises: c3d81f5a2e47
Create Date: 2026-10-02 11:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e92a6b7c18"
down_revision: str | Sequence[str] | None = "c3d81f5a2e47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # POZOR: zadny batch_alter_table na tabulce uzivatel. Batch rezim tabulku
    # zahodi a zalozi znovu, a na `uzivatel` odkazuji cizi klice s kaskadou
    # (tokeny, clovek, log) - DROP TABLE by je smazal. Sloupce se pridavaji primo.
    op.execute("ALTER TABLE uzivatel ADD COLUMN limit_cteni_den INTEGER NOT NULL DEFAULT 300")
    op.execute("ALTER TABLE uzivatel ADD COLUMN limit_kc_den INTEGER NOT NULL DEFAULT 100")
    op.execute("ALTER TABLE uzivatel ADD COLUMN limit_kc_rok INTEGER NOT NULL DEFAULT 500")


def downgrade() -> None:
    """Downgrade schema."""
    # SQLite od verze 3.35 umi sloupec smazat primo, bez prestavby tabulky.
    op.execute("ALTER TABLE uzivatel DROP COLUMN limit_kc_rok")
    op.execute("ALTER TABLE uzivatel DROP COLUMN limit_kc_den")
    op.execute("ALTER TABLE uzivatel DROP COLUMN limit_cteni_den")
