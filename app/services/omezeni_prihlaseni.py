"""Omezeni poctu pokusu - brani hadani hesla hrubou silou a zahlcovani schranek.

Kazdy limit je `Pravidlo`: klic (co se pocita), nejvyssi pocet pokusu a delka
okna. Retezec misto pravidla znamena vychozi limit (5 pokusu za 15 minut).
Pri prihlaseni se pocita zvlast podle dvojice ucet + IP adresa, zvlast podle
samotne IP a zvlast podle uctu (viz app/routes/ucet.py) - utocnik tak neobejde
limit stridanim jmen, ale ani nezamkne cizi ucet majiteli, ktery se prihlasuje
z jine site.

Drzi se jen v pameti procesu - po restartu serveru se limity vynuluji, coz je
v poradku (restart neni bezny utok a appka bezi na jednom serveru, ne v roji
instanci). Pamet je proto omezena: prazdne klice se mazou a klicu je nejvic
MAX_KLICU.

Omezovac je bezpecny pro vlakna: routy `def` (mapa) FastAPI pousti ve vice
vlaknech najednou a bez zamku by dve z nich cetly stejny pocet a obe prosly.
"""

from __future__ import annotations

import threading
import time
from typing import NamedTuple

LIMIT = 5
OKNO_S = 15 * 60

# Nejdelsi okno, ktere kdo pouziva. Pokusy starsi nez tohle uz nemuze potrebovat
# zadne pravidlo, tak se pri zapisu mazou. Rezerva na denni limity e-mailu.
NEJDELSI_OKNO_S = 24 * 3600

# Pojistka proti zaplneni pameti. Kazdy zapsany pokus zaklada klic; zapisuje se
# ale jen po neuspechu, ktery neblokovaly limity podle IP, takze jedna adresa
# jich nadela nejvyse desitky. Pri prekroceni se zahodi nejstarsi klice (na
# ukor toho, ze se na chvili zapomene nekolik pocitadel - lepsi nez pad serveru).
MAX_KLICU = 10_000


class Pravidlo(NamedTuple):
    klic: str
    limit: int = LIMIT
    okno_s: int = OKNO_S


class Rezervace(NamedTuple):
    """Pokus zapsany predem (viz `Omezovac.rezervuj_pokus`): klice a cas zapisu."""

    klice: tuple[str, ...]
    cas: float


