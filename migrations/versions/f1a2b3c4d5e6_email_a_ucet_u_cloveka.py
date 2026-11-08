"""email a ucet u cloveka

Clovek z ciselniku muze mit napojeny uzivatelsky ucet (pozvanka, Etapa 10) -
pripravuje se na e-podpisy (Etapa 12), kdy uz to bude nutne. Uzivatel navic
dostava email_overen a unikatni e-mail.

POZOR: zadny batch_alter_table. Na tabulky `clovek` i `uzivatel` odkazuji
cizi klice (sken, slozka, akce, cestak / vsechny FK na uzivatele) a batch
rezim by tabulku zahodil a zalozil znovu - se zapnutymi cizimi klici by to
spustilo kaskadu (ON DELETE CASCADE/SET NULL) a smazalo nebo vynulovalo
existujici data. SQLite od verze 3.35 umi ALTER TABLE ADD COLUMN i bez
prestavby - stejny vzor jako migrace b7d5cb226e48.

Revision ID: f1a2b3c4d5e6
Revises: 0b4476e47992
Create Date: 2026-09-17 23:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "0b4476e47992"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TABLE clovek ADD COLUMN email VARCHAR(200)")
    op.execute(
        "ALTER TABLE clovek ADD COLUMN uzivatel_id INTEGER REFERENCES uzivatel(id) ON DELETE SET NULL"
    )
    # Jeden ucet smi patrit nejvyse jednomu clovku - partial index, aby
    # nulove hodnoty (vetsina lidi v ciselniku bez uctu) navzajem nekolidovaly.
    op.execute(
        "CREATE UNIQUE INDEX uq_clovek_uzivatel ON clovek(uzivatel_id) "
        "WHERE uzivatel_id IS NOT NULL"
    )

    op.execute("ALTER TABLE uzivatel ADD COLUMN email_overen BOOLEAN NOT NULL DEFAULT 0")

    # Unikatni e-mail u uzivatele - ale nejdriv zkontrolovat, ze uz zadny
    # neni duplicitni (mohl vzniknout rucnim zadanim pred touhle migraci).
    # Migrace radeji skonci chybou s napovedou, nez aby nejaky radek tise
    # sama mazala nebo prepisovala.
    spojeni = op.get_bind()
    duplicity = spojeni.execute(
        sa.text(
            "SELECT email, COUNT(*) AS pocet FROM uzivatel "
            "WHERE email IS NOT NULL AND email != '' "
            "GROUP BY email HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if duplicity:
        seznam = ", ".join(f"„{radek[0]}“ ({radek[1]}×)" for radek in duplicity)
        raise RuntimeError(
            "Migrace f1a2b3c4d5e6 nejde spustit: v tabulce uzivatel je duplicitni "
            f"e-mail u víc účtů: {seznam}. Oprav to ručně v databázi (kazdy ucet musi "
            "mit jiny e-mail, nebo ho u nekterych uctu vymaz) a spust migraci znovu."
        )
    op.execute(
        "CREATE UNIQUE INDEX uq_uzivatel_email ON uzivatel(email) "
        "WHERE email IS NOT NULL AND email != ''"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX uq_uzivatel_email")
    op.execute("ALTER TABLE uzivatel DROP COLUMN email_overen")

    op.execute("DROP INDEX uq_clovek_uzivatel")
    op.execute("ALTER TABLE clovek DROP COLUMN uzivatel_id")
    op.execute("ALTER TABLE clovek DROP COLUMN email")
