"""Chyba u konkrétního pole formuláře.

Tenhle modul je jen stav a nic nesmí importovat (používá ho `sablony.py`); čtení
formuláře je v `app/formulare.py` (`formular_s_chybami`).

Obecná cesta (`chyba_formulare` v app/main.py) po špatném formuláři přesměruje a
ukáže jen hlášku v rohu: vyplněné údaje zmizí a není poznat, které pole je
špatně. U delších formulářů (založení a nastavení akce, nový uživatel, profil)
proto route přečte formulář přes `formulare.formular_s_chybami`:

- formulář je v pořádku -> dostane hotový model z `app/formulare.py` jako dřív,
- něco je špatně -> dostane `StavFormulare`: co člověk napsal (`hodnoty`) a text
  chyby u každého pole (`chyby`). Route pak stránku vykreslí znovu se stavem 422
  (`sablony.strana_s_chybou`) a šablona použije `stav_formulare.hodnota(...)`,
  `.atributy(...)` a `.napoveda(...)`: pole se orámuje (`aria-invalid`), text
  chyby je u něj a je na pole navázaný přes `aria-describedby`.

Hesla se nikdy nevracejí zpět do stránky (`tajna`); ostatní hodnoty jsou jen to,
co člověk právě sám poslal, a šablona je escapuje jako každý jiný text.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from markupsafe import Markup, escape
from starlette.requests import Request

SOUHRN_CHYB = "Nic se neuložilo — oprav označená pole."


@dataclass
class StavFormulare:
    """Co přišlo z formuláře a co je u kterého pole špatně.

    Prázdný stav (`odeslano=False`) znamená „formulář se ještě neodeslal":
    šablona pak ukáže výchozí hodnoty a žádné chyby.
    """

    hodnoty: dict[str, str] = field(default_factory=dict)
    chyby: dict[str, str] = field(default_factory=dict)
    # Chyby, které nepatří k žádnému poli (jen výjimečně).
    obecne: list[str] = field(default_factory=list)
    odeslano: bool = False
    # Vlastni veta do hlasky vpravo dole (kdyz se cast formulare ulozila).
    vlastni_souhrn: str | None = None

    def hodnota(self, pole: str, vychozi: object = "") -> str:
        """Text pro `value=`: co člověk napsal, jinak výchozí hodnota stránky."""
        if not self.odeslano:
            return "" if vychozi is None else str(vychozi)
        return self.hodnoty.get(pole, "")

    def zaskrtnuto(self, pole: str, vychozi: bool = False) -> bool:
        """Zaškrtávátko: po chybě stejné, jak ho člověk nechal."""
        if not self.odeslano:
            return vychozi
        return pole in self.hodnoty

    def atributy(self, pole: str) -> Markup:
        """Atributy pole s chybou (jinak nic): `aria-invalid` a odkaz na text chyby."""
        if pole not in self.chyby:
            return Markup("")
        return Markup(' aria-invalid="true" aria-describedby="chyba-{}"').format(pole)

    def napoveda(self, pole: str) -> Markup:
        """Text chyby u pole (jinak nic); `id` odpovídá `aria-describedby` z `atributy`."""
        if pole not in self.chyby:
            return Markup("")
        return Markup('<small id="chyba-{}" class="chyba-pole">{}</small>').format(
            pole, escape(self.chyby[pole])
        )

    @property
    def souhrn(self) -> str:
        """Věta do hlášky v rohu: bez pole, kterého se týká, je to přímo text chyby."""
        if self.vlastni_souhrn:
            return self.vlastni_souhrn
        if self.obecne:
            return self.obecne[0]
        return SOUHRN_CHYB


def stav_s_chybami(request: Request, *, souhrn: str | None = None, **chyby: str) -> StavFormulare:
    """Stav pro chybu, kterou našla až route (obsazený login, špatné heslo...).

    Hodnoty jsou ty, které před chvílí přečetla `formular_s_chybami`."""
    hodnoty = getattr(request.state, "hodnoty_formulare", {})
    return StavFormulare(hodnoty=hodnoty, chyby=dict(chyby), odeslano=True, vlastni_souhrn=souhrn)
