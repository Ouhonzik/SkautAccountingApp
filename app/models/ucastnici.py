"""Ucastnici akce (s dochazkou) a obdarovani s darky."""

from __future__ import annotations

import calendar
import datetime as dt
import json
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Zaklad
from app.models.konstanty import VEK_DITETE
from app.models.typy import Castka

if TYPE_CHECKING:
    from app.models.akce import Akce, PokladniRadek, Sken


class Ucastnik(Zaklad):
    """Radek z exportu ze h.skauting.cz.

    Obsahuje osobni udaje deti - nikdy se neposila do Gemini a nikdy nekonci
    v gitu.
    """

    __tablename__ = "ucastnik"

    id: Mapped[int] = mapped_column(primary_key=True)
    akce_id: Mapped[int] = mapped_column(ForeignKey("akce.id", ondelete="CASCADE"), index=True)
    poradi: Mapped[int] = mapped_column(Integer, default=0)
    jmeno: Mapped[str | None] = mapped_column(String(80), default=None)
    prijmeni: Mapped[str | None] = mapped_column(String(80), default=None)
    prezdivka: Mapped[str | None] = mapped_column(String(80), default=None)
    ulice: Mapped[str | None] = mapped_column(String(150), default=None)
    mesto: Mapped[str | None] = mapped_column(String(100), default=None)
    psc: Mapped[str | None] = mapped_column(String(20), default=None)
    datum_narozeni: Mapped[dt.date | None] = mapped_column(Date, default=None)
    # Oddil ucastnika (export z h.skauting.cz). Pozor: `Cestak.jednotka` je neco jineho -
    # cislo jednotky, ktera cestu plati.
    jednotka: Mapped[str | None] = mapped_column(String(50), default=None)
    # Prazdne = spocitat z dochazky. Vyplnene = rucni cislo, ktere ma prednost.
    osobodny: Mapped[int | None] = mapped_column(Integer, default=None)
    detodny: Mapped[int | None] = mapped_column(Integer, default=None)
    # Dny, kdy clovek CHYBEL, jako JSON seznam ISO dat. Prazdne = byl celou akci.
    # Uklada se nepritomnost, ne pritomnost, ze dvou duvodu: vychozi stav je
    # "vsichni byli vsechno" a odklikava se jen vyjimka, a kdyz se posune termin
    # akce, dochazka se nerozsype.
    dochazka_json: Mapped[str | None] = mapped_column(Text, default=None)
    # Poukazane financni prostredky - kazda cesta zvlast plus cislo dokladu.
    # Dotace je samostatna vec a neni totez co vouchery; zatim neni.
    hotove: Mapped[Decimal | None] = mapped_column(Castka, default=None)
    hotove_doklad: Mapped[str | None] = mapped_column(String(60), default=None)
    prevodem: Mapped[Decimal | None] = mapped_column(Castka, default=None)
    prevodem_doklad: Mapped[str | None] = mapped_column(String(60), default=None)
    vouchery: Mapped[Decimal | None] = mapped_column(Castka, default=None)
    vouchery_doklad: Mapped[str | None] = mapped_column(String(60), default=None)
    # Vratka se na rozdil od predchozich tri nikde neuklada jako cislo: zadava
    # se v pokladni knize (PokladniRadek.ucastnik_id) a `vratka` je soucet
    # jejich radku. Ucastnik muze dostat vic vratek a opis v sloupci by se pri
    # druhe vratce nebo smazani jednoho radku rozesel s knihou.
    vratka_doklad: Mapped[str | None] = mapped_column(String(60), default=None)

    akce: Mapped[Akce] = relationship(back_populates="ucastnici")
    # passive_deletes: radky knihy po smazani ucastnika jen ztrati vazbu (SET NULL
    # v databazi) - penize, ktere z pokladny odesly, nesmi zmizet.
    pokladni_radky: Mapped[list[PokladniRadek]] = relationship(
        back_populates="ucastnik", passive_deletes=True
    )

    @property
    def vratka(self) -> Decimal | None:
        """Soucet vratek ucastnikovi, tedy radku pokladni knihy s jeho vazbou.
        None, kdyz zadnou nema."""
        castky = [radek.castka for radek in self.pokladni_radky]
        return sum(castky, Decimal("0.00")) if castky else None

    @property
    def zaplaceno(self) -> Decimal:
        """Soucet vsech tri cest, kterymi mohly penize prijit."""
        return sum(
            (c for c in (self.hotove, self.prevodem, self.vouchery) if c is not None),
            Decimal("0.00"),
        )

    # --- dochazka ---

    @property
    def chybejici_dny(self) -> set[dt.date]:
        """Dny, kdy clovek chybel. Cte se z `dochazka_json`."""
        if not self.dochazka_json:
            return set()
        try:
            return {dt.date.fromisoformat(d) for d in json.loads(self.dochazka_json)}
        except ValueError, TypeError:
            return set()

    def zapis_chybejici(self, dny) -> None:
        """Ulozi dny nepritomnosti. Prazdne se uklada jako NULL, ne '[]'."""
        serazene = sorted(dny)
        self.dochazka_json = json.dumps([d.isoformat() for d in serazene]) if serazene else None

    def byl_pritomen(self, den: dt.date) -> bool:
        return den not in self.chybejici_dny

    # --- osobodny a detodny ---

    @property
    def osobodny_spoctene(self) -> int:
        """Pocet dnu akce, kdy tu clovek byl."""
        chybel = self.chybejici_dny
        return sum(1 for den in self.akce.dny if den not in chybel)

    def vek_k(self, den: dt.date) -> int | None:
        """Kolik let bylo cloveku k danemu dni. None, kdyz datum narozeni chybi."""
        if self.datum_narozeni is None:
            return None
        narozeniny = (self.datum_narozeni.month, self.datum_narozeni.day)
        if narozeniny == (2, 29) and not calendar.isleap(den.year):
            # Narozeny 29. 2. slavi v neprestupnem roce 28. 2. (K-115, SPEC §11).
            narozeniny = (2, 28)
        vek = den.year - self.datum_narozeni.year
        return vek - 1 if (den.month, den.day) < narozeniny else vek

    @property
    def je_dite(self) -> bool:
        """Detoden se pocita do 15 let, presne do 14,99 roku - ditetem je ten,
        komu k prvnimu dni akce jeste nebylo patnact. Kdo ma patnacte narozeniny
        v prubehu akce, pocita se za dite po celou akci.

        Bez data narozeni vek neznama - takovy clovek se za dite nepocita
        a tabulka na chybejici datum upozorni.
        """
        dny = self.akce.dny
        if not dny:
            return False
        vek = self.vek_k(dny[0])
        # Zaporny vek (datum narozeni po zacatku akce) dite neni - je to chyba v datech.
        return vek is not None and 0 <= vek < VEK_DITETE

    @property
    def detodny_spoctene(self) -> int:
        return self.osobodny_spoctene if self.je_dite else 0

    @property
    def osobodny_vysledne(self) -> int:
        """Rucni cislo ma prednost pred spoctenym."""
        return self.osobodny if self.osobodny is not None else self.osobodny_spoctene

    @property
    def detodny_vysledne(self) -> int:
        return self.detodny if self.detodny is not None else self.detodny_spoctene


