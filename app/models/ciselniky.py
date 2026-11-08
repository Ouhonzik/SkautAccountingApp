"""Ciselniky: kategorie, lide a vozidla (vcetne fotek technickeho prukazu)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    event,
    false,
    inspect,
    select,
    text,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship, validates

from app.db import Zaklad
from app.models.konstanty import MAX_DELKA_KLICE_KATEGORIE, PEVNE_KLICE_KATEGORII
from app.models.typy import Setiny

if TYPE_CHECKING:
    from app.models.ucty import Uzivatel


class Kategorie(Zaklad):
    """Ciselnik druhu vydaju. Edituje se v aplikaci, ne v kodu."""

    __tablename__ = "kategorie"

    id: Mapped[int] = mapped_column(primary_key=True)
    nazev: Mapped[str] = mapped_column(String(80), unique=True)
    # Stale oznaceni pro navazani na vystup AI. Diky nemu jde kategorii
    # prejmenovat, aniz by se rozbilo rozpoznavani.
    klic: Mapped[str | None] = mapped_column(
        String(MAX_DELKA_KLICE_KATEGORIE), unique=True, default=None
    )
    poradi: Mapped[int] = mapped_column(Integer, default=0)
    # Pevna (systemova) kategorie: jedna z deviti napevno danych (`PEVNE_KATEGORIE`),
    # nejde smazat, prejmenovat ani ji zmenit klic. Aplikace se zvlast spoleha na
    # "Vecne dary" (cteni daru z uctenek). Nastavuje se sama podle klice
    # (`_pevna_podle_klice`), takze ji nemuze zalozit nikdo jiny nez ten, kdo zada
    # pevny klic - a ten je vzdy obsazeny, protoze pevne kategorie zaklada migrace.
    pevna: Mapped[bool] = mapped_column(Boolean, server_default=false(), default=False)

    @validates("klic")
    def _pevna_podle_klice(self, _atribut: str, klic: str | None) -> str | None:
        """Pevny klic (bez ohledu na velikost pismen) kategorii zamkne.

        Stejne se klic porovnava v `kategorie_daru` (SQL `lower`), proto se tu
        nic neorezava.

        Jedno misto pro vsechny cesty, kudy kategorie vznika: formular, obnova
        ze zalohy i migrace. Zpet se pevnost nikdy neodemyka - klic se po
        zalozeni neupravuje a pevna kategorie se nemaze.

        Delsi klic, nez dovoluje sloupec, se odmitne tady: SQLite delku
        `String(n)` nehlida a delsi klic by se tise ulozil. Formular a obnova ze
        zalohy ho zkontroluji / zkrati driv, aby se k teto vyjimce nikdy nedoslo.
        """
        if klic is not None and len(klic) > MAX_DELKA_KLICE_KATEGORIE:
            raise ValueError(f"Klíč kategorie má nejvýš {MAX_DELKA_KLICE_KATEGORIE} znaků.")
        if klic is not None and klic.lower() in PEVNE_KLICE_KATEGORII:
            self.pevna = True
        return klic


@event.listens_for(Session, "before_flush")
def _hlidej_unikatni_klic_kategorii(sezeni: Session, _kontext: object, _instance: object) -> None:
    """Klic kategorie je unikatni bez ohledu na velikost pismen.

    `UNIQUE` v SQLite rozlisuje velka a mala pismena, takze "vecne dary" a "Vecne dary"
    by v databazi obstaly vedle sebe a vznikly by dve pevne kategorie (`kategorie_daru`
    hleda podle `lower(klic)` a nasla by nahodnou). Formular to hlida driv; tohle je
    pojistka pro vsechny ostatni cesty (obnova, skripty, budouci kod). Bez migrace:
    kontroluje se jen klic, ktery se prave zapisuje (nova kategorie nebo zmeneny klic),
    ne stare radky - kdyby v ostre databazi uz takova dvojice byla, bezna uprava
    jejich poradi nebo nazvu se kvuli tomu nesmi rozbit.
    """
    obsazene: dict[str, Kategorie] = {}
    for kategorie in (*sezeni.new, *sezeni.dirty):
        if not isinstance(kategorie, Kategorie) or kategorie.klic is None:
            continue
        if not inspect(kategorie).attrs.klic.history.has_changes():
            continue
        klic = kategorie.klic.strip().casefold()
        if obsazene.setdefault(klic, kategorie) is not kategorie:
            raise ValueError(f"Klíč „{kategorie.klic}“ se v jedné dávce opakuje.")
    if not obsazene:
        return
    vlastni_id = {k.id for k in obsazene.values() if k.id is not None}
    existujici = select(Kategorie.id, Kategorie.klic).where(Kategorie.klic.is_not(None))
    for id_, klic in sezeni.execute(existujici):
        if id_ not in vlastni_id and klic.strip().casefold() in obsazene:
            raise ValueError(f"Klíč „{klic}“ už má jiná kategorie (velikost písmen se neliší).")


def zkrat_klic_kategorie(klic: str | None) -> str | None:
    """Klic zkraceny na delku sloupce (bez mezer na konci), prazdny na None.

    Pro data, ktera vznikla driv, nez formular delku hlidal - dnes zaloha akce
    z ostre databaze. Klic je jen stale oznaceni pro vystup AI a AI ho dostava
    pri kazdem cteni znovu, takze zkraceni nic nerozbije."""
    if klic is None:
        return None
    return klic[:MAX_DELKA_KLICE_KATEGORIE].rstrip() or None


class Clovek(Zaklad):
    """Ciselnik lidi, kteri na akci neco platili nebo neco prijali.

    Driv se jmeno psalo u kazde uctenky rucne jako volny text. Na tiskopisu
    PPD/VPD je ten udaj dvakrat - v kolonce "Vyplaceno komu" a pod podpisovou
    linkou prijemce - takze preklep v nem je videt na papire. Ted se clovek
    zalozi jednou a pak uz se jen vybira ze seznamu, stejne jako kategorie.
    """

    __tablename__ = "clovek"
    # Jeden ucet smi patrit nejvyse jednomu clovku. Deklarovano i tady (ne jen
    # v migraci f1a2b3c4d5e6), aby ho mel i testovaci `Zaklad.metadata.create_all()`
    # - jinak by testy nikdy nemohly narazit na kolizi, kterou realna databaze hlida.
    __table_args__ = (
        Index(
            "uq_clovek_uzivatel",
            "uzivatel_id",
            unique=True,
            sqlite_where=text("uzivatel_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    jmeno: Mapped[str] = mapped_column(String(120), unique=True)
    # Bydliste a kontakt jdou na cestovni prikaz (kolonky 2. Bydliste
    # a Kontakt). Nepovinne - u vetsiny lidi v ciselniku se nikdy nevyplni,
    # potrebuje je jen ten, kdo nekam jede.
    adresa: Mapped[str | None] = mapped_column(String(200), default=None)
    kontakt: Mapped[str | None] = mapped_column(String(120), default=None)
    poradi: Mapped[int] = mapped_column(Integer, default=0)
    # E-mail a napojeny ucet pro pozvanku a pozdeji pro e-podpisy. Jeden ucet smi patrit nejvyse jednomu clovku - hlida
    # partial unique index uq_clovek_uzivatel v migraci.
    email: Mapped[str | None] = mapped_column(String(200), default=None)
    uzivatel_id: Mapped[int | None] = mapped_column(
        ForeignKey("uzivatel.id", ondelete="SET NULL"), default=None
    )
    ucet: Mapped[Uzivatel | None] = relationship()


class Vozidlo(Zaklad):
    """Ciselnik aut, kterymi se jezdi na akce.

    Stejny princip jako Kategorie a Clovek: auto se zalozi jednou, pak uz se
    jen vybira na cestovnim prikazu. SPZ a spotrebu pise clovek rucne podle
    maleho technickeho prukazu - AI se do toho nezapojuje, jsou to dve cisla
    z uredniho papiru, ktera se zadavaji jednou za zivot auta. Fotky technickeho
    prukazu (VozidloFoto) jsou k nim doklad.
    """

    __tablename__ = "vozidlo"

    id: Mapped[int] = mapped_column(primary_key=True)
    nazev: Mapped[str] = mapped_column(String(120), unique=True)
    spz: Mapped[str | None] = mapped_column(String(20), default=None)
    # Prumerna spotreba dle technickeho prukazu, l/100 km. Nejsou to penize,
    # proto Setiny a ne Castka.
    spotreba: Mapped[Decimal | None] = mapped_column(Setiny, default=None)
    poradi: Mapped[int] = mapped_column(Integer, default=0)
    # Maly technicky prukaz je knizecka se dvema stranami, proto fotek muze
    # byt vic - jedna na kazdou stranu (nebo cele PDF).
    fotky: Mapped[list[VozidloFoto]] = relationship(
        back_populates="vozidlo",
        cascade="all, delete-orphan",
        order_by="VozidloFoto.poradi",
        lazy="selectin",
    )


class VozidloFoto(Zaklad):
    """Jedna fotka (strana) maleho technickeho prukazu. Doklad k SPZ
    a spotrebe - aplikace z ni nic necte, jen ji uschova."""

    __tablename__ = "vozidlo_foto"

    id: Mapped[int] = mapped_column(primary_key=True)
    vozidlo_id: Mapped[int] = mapped_column(ForeignKey("vozidlo.id", ondelete="CASCADE"))
    # Nazev souboru v data/uploads/ je uuid ze stejneho duvodu jako u skenu -
    # id se po smazani recykluje, uuid ne.
    soubor: Mapped[str] = mapped_column(String(300))
    puvodni_nazev: Mapped[str] = mapped_column(String(300))
    poradi: Mapped[int] = mapped_column(Integer, default=0)

    vozidlo: Mapped[Vozidlo] = relationship(back_populates="fotky")

    @property
    def otisk(self) -> str:
        """Znamka souboru do adresy nahledu, aby se dal cachovat. Viz Sken.otisk."""
        return Path(self.soubor).stem
