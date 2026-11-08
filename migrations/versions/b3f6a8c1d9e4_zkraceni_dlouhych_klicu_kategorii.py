"""zkraceni dlouhych klicu kategorii

Sloupec `kategorie.klic` je `String(30)`, ale SQLite delku nevynucuje a formular
kategorii ji dlouho nehlidal, takze v ostre databazi vznikla kategorie s klicem
o 40 znacich. Formular a model ted delsi klic odmitnou; tahle migrace
zkrati uz ulozene.

Schema se NEMENI, jen se opravi data:

- Klic delsi nez 30 znaku se zkrati na prvnich 30 (bez mezer na konci).
- Kdyby zkraceny klic splynul s klicem jine kategorie (je unikatni), pripise se
  mu `-<id>` a zkrati se tak, aby se vesel do 30 znaku.
- Klic je jen stale oznaceni pro vystup AI; AI ho dostava pri kazdem cteni
  znovu, takze zkraceni nic nerozbije. Nazev kategorie ani vazby (slozky,
  skeny) se nemeni.
- Pevnost (`pevna`) se nemeni: pevny klic `vecne dary` ma 10 znaku, zkraceni se
  ho netyka.

Zadny batch_alter_table: na `kategorie` odkazuji cizi klice (slozka, sken,
cast_skenu) a DROP TABLE by spustil jejich kaskadu - jen UPDATE.

Revision ID: b3f6a8c1d9e4
Revises: e17d18696af9
Create Date: 2026-10-13 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3f6a8c1d9e4"
down_revision: str | Sequence[str] | None = "e17d18696af9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Opsano zde schvalne, ne importem z app.models: migrace se nesmi zmenit,
# kdyz se zmeni kod aplikace.
MAX_DELKA = 30


def upgrade() -> None:
    """Upgrade schema (jen data)."""
    spojeni = op.get_bind()
    # Delku meri Python (znaky), ne SQL `length()` - ten u jinych kodovani
    # a znaku mimo zakladni rovinu nemusi dat totez.
    radky = spojeni.execute(sa.text("SELECT id, klic FROM kategorie ORDER BY id")).all()
    pouzite = {r.klic for r in radky if r.klic is not None and len(r.klic) <= MAX_DELKA}
    for radek in radky:
        if radek.klic is None or len(radek.klic) <= MAX_DELKA:
            continue
        novy = radek.klic[:MAX_DELKA].rstrip() or str(radek.id)
        if novy in pouzite:
            pripona = f"-{radek.id}"
            novy = radek.klic[: MAX_DELKA - len(pripona)].rstrip() + pripona
        pouzite.add(novy)
        spojeni.execute(
            sa.text("UPDATE kategorie SET klic = :klic WHERE id = :id"),
            {"klic": novy, "id": radek.id},
        )


def downgrade() -> None:
    """Zkraceny klic se vratit neda (puvodni text se neuklada); schema se nemenilo."""
