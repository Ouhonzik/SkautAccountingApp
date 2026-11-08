"""devet pevnych kategorii

Kategorie jsou od ted napevno tyto (poradi 0-8), vlastni stredisko pridava az za ne:
materiál spotřební, vybavení k realizaci akcí/táborů (do 3000 Kč/kus), věcné dary
(pro činovníky nebo partnerské subjekty), potraviny a stravné, cestovné (cestovní
náhrady), nájemné a ubytování, jízdné/doprava osob a materiálu, služby ostatní
(vč. účast. poplatků na externí akce), jiné ostatní náklady.

Data se prevadeji NA MISTE, nic se nesluci a zadne uctenky ani slozky se nepresouvaji
(slozky jsou podle kategorie a cloveka, sloucenim by vznikly kolize). Kazdou stavajici
kategorii najde migrace a UPDATEM jejiho radku zmeni nazev, klic (jen kde je to nutne),
`pevna` a poradi. Radek (id) zustava, takze vsechny odkazy (slozka, sken, cast_skenu)
drzi.

Jak se hleda, ktera stara kategorie je ktera pevna (bez ohledu na velikost pismen
a diakritiku), od nejsilnejsi shody:

0. uz drive pevna kategorie s timto klicem (Věcné dary z K-219) - vyhrava vzdy;
1. radek, ktery se uz jmenuje presne jako pevna kategorie (migrace jde pustit znovu);
2. dosavadni nazev: Jídlo -> potraviny a stravné, Materiál -> materiál spotřební,
   Ubytování -> nájemné a ubytování, Cesťák -> cestovné (cestovní náhrady),
   Jízdné -> jízdné/doprava osob a materiálu, Jiné -> jiné ostatní náklady,
   Věcné dary (i s dodatkem v závorce) -> věcné dary (...);
3. radek, ktery nese klic dane pevne kategorie, i kdyz se jmenuje jinak.

Z vice kandidatu na jednu pevnou kategorii se prevede jen jeden (nejsilnejsi shoda,
pri shode nejnizsi id); ostatni zustanou jako vlastni a pokud nesou klic pevne
kategorie, prijdou o nej (klic je unikatni a je jen oznaceni pro AI - kategorie se
dal najde podle nazvu). Vlastni kategorie, jejiz nazev se po preklopeni shoduje
s nazvem pevne, dostane za nazev " (vlastní)".

Pevne kategorie, ktere v ciselniku chybi (vybavení, služby ostatní, v nove databazi
vsechny), migrace zalozi. Vlastni kategorie se seradi za ne (poradi 9, 10, ...) ve
stavajicim poradi.

Jen UPDATE a INSERT, zadny batch_alter_table (na `kategorie` odkazuji cizi klice
a DROP TABLE by spustil jejich kaskadu). Migrace je idempotentni - druhe spusteni nic
nezmeni. Co se prevedlo, vypise se.

Revision ID: c5d8a2f7b169
Revises: b3f6a8c1d9e4
Create Date: 2026-10-19 10:00:00.000000

"""

import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5d8a2f7b169"
down_revision: str | Sequence[str] | None = "b3f6a8c1d9e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Opsano zde schvalne, ne importem z app.models: migrace se nesmi zmenit,
# kdyz se zmeni kod aplikace.
# (klic, nazev, dosavadni nazev bez diakritiky malymi pismeny, nebo None)
PEVNE: tuple[tuple[str, str, str | None], ...] = (
    ("material", "materiál spotřební", "material"),
    ("vybaveni", "vybavení k realizaci akcí/táborů (do 3000 Kč/kus)", None),
    ("vecne dary", "věcné dary (pro činovníky nebo partnerské subjekty)", "vecne dary"),
    ("jidlo", "potraviny a stravné", "jidlo"),
    ("cestak", "cestovné (cestovní náhrady)", "cestak"),
    ("ubytovani", "nájemné a ubytování", "ubytovani"),
    ("jizdne", "jízdné/doprava osob a materiálu", "jizdne"),
    ("sluzby", "služby ostatní (vč. účast. poplatků na externí akce)", None),
    ("jine", "jiné ostatní náklady", "jine"),
)
ZACATEK_NAZVU_DARU = "vecne dar"
PRIPONA_VLASTNI = " (vlastní)"


def _tvar(text: str | None) -> str:
    """Maly text bez diakritiky a nadbytecnych mezer, k porovnavani nazvu a klicu.

    Porovnani je v Pythonu, ne v SQL: `lower()` v SQLite umi jen anglickou abecedu."""
    rozlozeny = unicodedata.normalize("NFKD", (text or "").casefold())
    bez_carek = "".join(z for z in rozlozeny if not unicodedata.combining(z))
    return " ".join(bez_carek.split())


def _sila_shody(radek: sa.Row, klic: str, nazev: str, stary_nazev: str | None) -> int | None:
    """0 = uz pevna s timto klicem, 1 = presny nazev, 2 = dosavadni nazev, 3 = jen klic."""
    tvar_nazvu = _tvar(radek.nazev)
    if radek.pevna and _tvar(radek.klic) == klic:
        return 0
    if tvar_nazvu == _tvar(nazev):
        return 1
    if stary_nazev is not None and (
        tvar_nazvu == stary_nazev
        or (klic == "vecne dary" and tvar_nazvu.startswith(ZACATEK_NAZVU_DARU))
    ):
        return 2
    if _tvar(radek.klic) == klic:
        return 3
    return None


