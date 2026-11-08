"""Jednorazove tokeny v odkazech z e-mailu (pozvanka, reset hesla, zmena e-mailu).

V databazi (`TokenUzivatele.otisk`) lezi jen SHA-256 otisk tokenu, nikdy
surovy token - unik databaze tak sam o sobe neprozradi platne odkazy.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cas import ted
from app.models import (
    UCEL_POZVANKA,
    UCEL_RESET_HESLA,
    UCEL_ZMENA_EMAILU,
    TokenUzivatele,
    Uzivatel,
)

# Jak dlouho odkaz platí, podle ucelu.
PLATNOST = {
    UCEL_POZVANKA: 7 * 24 * 60,  # 7 dni, v minutach
    UCEL_RESET_HESLA: 60,  # 1 hodina
    UCEL_ZMENA_EMAILU: 24 * 60,  # 24 hodin
}


def _otisk(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def zneplatni_vse(
    sezeni: Session,
    uzivatel: Uzivatel,
    ucely: Iterable[str] | None = None,
) -> None:
    """Oznaci vsechny dosud nepouzite tokeny uctu (nebo jen tokeny zadanych
    `ucely`) za pouzite. Odkaz vydany pred zmenou hesla, zmenou e-mailu nebo
    deaktivaci uctu totiz nesmi po ni dal platit - clovek, ktery tuší, ze se mu
    nekdo dostal ke schrance nebo uctu, dela presne tyto tri veci."""
    dotaz = select(TokenUzivatele).where(
        TokenUzivatele.uzivatel_id == uzivatel.id,
        TokenUzivatele.pouzito.is_(None),
    )
    if ucely is not None:
        dotaz = dotaz.where(TokenUzivatele.ucel.in_(list(ucely)))
    ted_ = ted()
    for zaznam in sezeni.scalars(dotaz):
        zaznam.pouzito = ted_


def vydej(
    sezeni: Session,
    uzivatel: Uzivatel,
    ucel: str,
    nova_hodnota: str | None = None,
) -> str:
    """Vyrobi novy token, ulozi jen jeho otisk a vrati surovy token pro odkaz.

    Zneplatni pritom vsechny starsi nepouzite tokeny stejneho ucelu pro
    stejneho uzivatele - platny muze byt vzdy jen ten posledni vydany.
    """
    zneplatni_vse(sezeni, uzivatel, (ucel,))
    ted_ = ted()

    token = secrets.token_urlsafe(32)
    sezeni.add(
        TokenUzivatele(
            uzivatel_id=uzivatel.id,
            ucel=ucel,
            otisk=_otisk(token),
            nova_hodnota=nova_hodnota,
            platnost_do=ted_ + dt.timedelta(minutes=PLATNOST[ucel]),
        )
    )
    return token


def najdi(sezeni: Session, ucel: str, token: str) -> TokenUzivatele | None:
    """Najde platny, dosud nepouzity token daneho ucelu. Vraci None u
    neplatneho, prosleho nebo uz pouziteho tokenu - volajici nerozlisuje
    proc presne odkaz neplati, jen ukaze obecnou hlasku."""
    zaznam = sezeni.scalar(select(TokenUzivatele).where(TokenUzivatele.otisk == _otisk(token)))
    if zaznam is None:
        return None
    if zaznam.ucel != ucel:
        return None
    if zaznam.pouzito is not None:
        return None
    if zaznam.platnost_do <= ted():
        return None
    return zaznam


def spotrebuj(sezeni: Session, token_radek: TokenUzivatele) -> None:
    token_radek.pouzito = ted()
