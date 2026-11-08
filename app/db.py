"""Pripojeni k databazi.

Databaze je jeden soubor SQLite ve slozce data/. Pro jedno stredisko s par
soucasnymi uzivateli to bohate staci a zalohuje se zkopirovanim souboru.
Appka zamerne bezi jako jeden proces, protoze SQLite snese jednoho
zapisovatele.
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy import Engine, MetaData, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import nastaveni

# SQLite neumi ALTER TABLE, takze Alembic pri zmene tabulku prestavuje - a k tomu
# potrebuje, aby kazde omezeni melo jmeno. SQLAlchemy je bez tohohle predpisu
# nepojmenovava a migrace pak spadne na "Constraint must have a name".
KONVENCE_JMEN = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Zaklad(DeclarativeBase):
    """Spolecny predek vsech tabulek."""

    metadata = MetaData(naming_convention=KONVENCE_JMEN)


nastaveni.zaloz_slozky()

# Jak dlouho smi zapis cekat, nez ho zablokuje jiny zapis (v sekundach). Vychozich
# 5 s je malo pro delsi zapisy (import, obnova ze zalohy): ostatni zapisy by v tu dobu
# padaly na "database is locked". E-mail zamek nedrzi - `posta.posli` pred SMTP commituje.
CEKANI_NA_ZAMEK_S = 30


def vytvor_engine(soubor) -> Engine:
    """Pripojeni k databazi v `soubor` s nastavenim, ktere appka potrebuje."""
    # hide_parameters: SQLAlchemy by jinak do textu kazde chyby databaze (a tedy
    # do zurnalu serveru) opsalo hodnoty ukladanych poli - jmena a data narozeni
    # deti, e-maily, otisky hesel a tokenu. S timhle zustane jen SQL prikaz.
    novy = create_engine(
        f"sqlite:///{soubor}",
        hide_parameters=True,
        connect_args={"timeout": CEKANI_NA_ZAMEK_S},
    )

    @event.listens_for(novy, "connect")
    def _zapni_cizi_klice(spojeni, _zaznam):
        """SQLite ve vychozim stavu cizi klice nehlida - musime si o to rict.

        Bez tohohle by slo smazat akci a nechat v databazi osirele uctenky.
        """
        kurzor = spojeni.cursor()
        kurzor.execute("PRAGMA foreign_keys=ON")
        kurzor.close()

    return novy


engine = vytvor_engine(nastaveni.soubor_databaze)


Sezeni = sessionmaker(bind=engine, expire_on_commit=False)


def zamkni_zapis(sezeni: Session) -> None:
    """Zacne zapisovaci transakci `BEGIN IMMEDIATE` - ostatni zapisy pockaji.

    Pouzij na zacatku routy, ktera cte, z precteneho neco pocita a vysledek
    zapisuje (soucet jizd -> castka v pokladni knize, `max(poradi) + 1`). SQLite
    jinak zapisovaci zamek vezme az pri prvnim zapisu, takze dve takove routy
    nacetou stejny stav a druha pak prepise vysledek prvni. S timhle druha pocka
    na commit prvni a nacte uz jeji vysledek.

    Volej PRED nactenim dat, ktera se z nich pocitaji, a v `async` route az po
    posledni `await` (jinak by dalsi pozadavek ve stejne smycce cekal na zamek,
    ktery drzi zastavena routa). Nepouzivej plosne pro vsechny pozadavky - zamek
    se drzi do konce transakce, a kdo cte stranky, by cekal na kazdeho pisare.
    Mezi zamknutim a commitem nevolej nic pomaleho (sit, e-mail, AI).

    Kdyz zapis uz probiha (nektery dotaz uz neco zapsal), zamek uz mame a nic se nedela.
    """
    spojeni = sezeni.connection().connection.dbapi_connection
    if spojeni is None or spojeni.in_transaction:
        return
    spojeni.execute("BEGIN IMMEDIATE")


def dej_sezeni() -> Iterator[Session]:
    """Sezeni s databazi pro jeden pozadavek. Pouziva FastAPI pres Depends()."""
    with Sezeni() as sezeni:
        yield sezeni


# Zavislost na databazi zapsana do typu (FastAPI od 0.95): v route staci
# `sezeni: Databaze`. Je tady a ne v routes/, aby ji mohl pouzit i app/auth.py.
Databaze = Annotated[Session, Depends(dej_sezeni)]
