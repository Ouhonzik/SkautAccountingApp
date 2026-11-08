"""Kontrola, ze appka opravdu funguje - vola ji /zdravi.

Zvenku ji kazdych par minut vola hlidaci sluzba (napr. UptimeRobot).
Driv /zdravi vracelo "ok" naslepo, takze rozbita databaze
nebo plny disk zustaly nepovsimnute, dokud nezacali padat uzivatele.
"""

import logging
import shutil

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import nastaveni
from app.models import Uzivatel

log = logging.getLogger(__name__)

# Pod touhle hranici uz hrozi, ze se nepovede ulozit ucetenka ani zapsat do databaze.
MIN_VOLNO_BAJTU = 1024**3


def je_v_poradku(sezeni: Session) -> bool:
    """True, kdyz jde cist z databaze a na disku je aspon 1 GB. Nic nezapisuje
    (vola se porad dokola). Podrobnosti chyby jdou jen do logu serveru -
    adresa je verejna, odpoved nesmi nic prozradit."""
    try:
        sezeni.scalar(select(func.count(Uzivatel.id)))
        volno = shutil.disk_usage(nastaveni.data_dir).free
    except Exception:
        log.exception("Kontrola zdravi selhala")
        return False
    if volno < MIN_VOLNO_BAJTU:
        log.error("Kontrola zdravi: na disku zbyva jen %d MB", volno // 1024**2)
        return False
    return True
