"""kdo vybral prijem z ciselniku lidi

Jmeno na tiskopisu PPD ("Prijato od") se u uctenek vybira z ciselniku lidi a
u prijmu se psalo rucne, i kdyz preklep v nem je videt az na papire. Novy sloupec
`prijem.clovek_id` navazuje prijem na cloveka z ciselniku (smazany clovek vazbu
jen vynuluje - ON DELETE SET NULL).

Stavajici texty `prijem.kdo` se prevedou podle shody jmena s ciselnikem (bez
ohledu na velka pismena a okolni mezery, porovnava se v Pythonu - `lower()`
v SQLite zna jen anglickou abecedu). U prevedenych se text smaze, aby se po
prejmenovani cloveka nerozesel. Co se v ciselniku nenajde, zustane jako text.

Revision ID: c9e4b7a1d352
Revises: a3d8e1f5c207
Create Date: 2026-10-10 16:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "c9e4b7a1d352"
down_revision: str | Sequence[str] | None = "a3d8e1f5c207"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _klic(jmeno: str) -> str:
    """Tvar jmena pro porovnani - stejny jako `ciselniky.klic_nazvu`, ale ze
    vice mezer uvnitr udela jednu (rucne psany text je casto neupraveny)."""
    return " ".join(jmeno.split()).casefold()


def upgrade() -> None:
    """Upgrade schema."""
    # POZOR: zadny batch_alter_table na tabulce prijem (a hlavne ne na clovek).
    # Batch rezim tabulku zahodi a zalozi znovu a app/db.py zapina cizi klice
    # i pri migraci - DROP TABLE spusti kaskadu a smaze data. Sloupec se pridava
    # primo, SQLite od 3.35 umi i ADD COLUMN s cizim klicem.
    op.execute(
        "ALTER TABLE prijem ADD COLUMN clovek_id INTEGER REFERENCES clovek(id) ON DELETE SET NULL"
    )

    spojeni = op.get_bind()
    lide: dict[str, int] = {}
    for clovek_id, jmeno in spojeni.execute(text("SELECT id, jmeno FROM clovek ORDER BY id")):
        # Jmeno v ciselniku je unikatni, ale po sloučení mezer a velkych pismen
        # se dva mohou potkat - takovy text je dvojznacny, nechame ho jako text.
        klic = _klic(jmeno)
        lide[klic] = -1 if klic in lide else clovek_id

    for prijem_id, kdo in spojeni.execute(
        text("SELECT id, kdo FROM prijem WHERE kdo IS NOT NULL")
    ).all():
        clovek_id = lide.get(_klic(kdo))
        if clovek_id is not None and clovek_id > 0:
            spojeni.execute(
                text("UPDATE prijem SET clovek_id = :clovek, kdo = NULL WHERE id = :prijem"),
                {"clovek": clovek_id, "prijem": prijem_id},
            )


def downgrade() -> None:
    """Downgrade schema."""
    # Jmeno z ciselniku se vrati do textu, aby se nic neztratilo.
    op.execute(
        "UPDATE prijem SET kdo = (SELECT jmeno FROM clovek WHERE clovek.id = prijem.clovek_id) "
        "WHERE clovek_id IS NOT NULL"
    )
    # Bez prestavby tabulky ze stejneho duvodu jako v upgrade().
    op.execute("ALTER TABLE prijem DROP COLUMN clovek_id")
