"""Tabulky databaze, rozdelene do souboru podle temat.

Datovy model aplikace. Klicova mysklenka: co hospodar
nazyva dokladem, je u nas Slozka - na disku je to opravdu slozka, v Excelu
skupina radku se spolecnym souctem.

Vsechna jmena se vyvazeji i odtud, takze `from app.models import Sken` funguje
vsude (routy, sluzby, testy, migrace). Importem tohoto balicku se naimportuji
vsechny soubory - SQLAlchemy a Alembic tak vidi vsechny tabulky a retezcove
vazby (`relationship("Sken")`) se dohledaji.

- `konstanty` - ciselnikove retezce (druhy skenu, role, druhy cinnosti, ucely e-mailu)
- `typy` - `Castka`, `Setiny`, `Desetiny`
- `akce` - Akce, Slozka, Sken, CastSkenu, Prijem, PokladniRadek
- `ciselniky` - Kategorie, Clovek, Vozidlo, VozidloFoto
- `ucastnici` - Ucastnik, Obdarovany, Dar
- `cestaky` - Cestak, Jizda
- `ucty` - Uzivatel, OdeslanyEmail, TokenUzivatele
- `log` - ZaznamCinnosti, PouzitiAI
"""

from app.models.akce import (
    Akce as Akce,
)
from app.models.akce import (
    CastSkenu as CastSkenu,
)
from app.models.akce import (
    PokladniRadek as PokladniRadek,
)
from app.models.akce import (
    Prijem as Prijem,
)
from app.models.akce import (
    Sken as Sken,
)
from app.models.akce import (
    Slozka as Slozka,
)
from app.models.cestaky import (
    Cestak as Cestak,
)
from app.models.cestaky import (
    Jizda as Jizda,
)
from app.models.ciselniky import (
    Clovek as Clovek,
)
from app.models.ciselniky import (
    Kategorie as Kategorie,
)
from app.models.ciselniky import (
    Vozidlo as Vozidlo,
)
from app.models.ciselniky import (
    VozidloFoto as VozidloFoto,
)
from app.models.konstanty import (
    CINNOST_EMAIL_OMEZEN as CINNOST_EMAIL_OMEZEN,
)
from app.models.konstanty import (
    CINNOST_OBNOVA as CINNOST_OBNOVA,
)
from app.models.konstanty import (
    CINNOST_ODHLASENI as CINNOST_ODHLASENI,
)
from app.models.konstanty import (
    CINNOST_POZVANKA as CINNOST_POZVANKA,
)
from app.models.konstanty import (
    CINNOST_PRIDANI as CINNOST_PRIDANI,
)
from app.models.konstanty import (
    CINNOST_PRIHLASENI as CINNOST_PRIHLASENI,
)
from app.models.konstanty import (
    CINNOST_PRIHLASENI_NEUSPESNE as CINNOST_PRIHLASENI_NEUSPESNE,
)
from app.models.konstanty import (
    CINNOST_REGISTRACE as CINNOST_REGISTRACE,
)
from app.models.konstanty import (
    CINNOST_RESET_HESLA as CINNOST_RESET_HESLA,
)
from app.models.konstanty import (
    CINNOST_RESET_HESLA_ZADOST as CINNOST_RESET_HESLA_ZADOST,
)
from app.models.konstanty import (
    CINNOST_SMAZANI as CINNOST_SMAZANI,
)
from app.models.konstanty import (
    CINNOST_STAZENI as CINNOST_STAZENI,
)
from app.models.konstanty import (
    CINNOST_UPRAVA as CINNOST_UPRAVA,
)
from app.models.konstanty import (
    DRUH_ARCH_JIZDENEK as DRUH_ARCH_JIZDENEK,
)
from app.models.konstanty import (
    DRUH_CESTOVNI_PRIKAZ as DRUH_CESTOVNI_PRIKAZ,
)
from app.models.konstanty import (
    DRUH_JINE as DRUH_JINE,
)
from app.models.konstanty import (
    DRUH_PALIVO as DRUH_PALIVO,
)
from app.models.konstanty import (
    DRUH_UCTENKA as DRUH_UCTENKA,
)
from app.models.konstanty import (
    DRUHY_SKENU as DRUHY_SKENU,
)
from app.models.konstanty import (
    MIN_CASTI_ROZDELENI as MIN_CASTI_ROZDELENI,
)
from app.models.konstanty import (
    NAZVY_PROSTREDKU as NAZVY_PROSTREDKU,
)
from app.models.konstanty import (
    NAZVY_ROLI as NAZVY_ROLI,
)
from app.models.konstanty import (
    NEJDRIVE_NAROZENI as NEJDRIVE_NAROZENI,
)
from app.models.konstanty import (
    PROSTREDEK_AUTO as PROSTREDEK_AUTO,
)
from app.models.konstanty import (
    PROSTREDEK_AUTOBUS as PROSTREDEK_AUTOBUS,
)
from app.models.konstanty import (
    PROSTREDEK_MOTOCYKL as PROSTREDEK_MOTOCYKL,
)
from app.models.konstanty import (
    PROSTREDEK_VLAK as PROSTREDEK_VLAK,
)
from app.models.konstanty import (
    PROSTREDKY_NA_KM as PROSTREDKY_NA_KM,
)
from app.models.konstanty import (
    ROLE as ROLE,
)
from app.models.konstanty import (
    ROLE_PODEPISUJICI as ROLE_PODEPISUJICI,
)
from app.models.konstanty import (
    ROLE_REVIZOR as ROLE_REVIZOR,
)
from app.models.konstanty import (
    ROLE_SPRAVCE as ROLE_SPRAVCE,
)
from app.models.konstanty import (
    ROLE_UZIVATEL as ROLE_UZIVATEL,
)
from app.models.konstanty import (
    SMER_PRIJEM as SMER_PRIJEM,
)
from app.models.konstanty import (
    SMER_VYDEJ as SMER_VYDEJ,
)
from app.models.konstanty import (
    STAV_EMAILU_LOKALNE as STAV_EMAILU_LOKALNE,
)
from app.models.konstanty import (
    STAV_EMAILU_ODESLANO as STAV_EMAILU_ODESLANO,
)
from app.models.konstanty import (
    STAV_EMAILU_OMEZENO as STAV_EMAILU_OMEZENO,
)
from app.models.konstanty import (
    STAV_EMAILU_SELHALO as STAV_EMAILU_SELHALO,
)
from app.models.konstanty import (
    STAV_ROZPRACOVANA as STAV_ROZPRACOVANA,
)
from app.models.konstanty import (
    STAV_UZAVRENA as STAV_UZAVRENA,
)
from app.models.konstanty import (
    UCEL_CHYBA_APPKY as UCEL_CHYBA_APPKY,
)
from app.models.konstanty import (
    UCEL_POZVANKA as UCEL_POZVANKA,
)
from app.models.konstanty import (
    UCEL_RESET_HESLA as UCEL_RESET_HESLA,
)
from app.models.konstanty import (
    UCEL_UPOZORNENI_EMAILU as UCEL_UPOZORNENI_EMAILU,
)
from app.models.konstanty import (
    UCEL_ZALOHA_SELHALA as UCEL_ZALOHA_SELHALA,
)
from app.models.konstanty import (
    UCEL_ZMENA_EMAILU as UCEL_ZMENA_EMAILU,
)
from app.models.konstanty import (
    VEK_DITETE as VEK_DITETE,
)
from app.models.konstanty import (
    dnes as dnes,
)
from app.models.konstanty import (
    pripustne_narozeni as pripustne_narozeni,
)
from app.models.log import (
    PouzitiAI as PouzitiAI,
)
from app.models.log import (
    ZaznamCinnosti as ZaznamCinnosti,
)
from app.models.typy import (
    Castka as Castka,
)
from app.models.typy import (
    Desetiny as Desetiny,
)
from app.models.typy import (
    Setiny as Setiny,
)
from app.models.ucastnici import (
    Dar as Dar,
)
from app.models.ucastnici import (
    Obdarovany as Obdarovany,
)
from app.models.ucastnici import (
    Ucastnik as Ucastnik,
)
from app.models.ucty import (
    OdeslanyEmail as OdeslanyEmail,
)
from app.models.ucty import (
    TokenUzivatele as TokenUzivatele,
)
from app.models.ucty import (
    Uzivatel as Uzivatel,
)
