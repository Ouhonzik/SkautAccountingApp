"""Akce, jeji slozky, skeny, prijmy a pokladni radky - jadro vyuctovani."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Zaklad
from app.models.konstanty import (
    DRUH_UCTENKA,
    MIN_CASTI_ROZDELENI,
    SMER_PRIJEM,
    SMER_VYDEJ,
    STAV_ROZPRACOVANA,
)
from app.models.typy import Castka

if TYPE_CHECKING:
    from app.models.cestaky import Cestak
    from app.models.ciselniky import Clovek, Kategorie
    from app.models.ucastnici import Dar, Obdarovany, Ucastnik


class Akce(Zaklad):
    __tablename__ = "akce"

    id: Mapped[int] = mapped_column(primary_key=True)
    nazev: Mapped[str] = mapped_column(String(200))
    datum_vyuctovani: Mapped[dt.date | None] = mapped_column(Date, default=None)
    # Termin akce - prvni a posledni den. Z nej se sestavuje mrizka dochazky
    # a hlida se datum uctenek. Prazdne u akci zalozenych driv, nez pole vzniklo.
    datum_od: Mapped[dt.date | None] = mapped_column(Date, default=None)
    datum_do: Mapped[dt.date | None] = mapped_column(Date, default=None)
    stav: Mapped[str] = mapped_column(String(20), default=STAV_ROZPRACOVANA)
    vytvoreno: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    # Vlozi se doprostred cisla dokladu: "VPD 01" -> "VPD 2026/01 01". Tiskne se
    # jen na tiskopis PPD/VPD; kniha, slozky ani Excely ho nemaji.
    # Prazdne dokud se Honza nedomluvi s hospodarem na podobe cislovani.
    prefix_dokladu: Mapped[str | None] = mapped_column(String(20), default=None)
    # Kdo akci vede pokladnu. Natiskne se malym pismem pod podpisovou linku
    # na tiskopisech PPD/VPD (SPEC 19); jinde se nepouziva. Vybira se
    # z ciselniku lidi ze stejneho duvodu jako "kdo platil" - je to jmeno,
    # ktere jde na papir, takze preklep v nem je videt az na vytistenem dokladu.
    pokladnik_id: Mapped[int | None] = mapped_column(
        ForeignKey("clovek.id", ondelete="SET NULL"), default=None
    )
    # Kolik MB souboru smi mit akce dohromady. Prazdne = plati vychozi
    # hodnota z .env (`vychozi_misto_akce_mb`) - meni jen spravce, u vetsiny
    # akci zustava prazdne. Viz app/services/soubory.py:limit_mista_akce_mb.
    misto_mb_limit: Mapped[int | None] = mapped_column(Integer, default=None)

    slozky: Mapped[list[Slozka]] = relationship(
        back_populates="akce", cascade="all, delete-orphan", order_by="Slozka.poradi"
    )
    skeny: Mapped[list[Sken]] = relationship(back_populates="akce", cascade="all, delete-orphan")
    prijmy: Mapped[list[Prijem]] = relationship(back_populates="akce", cascade="all, delete-orphan")
    ucastnici: Mapped[list[Ucastnik]] = relationship(
        back_populates="akce", cascade="all, delete-orphan", order_by="Ucastnik.poradi"
    )
    pokladni_radky: Mapped[list[PokladniRadek]] = relationship(
        back_populates="akce",
        cascade="all, delete-orphan",
        order_by="PokladniRadek.poradi",
    )
    obdarovani: Mapped[list[Obdarovany]] = relationship(
        back_populates="akce", cascade="all, delete-orphan", order_by="Obdarovany.poradi"
    )
    dary: Mapped[list[Dar]] = relationship(
        back_populates="akce", cascade="all, delete-orphan", order_by="Dar.poradi"
    )
    cestaky: Mapped[list[Cestak]] = relationship(
        back_populates="akce", cascade="all, delete-orphan", order_by="Cestak.poradi"
    )
    # Vztah se jmenuje jinak nez vlastnost `pokladnik` niz - ta vraci jmeno,
    # protoze pod nim ho cte sablona dokladu.
    pokladnik_clovek: Mapped[Clovek | None] = relationship()

    @property
    def pokladnik(self) -> str | None:
        return self.pokladnik_clovek.jmeno if self.pokladnik_clovek else None

    @property
    def ma_termin(self) -> bool:
        """Ma akce vyplneny oba konce terminu? Bez toho nejde dochazka ani
        hlidani dat uctenek - obe potrebuji vedet, ktere dny akce trvala."""
        return self.datum_od is not None and self.datum_do is not None

    @property
    def dny(self) -> list[dt.date]:
        """Vsechny dny akce od prvniho do posledniho vcetne. Sloupce mrizky
        dochazky. Bez terminu prazdny seznam."""
        if self.datum_od is None or self.datum_do is None or self.datum_do < self.datum_od:
            return []
        pocet = (self.datum_do - self.datum_od).days + 1
        return [self.datum_od + dt.timedelta(days=i) for i in range(pocet)]

    @property
    def soucet_vydaju(self) -> Decimal:
        """Vydaje akce: slozky s uctenkami plus rucni vydajove radky knihy.

        Rucni radky se sem zapocitavaji od 12. 9. 2026. Driv scital jen slozky,
        takze cestak ani vratka ucastnikovi nebyly v "Balance" videt - a to jsou
        tisice korun, ktere z pokladny doopravdy odesly. Bilance tim zacala
        odpovidat pokladni knize.

        Prevod zustatku do pokladny pristavu se sem nepocita: v databazi nikdy
        nevznika, dopocitava se az pri sestaveni knihy (pokladni_kniha). Kdyby
        se zapocital, vysla by bilance vzdycky nula a prestala by cokoli rikat.
        """
        slozky = sum((s.soucet for s in self.slozky), Decimal("0.00"))
        rucni = sum(
            (r.castka for r in self.pokladni_radky if not r.je_prijem),
            Decimal("0.00"),
        )
        return slozky + rucni

    @property
    def soucet_prijmu(self) -> Decimal:
        prijmy = sum((p.castka for p in self.prijmy), Decimal("0.00"))
        rucni = sum((r.castka for r in self.pokladni_radky if r.je_prijem), Decimal("0.00"))
        return prijmy + rucni

    @property
    def bilance(self) -> Decimal:
        """Jak to pocita hospodar: prijmy minus vydaje."""
        return self.soucet_prijmu - self.soucet_vydaju


class Slozka(Zaklad):
    """To, cemu hospodar rika doklad.

    Nova slozka vznika vzdy, kdyz se lisi kategorie NEBO kdo platil - kazdemu
    cloveku se proplaci jeho slozka zvlast.
    """

    __tablename__ = "slozka"

    id: Mapped[int] = mapped_column(primary_key=True)
    akce_id: Mapped[int] = mapped_column(ForeignKey("akce.id", ondelete="CASCADE"), index=True)
    nazev: Mapped[str] = mapped_column(String(200))
    kategorie_id: Mapped[int | None] = mapped_column(
        ForeignKey("kategorie.id", ondelete="SET NULL"), default=None
    )
    clovek_id: Mapped[int | None] = mapped_column(
        ForeignKey("clovek.id", ondelete="SET NULL"), default=None
    )
    # Co to realne bylo - kolonka "Ucel platby" na tiskopisu PPD/VPD. Nazev
    # slozky se tam davat neda: "Jidlo - Lednova vikendovka" je nazev prihradky
    # na disku, ne ucel platby. Prazdne pole znamena "vezmi navrh".
    ucel_platby: Mapped[str | None] = mapped_column(String(200), default=None)
    poradi: Mapped[int] = mapped_column(Integer, default=0)

    akce: Mapped[Akce] = relationship(back_populates="slozky")
    kategorie: Mapped[Kategorie | None] = relationship()
    clovek: Mapped[Clovek | None] = relationship()
    # lazy="selectin": ucetenky vsech nactenych slozek se nactou jednim dotazem
    # `WHERE slozka_id IN (...)`, ne po jedne. Soucet, pokladni kniha i doklady
    # sahaji na `slozka.skeny` u kazde slozky - bez toho je to dotaz na slozku.
    skeny: Mapped[list[Sken]] = relationship(
        back_populates="slozka", order_by="Sken.poradi", lazy="selectin"
    )

    @property
    def platic(self) -> Clovek | None:
        """Kdo za slozku platil - a komu se tedy na dokladu vyplaci.

        Rozhoduji uctenky, ne pole u slozky. Pole `clovek_id` je jen opis:
        vyplni ho automaticke trideni podle prvni uctenky a uz se samo nemeni.
        Kdyz se pak u uctenky prepise, kdo platil, zustavalo ve slozce stare
        jmeno - a to se tisklo na doklad, i kdyz uz nikde jinde v aplikaci
        nebylo. Proto se pole slozky bere az jako zaloha: kdyz uctenky mlci
        (nikdo u nich vyplneny neni) nebo se neshodnou (rucne slouceny papiry
        vic lidi), plati to, co je u slozky.

        Prilohy se nepocitaji - stejne jako do souctu. Papir dolozeny
        k uctence neni vydaj, takze o platici nerozhoduje.
        """
        lide = {s.clovek.id: s.clovek for s in self.skeny_s_radkem if s.clovek is not None}
        if len(lide) == 1:
            return next(iter(lide.values()))
        return self.clovek

    @property
    def kdo_platil(self) -> str | None:
        """Jmeno platice. Zustava pod puvodnim nazvem, protoze pod nim ho
        cte Excel pro hospodare, pokladni kniha i zaloha."""
        platic = self.platic
        return platic.jmeno if platic else None

    @property
    def skeny_s_radkem(self) -> list[Sken]:
        """Skeny, ktere maji vlastni radek v Excelu - tedy krome priloh."""
        return [s for s in self.skeny if not s.je_priloha]

    @property
    def prilohy_bez_uctenky(self) -> list[Sken]:
        """Prilohy ve slozce, ktere nepatri k zadne zdejsi uctence.

        Papir se na doklad nalepi tak jako tak, takze se na nem musi objevit -
        jen ne pod konkretni uctenkou. Sem spadne i priloha, jejiz uctenka lezi
        v jine slozce.
        """
        s_radkem = {s.id for s in self.skeny_s_radkem}
        return [s for s in self.skeny if s.je_priloha and s.priloha_k_id not in s_radkem]

    @property
    def soucet(self) -> Decimal:
        """Soucet slozky. Prilohy se do nej nepocitaji."""
        return sum(
            (s.castka for s in self.skeny_s_radkem if s.castka is not None),
            Decimal("0.00"),
        )

    @property
    def navrh_ucelu(self) -> str:
        """Ucel platby poskladany z kategorie a obchodniku ze skenu.

        Vyjde z toho "Jidlo - Lidl, Albert". Prilohy se do nej nepocitaji,
        stejne jako do souctu. Kdyz neni z ceho skladat, zbyde nazev slozky -
        porad lepsi nez prazdna kolonka na papire.

        U rozdelene uctenky se pocitaji vsechny kategorie z jejiho rozpisu, ne
        jen ta hlavni: kdyz je na dokladu material, ma byt v ucelu platby videt.
        """
        obchodnici: list[str] = []
        kategorie: list[str] = []
        for sken in self.skeny_s_radkem:
            jmeno = (sken.obchodnik or "").strip()
            if jmeno and jmeno not in obchodnici:
                obchodnici.append(jmeno)
            for k in sken.kategorie_z_casti:
                if k.nazev not in kategorie:
                    kategorie.append(k.nazev)

        # Slozka z automatickeho trideni ma kategorii svou; rucne zalozena nema
        # zadnou, tak se vezme z uctenek, ktere v ni lezi. Kdyz ale uctenky
        # zasahuji do vic kategorii, nez kolik jich drzi slozka, rozhoduji ony:
        # rozdelena uctenka udela ze slozky "jen jidlo" slozku s materialem
        # a na dokladu ma byt videt, za co se opravdu platilo.
        #
        # Kategorie se oddeluji carkou jako obchodnici - pomlcka oddeluje az
        # kategorie od obchodniku. "Jidlo — Material — Albert" by se cetlo jako
        # tri rovnocenne veci, ackoli material je jen dalsi kategorie.
        if self.kategorie and len(kategorie) <= 1:
            casti = [self.kategorie.nazev]
        elif kategorie:
            casti = [", ".join(kategorie)]
        else:
            casti = []
        if obchodnici:
            casti.append(", ".join(obchodnici))
        return " — ".join(casti) or self.nazev

    @property
    def ucel_platby_vysledny(self) -> str:
        """Rucne vepsany ucel ma prednost pred navrhem."""
        return self.ucel_platby or self.navrh_ucelu


class Sken(Zaklad):
    """Jeden nahrany soubor. Muze mit vic stranek a odpovida jednomu radku v Excelu.

    Vyjimkou je priloha (je_priloha=True) - ta je v ZIPu, ale radek v Excelu nema.
    Typicky uctenka za benzin u cestaku: dokladá cenu za litr, ne vydaj.

    Priloha se da pripnout k uctence, ke ktere patri (`priloha_k`). Nikde se tim
    nic nepricte - vazba se projevi jen na tiskopisu PPD/VPD, kde se pod uctenkou
    vypise, ktere papiry k ni jsou nalepene.
    """

    __tablename__ = "sken"

    id: Mapped[int] = mapped_column(primary_key=True)
    akce_id: Mapped[int] = mapped_column(ForeignKey("akce.id", ondelete="CASCADE"), index=True)
    slozka_id: Mapped[int | None] = mapped_column(
        ForeignKey("slozka.id", ondelete="SET NULL"), default=None, index=True
    )

    # soubor na disku
    soubor: Mapped[str] = mapped_column(String(300))
    puvodni_nazev: Mapped[str] = mapped_column(String(300))
    pocet_stranek: Mapped[int] = mapped_column(Integer, default=1)

    # co precetla AI a co z toho Honza potvrdil
    nazev: Mapped[str | None] = mapped_column(String(200), default=None)
    druh: Mapped[str] = mapped_column(String(30), default=DRUH_UCTENKA)
    obchodnik: Mapped[str | None] = mapped_column(String(150), default=None)
    datum: Mapped[dt.date | None] = mapped_column(Date, default=None)
    castka: Mapped[Decimal | None] = mapped_column(Castka, default=None)
    cena_za_litr: Mapped[Decimal | None] = mapped_column(Castka, default=None)
    clovek_id: Mapped[int | None] = mapped_column(
        ForeignKey("clovek.id", ondelete="SET NULL"), default=None
    )
    kategorie_id: Mapped[int | None] = mapped_column(
        ForeignKey("kategorie.id", ondelete="SET NULL"), default=None
    )

    je_priloha: Mapped[bool] = mapped_column(Boolean, default=False)
    # Ke ktere uctence priloha patri. Vyplnene jen u prilohy; smazani te uctenky
    # prilohu nesmaze, jen ji odpoji (SET NULL) - papir na disku zustava.
    priloha_k_id: Mapped[int | None] = mapped_column(
        ForeignKey("sken.id", ondelete="SET NULL"), default=None, index=True
    )
    zkontrolovano: Mapped[bool] = mapped_column(Boolean, default=False)
    # Ztisene varovani o datu mimo termin akce. Uctenky mimo termin se realne
    # objevuji a normalne se uctuji, takze varovani musi jit utlumit - zustane
    # videt, ale prestane kricet.
    varovani_ztiseno: Mapped[bool] = mapped_column(Boolean, default=False)
    jistota: Mapped[str | None] = mapped_column(String(10), default=None)
    # Kolikrat po sobe se cteni pres AI nepovedlo (vadna odpoved, poskozeny
    # soubor). Po MAX_POKUSU_CTENI se sken z davky vynecha, jinak by se u placeneho
    # klice cetl donekonecna a zabiral misto dalsim. Uspech a tlacitko
    # "Zkusit znovu" ho vynuluji.
    pokusy_cteni: Mapped[int] = mapped_column(Integer, server_default="0", default=0)
    # Veci z uctenky uz AI vytahla, i kdyz zadna nenasla ("0 veci" je platny
    # vysledek). Bez toho by se uctenka bez veci cetla pri kazdem kliknuti znovu.
    dary_precteny: Mapped[bool] = mapped_column(Boolean, server_default=false(), default=False)
    poznamka: Mapped[str | None] = mapped_column(Text, default=None)
    rozpis_json: Mapped[str | None] = mapped_column(Text, default=None)
    poradi: Mapped[int] = mapped_column(Integer, default=0)
    # Rucni poradi radku v pokladni knize (sipky nahoru/dolu). Prazdne znamena
    # "nikdo s knihou nehybal" a radek se radi podle data. Cislo dokladu na tomhle
    # nezavisi - to zustava uctence, i kdyz se posune na druhy konec knihy.
    poradi_v_knize: Mapped[int | None] = mapped_column(Integer, default=None)

    akce: Mapped[Akce] = relationship(back_populates="skeny")
    slozka: Mapped[Slozka | None] = relationship(back_populates="skeny")
    kategorie: Mapped[Kategorie | None] = relationship()
    clovek: Mapped[Clovek | None] = relationship()
    # Odkaz do teze tabulky: sken ukazuje na jiny sken. `remote_side` rika,
    # ktera strana je ta odkazovana - bez toho by SQLAlchemy nepoznala, jestli
    # je vztah "jedna priloha k uctence", nebo naopak.
    priloha_k: Mapped[Sken | None] = relationship(back_populates="prilohy", remote_side="Sken.id")
    # lazy="selectin" u priloh a casti: kniha, doklady i stranka Uctenky na ne
    # sahaji u kazde uctenky (`nalepene_prilohy`, `je_rozdelena`, `podily`).
    # `join_depth=3` je u priloh nutne: je to vazba skenu na sken, a SQLAlchemy
    # u vazby na sebe sama prestane nacitat dopredu, jakmile cil uz jednou v rade
    # byl (sken ze slozky) - priloha by se pak zase nacitala po jedne. Tri urovne
    # staci na nejdelsi cestu: akce -> slozky -> uctenky -> prilohy.
    prilohy: Mapped[list[Sken]] = relationship(
        back_populates="priloha_k", order_by="Sken.poradi", lazy="selectin", join_depth=3
    )
    casti: Mapped[list[CastSkenu]] = relationship(
        back_populates="sken",
        cascade="all, delete-orphan",
        order_by="CastSkenu.poradi",
        lazy="selectin",
    )

    @property
    def kdo_platil(self) -> str | None:
        """Jmeno z ciselniku - viz Slozka.kdo_platil."""
        return self.clovek.jmeno if self.clovek else None

    @property
    def otisk(self) -> str:
        """Znamka nahraneho souboru do adresy nahledu (`?v=`).

        Jmeno souboru na disku je nahodne uuid, takze se nikdy neopakuje.
        Id skenu ano - po smazani ho dostane novy sken - a prohlizec by pod
        touz adresou drzel v pameti obrazek davno smazane uctenky.
        """
        return Path(self.soubor).stem

    @property
    def nalepene_prilohy(self) -> list[Sken]:
        """Prilohy teto uctenky, ktere lezi v teze slozce.

        Doklad ma vyjmenovat presne ty papiry, ktere se na nej lepi. Priloha
        odlozena do jine slozky se proto vypise az u ni, ne tady.
        """
        return [p for p in self.prilohy if p.slozka_id == self.slozka_id]

    @property
    def stranky_archu(self) -> list[dict]:
        """Rozpis archu jizdenek po strankach (sloupec `rozpis_json`). Prazdny seznam u vsech ostatnich.

        NE rozdeleni uctenky po kategoriich - to jsou `casti` (`kategorie_z_casti`).
        """
        if not self.rozpis_json:
            return []
        try:
            return json.loads(self.rozpis_json)
        except ValueError:
            return []

    @property
    def je_rozdelena(self) -> bool:
        """Ma uctenka rozpis po kategoriich?

        Jedina cast rozdeleni neni - to je tataz informace jako `kategorie_id`,
        jen zapsana dvakrat. Deli se az od `MIN_CASTI_ROZDELENI` (dvou).
        """
        return len(self.casti) >= MIN_CASTI_ROZDELENI

    @property
    def soucet_casti(self) -> Decimal:
        return sum((c.castka for c in self.casti if c.castka is not None), Decimal("0.00"))

    @property
    def rozdeleni_sedi(self) -> bool:
        """Soucet casti se musi rovnat castce uctenky.

        Cast neni dalsi vydaj, jen rozpis toho stejneho - kdyz se rozejdou,
        ukazovalo by vyuctovani po kategoriich neco jineho nez celkem.
        """
        if not self.je_rozdelena:
            return True
        return self.castka is not None and self.soucet_casti == self.castka

    @property
    def kategorie_z_casti(self) -> list[Kategorie]:
        """Kategorie uctenky pro souhrny a ucel platby.

        U rozdelene uctenky vsechny z rozpisu, jinak ta jedina jeji. Bez tohohle
        by souctu po kategoriich zmizel material schovany na uctence za jidlo.
        """
        if not self.je_rozdelena:
            return [self.kategorie] if self.kategorie else []
        videne: dict[int, Kategorie] = {}
        for cast in self.casti:
            if cast.kategorie is not None:
                videne.setdefault(cast.kategorie.id, cast.kategorie)
        return list(videne.values())

    @property
    def podily(self) -> list[tuple[Kategorie | None, Decimal]]:
        """Kolik penez uctenka pridava ktere kategorii.

        U nerozdelene uctenky jedina dvojice s celou castkou - diky tomu s tim
        souhrny pracuji stejne, at je uctenka rozdelena, nebo ne.
        """
        if not self.je_rozdelena:
            return [(self.kategorie, self.castka or Decimal("0.00"))]
        return [(c.kategorie, c.castka or Decimal("0.00")) for c in self.casti]


class CastSkenu(Zaklad):
    """Jedna kategorie na rozdelene uctence.

    Na paragonu z Alberta byva vedle jidla i material - toaletni papir, sampon.
    Cast rika, kolik z celkove castky uctenky patri ktere kategorii. Soucet
    casti se musi rovnat `Sken.castka`: cast neni dalsi vydaj, jen rozpis toho
    stejneho. Do souctu slozky, do bilance ani do zustatku v pokladni knize
    proto nevstupuje - tam dal plati `Sken.castka`.

    Prazdny seznam casti znamena "uctenka se nedeli" a plati `Sken.kategorie_id`.
    """

    __tablename__ = "cast_skenu"

    id: Mapped[int] = mapped_column(primary_key=True)
    sken_id: Mapped[int] = mapped_column(ForeignKey("sken.id", ondelete="CASCADE"), index=True)
    kategorie_id: Mapped[int | None] = mapped_column(
        ForeignKey("kategorie.id", ondelete="SET NULL"), default=None
    )
    castka: Mapped[Decimal | None] = mapped_column(Castka, default=None)
    # Co na uctence do teto kategorie spadlo ("toaletní papír, šampon").
    # Jen pro cloveka, nikde se z toho nic nepocita.
    popis: Mapped[str | None] = mapped_column(String(200), default=None)
    poradi: Mapped[int] = mapped_column(Integer, default=0)

    sken: Mapped[Sken] = relationship(back_populates="casti")
    kategorie: Mapped[Kategorie | None] = relationship()

    @property
    def nazev_kategorie(self) -> str:
        return self.kategorie.nazev if self.kategorie else "Bez kategorie"


class Prijem(Zaklad):
    __tablename__ = "prijem"

    id: Mapped[int] = mapped_column(primary_key=True)
    akce_id: Mapped[int] = mapped_column(ForeignKey("akce.id", ondelete="CASCADE"), index=True)
    popis: Mapped[str] = mapped_column(String(200), default="Účastnické poplatky")
    castka: Mapped[Decimal] = mapped_column(Castka)
    # Kdo penize vybral = "Prijato od" na tiskopisu PPD. Stejne jako u uctenek
    # se jmeno vybira z ciselniku lidi - preklep v nem je videt az na
    # papire. Clovek s prijmem se z ciselniku nesmaze (`ciselniky.pouziti_lidi`),
    # SET NULL je jen pojistka.
    clovek_id: Mapped[int | None] = mapped_column(
        ForeignKey("clovek.id", ondelete="SET NULL"), default=None
    )
    # Starý volny text z doby pred ciselnikem. Migrace do `clovek_id` prevedla
    # vsechno, co se jmenem shodlo s clovekem v ciselniku; tady zustal jen text,
    # ktery se nenasel. Nove prijmy ho nenastavuji.
    kdo: Mapped[str | None] = mapped_column(String(120), default=None)
    # Den, kdy penize prisly do pokladny - podle nej se radi pokladni kniha.
    datum: Mapped[dt.date | None] = mapped_column(Date, default=None)
    poradi: Mapped[int] = mapped_column(Integer, default=0)
    # Rucni poradi v pokladni knize - viz Sken.poradi_v_knize.
    poradi_v_knize: Mapped[int | None] = mapped_column(Integer, default=None)

    akce: Mapped[Akce] = relationship(back_populates="prijmy")
    # joined: prijmu bywa par na akci a jmeno cte pokladni kniha, doklady i
    # Excel u kazdeho - jinak by to byl dotaz na kazdeho cloveka zvlast.
    clovek: Mapped[Clovek | None] = relationship(lazy="joined")

    @property
    def kdo_vybral(self) -> str | None:
        """Jmeno z ciselniku; u prijmu, ktery se v migraci nenasel, puvodni text."""
        return self.clovek.jmeno if self.clovek else self.kdo


class PokladniRadek(Zaklad):
    """Radek pokladni knihy, ke kteremu neni nahrana zadna uctenka.

    Typicky odvod zustatku hospodari nebo prevzata zaloha - pohyb hotovosti,
    ktery se nikde jinde v aplikaci neeviduje, ale bez nej by kniha nevysla.
    """

    __tablename__ = "pokladni_radek"
    # Jeden cestak = nejvyse jeden radek v knize. Bez toho by dvojklik nebo dve
    # okna mohla zalozit dva vydaje a castka by se v bilanci pocitala dvakrat.
    # Castecny index, aby radky bez cestaku (NULL) navzajem nekolidovaly.
    __table_args__ = (
        Index(
            "uq_pokladni_radek_cestak",
            "cestak_id",
            unique=True,
            sqlite_where=text("cestak_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    akce_id: Mapped[int] = mapped_column(ForeignKey("akce.id", ondelete="CASCADE"), index=True)
    datum: Mapped[dt.date | None] = mapped_column(Date, default=None)
    ucel: Mapped[str] = mapped_column(String(200))
    smer: Mapped[str] = mapped_column(String(10), default=SMER_VYDEJ)
    castka: Mapped[Decimal] = mapped_column(Castka)
    poradi: Mapped[int] = mapped_column(Integer, default=0)
    # Rucni poradi v pokladni knize - viz Sken.poradi_v_knize.
    poradi_v_knize: Mapped[int | None] = mapped_column(Integer, default=None)
    # Vyplneno jen u vratky ucastnikovi. SET NULL, ne CASCADE - kdyz se
    # ucastnik smaze nebo znovu naimportuje, penize, ktere uz odesly z
    # pokladny, nesmi zmizet beze stopy, jen ztrati vazbu.
    ucastnik_id: Mapped[int | None] = mapped_column(
        ForeignKey("ucastnik.id", ondelete="SET NULL"), default=None
    )
    # Vyplneno jen u radku, ktery vznikl z cestaku. CASCADE, ne SET NULL -
    # na rozdil od vratky ucastnikovi tady zadne penize z pokladny neodesly,
    # cestak se teprve chystal proplatit. Smazany cestak ma radek zrusit.
    cestak_id: Mapped[int | None] = mapped_column(
        ForeignKey("cestak.id", ondelete="CASCADE"), default=None
    )

    akce: Mapped[Akce] = relationship(back_populates="pokladni_radky")
    ucastnik: Mapped[Ucastnik | None] = relationship(back_populates="pokladni_radky")
    cestak: Mapped[Cestak | None] = relationship(back_populates="pokladni_radek")

    @property
    def je_prijem(self) -> bool:
        return self.smer == SMER_PRIJEM

    @property
    def je_z_cestaku(self) -> bool:
        """Radek, ktery se v knize needituje - jeho castka je soucet jizd."""
        return self.cestak_id is not None
