"""Vychozi kategorie se uz nenalevaji vubec

Puvodne tahle migrace nalila do prazdneho ciselniku sest vychozich kategorii
(Jizdne, Jidlo, Material, Ubytovani, Cestak, Jine). Driv je doplnoval server
pri kazdem startu, co Honza smazal, bylo po restartu zpatky - a migrace to
mela udelat presne jednou, aby se to stat nemohlo.

Honza 12. 9. 2026 rozhodl jinak: **zadne prednastavene kategorie**. Ciselnik
je cely v rukou strediska, zaklada se na strance /kategorie a AI vybira jen
z toho, co tam opravdu je. Nova databaze proto zacina prazdna.

Telo `upgrade()` je tedy schvalne prazdne. Migrace se nesmi smazat - v
databazich, ktere ji uz probehly, je zapsana v historii a Alembic by pri
`upgrade head` neznamou revizi nenasel. Kategorie, ktere nalila driv, se taky
nemazou: Honza si je prejmenoval a jsou navazane na uctenky.

Revision ID: e3a91c47b5d2
Revises: df84bb38cf97
Create Date: 2026-09-03 10:00:00.000000

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "e3a91c47b5d2"
down_revision: str | Sequence[str] | None = "df84bb38cf97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Zamerne nic - viz docstring nahore.
    pass


def downgrade() -> None:
    pass
