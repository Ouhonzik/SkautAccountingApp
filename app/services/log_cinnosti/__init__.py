"""Log cinnosti: kdo, kdy a co v aplikaci zmenil - rozdeleno na tri casti.

- `zapisovani` - zapis: hook po ulozeni (`after_flush`), slucovani zaznamu
  a rucni `zapis()`. Bezpecnostne citliva cast, bezi pri kazdem ulozeni.
- `popisky` - texty a pravidla: nazvy objektu a poli, co se nikdy neloguje.
- `prehled` - cteni pro stranku /log: filtry, casova osa, dlazdice, graf.

Importem balicku se hook zaregistruje (`zapisovani` se importuje nize), a
proto zbytek appky smi dal psat `from app.services import log_cinnosti`
a volat `log_cinnosti.zapis(...)`. Podmodul se zamerne nejmenuje `zapis`,
aby se nepral s funkci `zapis` o jmeno v balicku.
"""

from app.services.log_cinnosti.popisky import NELOGOVANE_TABULKY, OSOBNI_TABULKY
from app.services.log_cinnosti.prehled import (
    OBDOBI_VOLBY,
    aktivita_po_dnech,
    dotaz_zaznamu,
    obdobi_hranice,
    seskup_po_dnech,
    seznam_akci_v_logu,
    seznam_uzivatelu_pro_filtr,
    souhrn,
)
from app.services.log_cinnosti.zapisovani import OKNO_SLOUCENI, bez_automatickeho_zapisu, zapis

__all__ = [
    "NELOGOVANE_TABULKY",
    "OBDOBI_VOLBY",
    "OKNO_SLOUCENI",
    "OSOBNI_TABULKY",
    "aktivita_po_dnech",
    "bez_automatickeho_zapisu",
    "dotaz_zaznamu",
    "obdobi_hranice",
    "seskup_po_dnech",
    "seznam_akci_v_logu",
    "seznam_uzivatelu_pro_filtr",
    "souhrn",
    "zapis",
]