class Obdarovany(Zaklad):
    """Kdo na akci neco dostal.

    Vzor: list Seznam_obdarovanych. Radek je jeden clovek - co dostal, visi
    na nem jako `dary` a muze toho byt vic. (Driv byl radek jeden dar a kdo
    dostal dve veci, byl v seznamu dvakrat. Zmenilo se to, kdyz se veci zacaly
    tahat z uctenek a rozdelovat pretahovanim - dve karticky u jednoho jmena
    jsou prehlednejsi nez tyz clovek dvakrat pod sebou.)

    Osobni udaje se sem pri pridani ze seznamu ucastniku **opisuji**, ne
    odkazuji. Duvod: import ucastniku nahrazuje cely seznam, takze odkaz by
    se pri kazdem novem importu zlomil. Diky kopii jde do seznamu pridat
    i clovek, ktery ucastnik vubec neni (host, vedouci ze sousedniho oddilu).

    Plati tu stejne pravidlo jako u ucastniku: jsou to udaje deti, nikdy
    neodchazi do Gemini a nikdy nekonci v gitu.
    """

    __tablename__ = "obdarovany"

    id: Mapped[int] = mapped_column(primary_key=True)
    akce_id: Mapped[int] = mapped_column(ForeignKey("akce.id", ondelete="CASCADE"), index=True)
    poradi: Mapped[int] = mapped_column(Integer, default=0)
    jmeno: Mapped[str | None] = mapped_column(String(80), default=None)
    prijmeni: Mapped[str | None] = mapped_column(String(80), default=None)
    prezdivka: Mapped[str | None] = mapped_column(String(80), default=None)
    ulice: Mapped[str | None] = mapped_column(String(150), default=None)
    mesto: Mapped[str | None] = mapped_column(String(100), default=None)
    psc: Mapped[str | None] = mapped_column(String(20), default=None)
    datum_narozeni: Mapped[dt.date | None] = mapped_column(Date, default=None)

    akce: Mapped[Akce] = relationship(back_populates="obdarovani")
    # Co clovek dostal. Jedina vec, ktera se ze seznamu ucastniku vzit neda.
    dary: Mapped[list[Dar]] = relationship(
        back_populates="obdarovany", order_by="Dar.poradi", lazy="selectin"
    )

    @classmethod
    def z_ucastnika(cls, ucastnik: Ucastnik, poradi: int) -> Obdarovany:
        """Novy radek s opsanymi udaji ucastnika. Vec zustava prazdna."""
        return cls(
            akce_id=ucastnik.akce_id,
            poradi=poradi,
            jmeno=ucastnik.jmeno,
            prijmeni=ucastnik.prijmeni,
            prezdivka=ucastnik.prezdivka,
            ulice=ucastnik.ulice,
            mesto=ucastnik.mesto,
            psc=ucastnik.psc,
            datum_narozeni=ucastnik.datum_narozeni,
        )

    @property
    def cele_jmeno(self) -> str:
        """Jmeno a prijmeni na jednom radku, bez prazdnych mezer navic."""
        return " ".join(c for c in (self.jmeno, self.prijmeni) if c)

    @property
    def veci(self) -> str:
        """Vsechno, co clovek dostal, na jednom radku: "mic, cepice, odznak".

        Takhle to jde do Excelu. Vzor strediska ma na kazdy dar vlastni radek,
        ale Honza chce cloveka v seznamu jednou - viz `excel_obdarovani`.
        """
        return ", ".join(dar.nazev for dar in self.dary)


