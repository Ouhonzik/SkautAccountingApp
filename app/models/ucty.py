"""Uzivatelske ucty, odeslane e-maily a jednorazove tokeny."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Zaklad
from app.models.konstanty import ROLE_PODEPISUJICI, ROLE_REVIZOR, ROLE_SPRAVCE, ROLE_UZIVATEL


class Uzivatel(Zaklad):
    """Ucet, kterym se lide hlasi do aplikace.

    Ucet jde smazat jen deaktivovany (`aktivni=False`), a to i s jeho zaznamy
    v logu cinnosti a ve statistice cteni AI (`services/uzivatele.py:smaz_ucet`) -
    smazani je vedome nevratne. Dokud ucet existuje, deaktivace je bezny zpusob,
    jak ho zablokovat.
    """

    __tablename__ = "uzivatel"
    # Stejny duvod jako u Clovek.__table_args__ vyse - partial unikatni index
    # deklarovany i v ORM, at ho ma i testovaci databaze, ne jen migrace f1a2b3c4d5e6.
    __table_args__ = (
        Index(
            "uq_uzivatel_email",
            "email",
            unique=True,
            sqlite_where=text("email IS NOT NULL AND email != ''"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Male pismeno, cislice, tecka/podtrzitko/pomlcka. Kontroluje se v
    # app/services/uzivatele.py, aby chyba sla hlasit citelne uzivateli.
    login: Mapped[str] = mapped_column(String(50), unique=True)
    jmeno: Mapped[str] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(200), default=None)
    # True kdyz e-mail potvrdil sam clovek (odkazem z pozvanky nebo ze zmeny
    # e-mailu v profilu) - jen na overeny e-mail jde poslat vyzvu k podpisu.
    # server_default (ne jen default=) je schvalny - diky nemu se zaloz_prvniho_spravce()
    # (rucni INSERT bez ORM, spousti se i ze stare migrace b42fe20a9964, kde
    # tenhle sloupec jeste neexistoval) nemusi menit pri kazdem novem sloupci.
    email_overen: Mapped[bool] = mapped_column(Boolean, server_default=false(), default=False)
    otisk_hesla: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20), default=ROLE_UZIVATEL)
    aktivni: Mapped[bool] = mapped_column(Boolean, default=True)
    # True, jakmile byl ucet aspon jednou aktivni (prijal pozvanku, spravce ho
    # zalozil s docasnym heslem, nebo byl deaktivovan). Rozlisuje "pozvany, jeste
    # neprijal" (aktivni=False, nekdy_aktivni=False) od "deaktivovany"
    # (aktivni=False, nekdy_aktivni=True) - pozvanka smi ozivit jen ten prvni,
    # deaktivovany ucet vraci jen tlacitko Aktivovat na /uzivatele.
    nekdy_aktivni: Mapped[bool] = mapped_column(Boolean, server_default=false(), default=False)
    # True po zalozeni uctu nebo po resetu hesla spravcem - clovek se prihlasi
    # docasnym heslem, ale hned ho appka posle zmenit si vlastni.
    musi_zmenit_heslo: Mapped[bool] = mapped_column(Boolean, default=False)
    # Dokdy dočasné heslo platí (naivní UTC); po lhůtě se s ním nejde přihlásit
    # (auth.overit). Prázdné = heslo si člověk zvolil sám. Nastavuje a maže
    # `nastav_heslo`, jiné místo sloupec nemění.
    docasne_heslo_do: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)
    # Zvysi se pri kazde zmene hesla. Cislo se opisuje i do prihlasovaci
    # cookie - kdyz se neshoduji, byl ucet mezitim zmeneny a session uz
    # neplati (viz app/auth.py:nacti_uzivatele).
    verze_hesla: Mapped[int] = mapped_column(Integer, default=1)
    vytvoreno: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    posledni_prihlaseni: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)

    # Limity cteni pres AI, nastavuje je spravce na /uzivatele. Pocita se
    # z `PouzitiAI`, ne z pameti - limit tak prezije restart serveru.
    # Meze a vychozi hodnoty jsou v app/services/cteni_ai.py (MEZE_LIMITU_AI).
    limit_cteni_den: Mapped[int] = mapped_column(Integer, server_default="300", default=300)
    limit_kc_den: Mapped[int] = mapped_column(Integer, server_default="100", default=100)
    limit_kc_rok: Mapped[int] = mapped_column(Integer, server_default="500", default=500)

    @property
    def je_spravce(self) -> bool:
        return self.role == ROLE_SPRAVCE

    @property
    def je_revizor(self) -> bool:
        return self.role == ROLE_REVIZOR

    @property
    def je_podepisujici(self) -> bool:
        return self.role == ROLE_PODEPISUJICI

    # Prava jsou vyslovny seznam roli, ne "vsechno krome". Sloupec `role` je volny
    # text bez CHECK v databazi (SQLite by musel tabulku prestavet a to tu hrozi
    # smazanim dat kaskadou), takze preklep ze zalohy nebo rucniho zasahu
    # ("Revizor", "revizor ") musi znamenat zadna prava, ne pravo zapisu.
    @property
    def muze_zapisovat(self) -> bool:
        return self.role in (ROLE_SPRAVCE, ROLE_UZIVATEL)

    @property
    def ma_pristup_k_akcim(self) -> bool:
        return self.role in (ROLE_SPRAVCE, ROLE_UZIVATEL, ROLE_REVIZOR)


class OdeslanyEmail(Zaklad):
    """Zaznam o kazdem pokusu odeslat e-mail - i o neuspesnem.

    Telo mailu se neuklada (obsahuje odkaz s tokenem, viz app/services/tokeny.py).
    Diky stavu tady appka pozna, ze SMTP prestalo fungovat, misto aby to
    zjistila az z toho, ze si nikdo nestezuje na chybejici pozvanku.
    """

    __tablename__ = "odeslany_email"
    # Dotaz "kdy byla uzivateli naposledy poslana pozvanka" (Lide, Uzivatele).
    __table_args__ = (Index("ix_odeslany_email_uzivatel_ucel", "uzivatel_id", "ucel", "vytvoreno"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    uzivatel_id: Mapped[int | None] = mapped_column(
        ForeignKey("uzivatel.id", ondelete="SET NULL"), default=None
    )
    komu: Mapped[str] = mapped_column(String(200))
    predmet: Mapped[str] = mapped_column(String(200))
    ucel: Mapped[str] = mapped_column(String(30))
    stav: Mapped[str] = mapped_column(String(20))
    chyba: Mapped[str | None] = mapped_column(Text, default=None)
    vytvoreno: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    odeslano: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)


class TokenUzivatele(Zaklad):
    """Jednorazovy token v odkazu z e-mailu (pozvanka, reset hesla, zmena e-mailu).

    V databazi lezi jen SHA-256 otisk (`otisk`), nikdy surovy token - unik
    databaze tak neprozradi platne odkazy. `nova_hodnota` nese novy e-mail
    u `zmena_emailu`, jinde zustava prazdna.
    """

    __tablename__ = "token_uzivatele"

    id: Mapped[int] = mapped_column(primary_key=True)
    uzivatel_id: Mapped[int] = mapped_column(ForeignKey("uzivatel.id", ondelete="CASCADE"))
    ucel: Mapped[str] = mapped_column(String(30))
    otisk: Mapped[str] = mapped_column(String(64), unique=True)
    nova_hodnota: Mapped[str | None] = mapped_column(String(200), default=None)
    platnost_do: Mapped[dt.datetime] = mapped_column(DateTime)
    pouzito: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)
    vytvoreno: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
