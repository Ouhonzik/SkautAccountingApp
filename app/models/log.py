"""Log cinnosti a evidence cteni pres AI."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Zaklad


class ZaznamCinnosti(Zaklad):
    """Jeden radek v logu cinnosti - kdo, kdy a co zmenil.

    Vetsinu radku vyrabi automaticky app/services/log_cinnosti/zapisovani.py ze
    SQLAlchemy udalosti pri kazdem ulozeni (`Session.flush`). Prihlaseni,
    odhlaseni a neuspesne pokusy se zapisuji rucne, protoze pri nich zadny
    radek jine tabulky nevznika ani se nemeni.
    """

    __tablename__ = "zaznam_cinnosti"
    # Hook logu pri kazdem ulozeni hleda zaznam ke sloucení podle objektu a casu;
    # log se nepromazava, takze bez indexu by ukladani s casem zpomalovalo.
    __table_args__ = (Index("ix_zaznam_cinnosti_objekt", "typ_objektu", "objekt_id", "do"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Pri smazani uctu se jeho zaznamy mazou (`smaz_ucet`), ten SET NULL je jen pojistka
    # proti poskozene vazbe. Jmeno je opsane vedle jako snapshot v case zaznamu.
    uzivatel_id: Mapped[int | None] = mapped_column(
        ForeignKey("uzivatel.id", ondelete="SET NULL"), default=None, index=True
    )
    uzivatel_jmeno: Mapped[str] = mapped_column(String(120))
    typ_cinnosti: Mapped[str] = mapped_column(String(30))
    # __tablename__ zmeneneho/smazaneho objektu, napr. "sken". Prazdne
    # u prihlaseni a odhlaseni.
    typ_objektu: Mapped[str | None] = mapped_column(String(30), default=None)
    objekt_id: Mapped[int | None] = mapped_column(Integer, default=None)
    # Bez ForeignKey schvalne - akce muze byt pozdeji smazana a zaznam
    # o tom ma v logu zustat. Slouzi jen k filtrovani, ne k JOINu.
    akce_id: Mapped[int | None] = mapped_column(Integer, default=None, index=True)
    akce_nazev: Mapped[str | None] = mapped_column(String(200), default=None)
    popis: Mapped[str] = mapped_column(String(500))
    # {"pole": {"pred": "...", "po": "..."}}; u osobnich udaju (ucastnik,
    # obdarovany) jen nazvy zmenenych poli, nikdy hodnoty.
    detail_json: Mapped[str | None] = mapped_column(Text, default=None)
    # Kolik objektu zaznam shrnuje - hromadna akce (napr. roztrideni) nebo
    # slouceni prubezneho ukladani (viz log_cinnosti.OKNO_SLOUCENI).
    pocet: Mapped[int] = mapped_column(Integer, default=1)
    od: Mapped[dt.datetime] = mapped_column(DateTime)
    do: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    ip: Mapped[str | None] = mapped_column(String(45), default=None)


class PouzitiAI(Zaklad):
    """Jedno cteni uctenky nebo veci pres Gemini - pro odhad utraty za placeny klic.

    Cena se tu neuklada, jen tokeny - pocita se az pri zobrazeni z cenniku
    v app/services/tarif.py, aby zmena cenniku prepocitala i historii.
    """

    __tablename__ = "pouziti_ai"

    id: Mapped[int] = mapped_column(primary_key=True)
    uzivatel_id: Mapped[int | None] = mapped_column(
        ForeignKey("uzivatel.id", ondelete="SET NULL"), default=None, index=True
    )
    # Bez ForeignKey - stejny duvod jako u ZaznamCinnosti.akce_id.
    akce_id: Mapped[int | None] = mapped_column(Integer, default=None)
    sken_id: Mapped[int | None] = mapped_column(Integer, default=None)
    kdy: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    tarif: Mapped[str] = mapped_column(String(10))
    model: Mapped[str] = mapped_column(String(60))
    tokeny_vstup: Mapped[int] = mapped_column(Integer, default=0)
    tokeny_vystup: Mapped[int] = mapped_column(Integer, default=0)
    tokeny_mysleni: Mapped[int] = mapped_column(Integer, default=0)
    # "uctenka" nebo "dary" - dve oddelena cteni tehoz skenu, viz app/services/ocr.py.
    ucel: Mapped[str] = mapped_column(String(20))