class Dar(Zaklad):
    """Jedna vec, ktera se na akci rozdava.

    Vznika prectenim uctenky z kategorie vecnych daru: jeden kus = jeden radek,
    takze "3x mic" jsou tri dary. Jinak by nesla kazdy mic pretahnout jinemu
    cloveku a nesedelo by automaticke rozhozeni.

    Dokud je `obdarovany_id` prazdne, lezi vec v zasobniku a ceka, komu ji
    Honza da. Smazani cloveka ze seznamu vec nesmaze - vrati ji do zasobniku
    (ON DELETE SET NULL). Stejne tak smazani uctenky: papir zmizi, vec zustane.

    S penezi tohle nema nic spolecneho. Vydaj resi uctenka; dar je jen
    evidence, kdo co dostal, a do zadneho souctu nevstupuje.
    """

    __tablename__ = "dar"

    id: Mapped[int] = mapped_column(primary_key=True)
    akce_id: Mapped[int] = mapped_column(ForeignKey("akce.id", ondelete="CASCADE"), index=True)
    # Z ktere uctenky vec pochazi. Podle toho se pozna, ktere uctenky uz AI
    # cetla - druhe kliknuti na "Vytahnout veci" je preskoci.
    sken_id: Mapped[int | None] = mapped_column(
        ForeignKey("sken.id", ondelete="SET NULL"), default=None
    )
    obdarovany_id: Mapped[int | None] = mapped_column(
        ForeignKey("obdarovany.id", ondelete="SET NULL"), default=None, index=True
    )
    nazev: Mapped[str] = mapped_column(String(200))
    poradi: Mapped[int] = mapped_column(Integer, default=0)

    akce: Mapped[Akce] = relationship(back_populates="dary")
    sken: Mapped[Sken | None] = relationship()
    obdarovany: Mapped[Obdarovany | None] = relationship(back_populates="dary")
