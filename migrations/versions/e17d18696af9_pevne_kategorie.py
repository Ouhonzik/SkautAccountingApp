"""pevne kategorie

Kategorie dostala sloupec `pevna`. Pevna (systemova) kategorie je ta, na kterou
aplikace spolehá - dnes jen "Vecne dary" (klic `vecne dary`), ze ktere se na
obrazovce Obdarovanych ctou veci z uctenek. Takova kategorie nejde smazat ani
prejmenovat; vlastni kategorie strediska se chovaji jako dosud.

Co se stane se stavajicimi daty:

- Kategorie s klicem `vecne dary` (v libovolne velikosti pismen) se oznaci jako pevna.
- Kdyz takova neni, ale existuje kategorie, jejiz nazev zacina "Vecne dar..." (tak
  ji driv appka hledala, kdyz jí chybel klic), dostane klic `vecne dary` a stane se
  pevnou. Pri vice shodach se bere ta s nejnizsim id. Stary klic se v tom pripade
  prepise - klic nikde jinde nic neridi, AI ho dostava pri kazdem cteni znovu.
- Kdyz nic takoveho v ciselniku neni (kategorie byla smazana nebo prejmenovana tak,
  ze ji nelze poznat), migrace NIC nezaklada: ciselnik je dle rozhodnuti strediska
  cely v jeho rukou (kategorie nemaji zadnou prednastavenou sadu) a obrazovka
  Obdarovanych sama rekne, ze kategorie chybi. Staci ji zalozit na /kategorie
  s klicem `vecne dary` - od te chvile je pevna.

Revision ID: e17d18696af9
Revises: c9e4b7a1d352
Create Date: 2026-10-12 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e17d18696af9"
down_revision: str | Sequence[str] | None = "c9e4b7a1d352"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Opsano zde schvalne, ne importem z app.models: migrace se nesmi zmenit,
# kdyz se zmeni kod aplikace.
KLIC_VECNE_DARY = "vecne dary"
ZACATEK_NAZVU_DARU = "věcné dar"


def upgrade() -> None:
    """Upgrade schema."""
    # POZOR: zadny batch_alter_table na tabulce kategorie. Batch rezim tabulku
    # zahodi a zalozi znovu a na `kategorie` odkazuji cizi klice (slozka, sken,
    # cast_skenu) - DROP TABLE by spustil jejich kaskadu. Sloupec se pridava primo.
    op.execute("ALTER TABLE kategorie ADD COLUMN pevna BOOLEAN DEFAULT 0 NOT NULL")

    spojeni = op.get_bind()
    # Porovnani v Pythonu, ne v SQL: `lower()` v SQLite umi jen anglickou abecedu.
    radky = spojeni.execute(sa.text("SELECT id, nazev, klic FROM kategorie ORDER BY id")).all()
    s_klicem = [r.id for r in radky if (r.klic or "").lower() == KLIC_VECNE_DARY]
    if s_klicem:
        pevne = s_klicem
    else:
        podle_nazvu = [
            r for r in radky if r.nazev.strip().casefold().startswith(ZACATEK_NAZVU_DARU)
        ]
        pevne = []
        if podle_nazvu:
            prvni = podle_nazvu[0]
            spojeni.execute(
                sa.text("UPDATE kategorie SET klic = :klic WHERE id = :id"),
                {"klic": KLIC_VECNE_DARY, "id": prvni.id},
            )
            pevne = [prvni.id]
    for kategorie_id in pevne:
        spojeni.execute(
            sa.text("UPDATE kategorie SET pevna = 1 WHERE id = :id"), {"id": kategorie_id}
        )


def downgrade() -> None:
    """Downgrade schema."""
    # SQLite od verze 3.35 umi sloupec smazat primo, bez prestavby tabulky.
    op.execute("ALTER TABLE kategorie DROP COLUMN pevna")