def preved_kategorie(spojeni: sa.Connection) -> list[str]:
    """Prevede kategorie na devet pevnych na miste; vrati vety o tom, co se stalo."""
    zprava: list[str] = []
    radky = spojeni.execute(
        sa.text("SELECT id, nazev, klic, poradi, pevna FROM kategorie ORDER BY id")
    ).all()

    # Prirazeni pevna kategorie -> radek: od nejsilnejsi shody, pri shode nejnizsi id.
    kandidati = sorted(
        (sila, radek.id, index)
        for index, (klic, nazev, stary) in enumerate(PEVNE)
        for radek in radky
        if (sila := _sila_shody(radek, klic, nazev, stary)) is not None
    )
    radek_pevne: dict[int, int] = {}  # index pevne -> id radku
    pevna_radku: dict[int, int] = {}  # id radku -> index pevne
    for _sila, id_radku, index in kandidati:
        if index not in radek_pevne and id_radku not in pevna_radku:
            radek_pevne[index] = id_radku
            pevna_radku[id_radku] = index

    podle_id = {r.id: r for r in radky}
    vlastni = [r for r in radky if r.id not in pevna_radku]
    pevne_klice = {klic for klic, _, _ in PEVNE}
    pevne_tvary = {_tvar(nazev) for _, nazev, _ in PEVNE}

    # 1. Vlastni kategorie nesmi drzet klic pevne (je unikatni) ani se jmenovat jako ona.
    # Radek, kteremu se klic nebo nazev meni, se nejdriv uvolni, ten se pak nastavi cely.
    obsazene_nazvy = {r.nazev for r in radky}
    for radek in vlastni:
        zmena: dict[str, object] = {}
        if radek.klic is not None and _tvar(radek.klic) in pevne_klice:
            zmena["klic"] = None
            zprava.append(f"Kategorie „{radek.nazev}“ přišla o klíč „{radek.klic}“ (patří pevné).")
        if _tvar(radek.nazev) in pevne_tvary:
            novy = radek.nazev + PRIPONA_VLASTNI
            cislo = 2
            while novy in obsazene_nazvy:
                novy = f"{radek.nazev}{PRIPONA_VLASTNI} {cislo}"
                cislo += 1
            obsazene_nazvy.add(novy)
            zmena["nazev"] = novy
            zprava.append(f"Vlastní kategorie „{radek.nazev}“ se přejmenovala na „{novy}“.")
        if zmena:
            _uprav(spojeni, radek.id, zmena)

    # 2. Klice prevadenych radku se nejdriv uvolni, aby se pri nastavovani nesrazily.
    for id_radku in pevna_radku:
        if podle_id[id_radku].klic is not None:
            _uprav(spojeni, id_radku, {"klic": None})

    # 3. Pevne kategorie: stavajici radek se prevede, chybejici se zalozi.
    for index, (klic, nazev, _stary) in enumerate(PEVNE):
        id_radku = radek_pevne.get(index)
        if id_radku is None:
            spojeni.execute(
                sa.text(
                    "INSERT INTO kategorie (nazev, klic, poradi, pevna) "
                    "VALUES (:nazev, :klic, :poradi, 1)"
                ),
                {"nazev": nazev, "klic": klic, "poradi": index},
            )
            zprava.append(f"Založena pevná kategorie „{nazev}“.")
            continue
        puvodni = podle_id[id_radku]
        _uprav(spojeni, id_radku, {"nazev": nazev, "klic": klic, "pevna": 1, "poradi": index})
        if puvodni.nazev != nazev:
            zprava.append(f"Kategorie „{puvodni.nazev}“ je teď pevná „{nazev}“.")

    # 4. Vlastni kategorie za nimi, v dosavadnim poradi.
    vlastni.sort(key=lambda r: (r.poradi, r.id))
    for pozice, radek in enumerate(vlastni):
        # pevna=0 pro pripad dvou "pevnych" radku se stejnym klicem v ruzne velikosti pismen
        _uprav(spojeni, radek.id, {"poradi": len(PEVNE) + pozice, "pevna": 0})
    return zprava


def _uprav(spojeni: sa.Connection, id_radku: int, zmena: dict[str, object]) -> None:
    sloupce = ", ".join(f"{sloupec} = :{sloupec}" for sloupec in zmena)
    spojeni.execute(
        sa.text(f"UPDATE kategorie SET {sloupce} WHERE id = :id"), {**zmena, "id": id_radku}
    )


def upgrade() -> None:
    """Upgrade (jen data, schema se nemeni)."""
    for veta in preved_kategorie(op.get_bind()):
        print(veta)  # vystup migrace vidi ten, kdo ji pousti


def downgrade() -> None:
    """Puvodni nazvy ani klice se neuklaji, vratit je nelze (obnov ze zalohy)."""