class Omezovac:
    def __init__(self, ted=time.monotonic):
        self._ted = ted
        # RLock, ne Lock: `zablokovano` vola `pocet`, oboji si zamek bere.
        self._zamek = threading.RLock()
        # Slovnik drzi poradi vkladani, takze prvni klic je nejstarsi.
        self._pokusy: dict[str, list[float]] = {}
        # Kdy se naposledy ohlasilo zablokovani klice (viz `ohlas_blokaci`).
        self._ohlaseno: dict[str, float] = {}

    def _cerstve(self, klic: str, okno_s: int) -> list[float]:
        """Pokusy klice v poslednim okne. Klic, ktery v pameti neni, se
        nezaklada (bez toho by kazda kontrola cizi hodnoty zvetsovala pamet,
        B-023); klic, kterému vsechny pokusy vyprselo, se smaze."""
        pokusy = self._pokusy.get(klic)
        if pokusy is None:
            return []
        hranice = self._ted() - okno_s
        cerstve = [cas for cas in pokusy if cas > hranice]
        if not cerstve:
            del self._pokusy[klic]
        elif len(cerstve) != len(pokusy):
            self._pokusy[klic] = cerstve
        return cerstve

    def pocet(self, klic: str, okno_s: int = OKNO_S) -> int:
        with self._zamek:
            return len(self._cerstve(klic, okno_s))

    def zablokovano(self, *pravidla: Pravidlo | str | None) -> bool:
        with self._zamek:
            for pravidlo in pravidla:
                if not pravidlo:
                    continue
                if isinstance(pravidlo, str):
                    pravidlo = Pravidlo(pravidlo)
                if self.pocet(pravidlo.klic, pravidlo.okno_s) >= pravidlo.limit:
                    return True
            return False

    def zaznamenej_neuspech(self, *klice: str | None) -> None:
        with self._zamek:
            self._zapis(self._ted(), klice)

    def _zapis(self, cas: float, klice: tuple[str | None, ...]) -> None:
        with self._zamek:
            for klic in klice:
                if klic:
                    # Vyjmout a vlozit znovu = klic se presune na konec, "nejstarsi"
                    # je tak ten, kdo se nejdele nepouzil.
                    pokusy = self._pokusy.pop(klic, [])
                    pokusy.append(cas)
                    self._pokusy[klic] = pokusy
            self._uklid()

    def zkus_pouzit(self, pravidlo: Pravidlo) -> bool:
        """Zkontroluje limit a hned zapise pouziti jednou operaci: vrati True
        (a pouziti se pocita), nebo False (limit vycerpan, nic se nezapise).
        Pro limity "kolik dotazu za okno" (mapa), ne pro neuspechy prihlaseni.
        Kontrola a zapis zvlast by mezi sebou nechaly mezeru pro druhe vlakno."""
        with self._zamek:
            if self.pocet(pravidlo.klic, pravidlo.okno_s) >= pravidlo.limit:
                return False
            self.zaznamenej_neuspech(pravidlo.klic)
            return True

    def rezervuj_pokus(self, *pravidla: Pravidlo | None) -> Rezervace | None:
        """Pro pokus, jehoz vysledek se pozna az po pomale praci (bcrypt):
        zkontroluje vsechna pravidla a hned zapise pokus do jejich klicu jednou
        operaci. Vrati rezervaci, nebo None (nejaky limit je vycerpany, nic se
        nezapise). Pri uspechu se rezervace vrati pres `vrat_pokus`, pri neuspechu
        zustane zapsana. Kdyby se kontrola a zapis dely kolem pomale prace, projdou
        souberzna vlakna kontrolou vsechna, dokud nikdo nezapsal neuspech.

        Dusledek (prijato, R27): pokus se pocita do limitu uz PRED overenim hesla,
        takze souberznych pokusu jednoho uctu z jedne IP projde nejvyse `limit`;
        dalsi souberzne se odmitne, i kdyz ma spravne heslo. Jeden clovek se
        neprihlasuje sestkrat najednou a utocnikovi to nic nedava."""
        pouzita = tuple(p for p in pravidla if p)
        with self._zamek:
            if self.zablokovano(*pouzita):
                return None
            klice = tuple(dict.fromkeys(p.klic for p in pouzita))
            cas = self._ted()
            self._zapis(cas, klice)
            return Rezervace(klice, cas)

    def vrat_pokus(self, rezervace: Rezervace) -> None:
        """Zrusi rezervovany pokus (pokus se povedl, nema se pocitat jako neuspech)."""
        with self._zamek:
            for klic in rezervace.klice:
                pokusy = self._pokusy.get(klic)
                if pokusy and rezervace.cas in pokusy:
                    pokusy.remove(rezervace.cas)
                    if not pokusy:
                        del self._pokusy[klic]

    def ohlas_blokaci(self, klic: str, okno_s: int = OKNO_S) -> bool:
        """True jen poprve za okno pro dany klic. Slouzi k tomu, aby se zablokovani
        zapsalo do logu cinnosti jednou, ne po kazdem odmitnutem pokusu - utocnik
        by jinak log zaplavil."""
        with self._zamek:
            cas = self._ted()
            posledni = self._ohlaseno.pop(klic, None)
            if posledni is not None and cas - posledni < okno_s:
                self._ohlaseno[klic] = posledni
                return False
            self._ohlaseno[klic] = cas
            while len(self._ohlaseno) > MAX_KLICU:
                del self._ohlaseno[next(iter(self._ohlaseno))]
            return True

    def vycisti(self, *klice: str | None) -> None:
        with self._zamek:
            for klic in klice:
                if klic:
                    self._pokusy.pop(klic, None)

    def _uklid(self) -> None:
        """Smaze klice bez cerstvych pokusu a udrzi pocet klicu pod stropem.
        Zapisuje se jen po neuspechu, takze jeden pruchod pameti (nejvyse
        MAX_KLICU klicu) nikoho nezdrzuje."""
        hranice = self._ted() - NEJDELSI_OKNO_S
        for klic in [k for k, casy in self._pokusy.items() if casy[-1] <= hranice]:
            del self._pokusy[klic]
        while len(self._pokusy) > MAX_KLICU:
            del self._pokusy[next(iter(self._pokusy))]


# Jeden sdileny omezovac pro celou aplikaci.
omezovac = Omezovac()
