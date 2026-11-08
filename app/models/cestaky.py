"""Cestovni prikazy a jejich jizdy."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import nastaveni
from app.db import Zaklad
from app.models.konstanty import NAZVY_PROSTREDKU, PROSTREDEK_AUTO, PROSTREDKY_NA_KM
from app.models.typy import Castka, Desetiny, Setiny

if TYPE_CHECKING:
    from app.models.akce import Akce, PokladniRadek, Sken, Slozka
    from app.models.ciselniky import Clovek, Vozidlo


class Cestak(Zaklad):
    """Cestovni prikaz - jedna cesta na akci a nahrada za ni.

    Driv se delal v h.skauting.cz a do aplikace se nahraval hotovy jako PDF.
    Na rozdil od h.skautingu, kde jsou vsechny cestaky za rok pohromade,
    patri tady kazdy cestak do sve akce.

    Adresa, kontakt, spotreba i cena za litr se pri zalozeni z ciselniku
    OPISUJI, neodkazuji se - stejne rozhodnuti jako u Obdarovany.z_ucastnika.
    Jakmile udaj jde na papir, nesmi ho zpetne zmenit pozdejsi oprava
    ciselniku: kdyby se u auta opravila spotreba, prepocital by se uz
    odevzdany cestak a castka na papire by prestala sedet s pokladni knihou.
    Vyjimka: oprava spotreby vozidla a ceny za litr z uctenky se
    promita do cestaku NEUZAVRENYCH akci (services/cestaky.py), uzavrene zustanou.

    Sazba nahrady se zamerne NEUKLADA. Pocita se za behu ze tri ulozenych
    vstupu (app/services/cestaky.py) - vychazi 6,854 Kc/km a Castka umi jen
    dve desetinna mista, takze ulozena by se tise zaokrouhlila na 6,85
    a radky by vysly o haleře niz.
    """

    __tablename__ = "cestak"

    id: Mapped[int] = mapped_column(primary_key=True)
    akce_id: Mapped[int] = mapped_column(ForeignKey("akce.id", ondelete="CASCADE"), index=True)

    ucel_cesty: Mapped[str] = mapped_column(String(200))
    misto: Mapped[str | None] = mapped_column(String(200), default=None)
    spolucestujici: Mapped[str | None] = mapped_column(String(300), default=None)
    poznamka: Mapped[str | None] = mapped_column(Text, default=None)
    # Cislo jednotky, ktera cestu plati (vychozi z nastaveni). Neplest s `Ucastnik.jednotka`,
    # to je oddil ditete.
    jednotka: Mapped[str | None] = mapped_column(
        String(30), default=lambda: nastaveni.stredisko_jednotka
    )

    cestujici_id: Mapped[int | None] = mapped_column(
        ForeignKey("clovek.id", ondelete="SET NULL"), default=None
    )
    adresa: Mapped[str | None] = mapped_column(String(200), default=None)
    kontakt: Mapped[str | None] = mapped_column(String(120), default=None)

    vozidlo_id: Mapped[int | None] = mapped_column(
        ForeignKey("vozidlo.id", ondelete="SET NULL"), default=None
    )
    spotreba: Mapped[Decimal | None] = mapped_column(Setiny, default=None)

    # Palivova uctenka, kterou je cena za litr dolozena. Je to priloha
    # (Sken.je_priloha), takze se nikde nepocita - jen se vypisuje.
    palivova_uctenka_id: Mapped[int | None] = mapped_column(
        ForeignKey("sken.id", ondelete="SET NULL"), default=None
    )
    # Slozka, ve ktere palivova uctenka lezela, nez cestak prisel do vydaju (tam se
    # lepi na doklad cestaku, ne do slozky). Po odebrani z vydaju se do ni vrati.
    # SET NULL, ne obycejne cislo: smazana slozka se pak nemuze splest s novou, ktera
    # po ni dostala stejne id.
    puvodni_slozka_palivovky_id: Mapped[int | None] = mapped_column(
        ForeignKey("slozka.id", ondelete="SET NULL"), default=None
    )
    cena_za_litr: Mapped[Decimal | None] = mapped_column(Castka, default=None)
    # Zakladni nahrada za 1 km podle vyhlasky. Meni se jednou za rok, proto
    # rucni pole predvyplnene z posledniho cestaku, ne nastaveni aplikace.
    opotrebeni: Mapped[Decimal | None] = mapped_column(Castka, default=None)

    poradi: Mapped[int] = mapped_column(Integer, default=0)
    vytvoreno: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    akce: Mapped[Akce] = relationship(back_populates="cestaky")
    cestujici: Mapped[Clovek | None] = relationship()
    vozidlo: Mapped[Vozidlo | None] = relationship()
    palivova_uctenka: Mapped[Sken | None] = relationship()
    puvodni_slozka_palivovky: Mapped[Slozka | None] = relationship(
        foreign_keys=[puvodni_slozka_palivovky_id]
    )
    jizdy: Mapped[list[Jizda]] = relationship(
        back_populates="cestak",
        cascade="all, delete-orphan",
        order_by="Jizda.poradi",
        lazy="selectin",
    )
    # Radek v pokladni knize. Dokud je None, je cestak "Rozpracovany"; jakmile
    # existuje, je "Ve vydajich". Zvlastni sloupec na stav nemame schvalne -
    # dva zdroje pravdy by se driv nebo pozdeji rozesly.
    pokladni_radek: Mapped[PokladniRadek | None] = relationship(
        back_populates="cestak", cascade="all, delete-orphan", uselist=False
    )

    @property
    def je_ve_vydajich(self) -> bool:
        return self.pokladni_radek is not None


class Jizda(Zaklad):
    """Jeden usek cesty - radek v rozpisu na cestovnim prikazu."""

    __tablename__ = "jizda"

    id: Mapped[int] = mapped_column(primary_key=True)
    cestak_id: Mapped[int] = mapped_column(ForeignKey("cestak.id", ondelete="CASCADE"), index=True)
    datum: Mapped[dt.date | None] = mapped_column(Date, default=None)
    odkud: Mapped[str | None] = mapped_column(String(150), default=None)
    kam: Mapped[str | None] = mapped_column(String(150), default=None)
    prostredek: Mapped[str] = mapped_column(String(20), default=PROSTREDEK_AUTO)
    # Kilometry na jedno desetinne misto (12,4), v databazi cele desetiny.
    km: Mapped[Decimal | None] = mapped_column(Desetiny, default=None)
    # Zaplacena cena jizdenky u vlaku a autobusu, kde se km nepocitaji.
    jizdne: Mapped[Decimal | None] = mapped_column(Castka, default=None)
    # Souradnice konce useku, aby sla trasa po znovuotevreni prekreslit v mape.
    # Float schvalne - pravidlo "penize nikdy jako float" se na zemepisnou
    # delku nevztahuje a presnost na centimetry bohate staci.
    z_lat: Mapped[float | None] = mapped_column(Float, default=None)
    z_lon: Mapped[float | None] = mapped_column(Float, default=None)
    do_lat: Mapped[float | None] = mapped_column(Float, default=None)
    do_lon: Mapped[float | None] = mapped_column(Float, default=None)
    poradi: Mapped[int] = mapped_column(Integer, default=0)

    cestak: Mapped[Cestak] = relationship(back_populates="jizdy")

    @property
    def je_na_km(self) -> bool:
        """Pocita se nahrada z kilometru, nebo je to zaplacena jizdenka?"""
        return self.prostredek in PROSTREDKY_NA_KM

    @property
    def nazev_prostredku(self) -> str:
        return NAZVY_PROSTREDKU.get(self.prostredek, self.prostredek)
