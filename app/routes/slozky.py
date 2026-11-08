"""Roztrideni skenu do slozek (= toho, cemu hospodar rika doklady)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from app.formulare import id_z_textu, mez_delky, over_delky
from app.models import Clovek, Slozka
from app.routes.spolecne import (
    Databaze,
    nacti_akci,
    nacti_formular,
    nacti_otevrenou_akci,
    over_otevrenou,
    prazdne_na_none,
    prubezne_ulozeni,
    text_z_formulare,
    uz_smazano_kam,
)
from app.sablony import oznam, strana, zpet
from app.services import pokladni_kniha, trideni
from app.services.ciselniky import dalsi_poradi, vsechny_kategorie, vsichni_lide

router = APIRouter()


@router.get("/akce/{akce_id}/slozky", response_class=HTMLResponse)
async def prehled(request: Request, akce_id: int, sezeni: Databaze):
    akce = nacti_akci(sezeni, akce_id)
    # GET nic nemeni, takze tu se prazdne slozky neuklizeji (revizor by pri
    # prohlizeni zapisoval do databaze a cizi odkaz by mazal slozky i s rucnimi
    # nazvy). Uklizi je az ulozeni formulare nize nebo kriz u slozky.
    return strana(
        request,
        "slozky.html",
        akce=akce,
        kategorie=vsechny_kategorie(sezeni),
        lide=vsichni_lide(sezeni),
        nezarazene=[s for s in akce.skeny if s.slozka_id is None],
        # Cislo dokladu ke kazde slozce - tez, ktere je na papire i v exportu.
        # Bez prefixu akce: ten je u vsech dokladu stejny a na obrazovce nic
        # nerozlisi. Slozka se samymi prilohami doklad nema a v mape chybi.
        cisla=pokladni_kniha.cisla_slozek(akce),
    )


def _varovani_nezkontrolovanych(request: Request, akce) -> None:
    """Doklady se skládají z účtenek tak, jak je přečetla AI. Dokud je člověk
    nezkontroluje, můžou být částky i kategorie špatně."""
    zbyva = sum(1 for s in akce.skeny if not s.zkontrolovano)
    if zbyva:
        oznam(
            request,
            f"Některé účtenky ještě nejsou zkontrolované ({zbyva}) — před uzavřením akce "
            f"je zkontroluj v Účtenkách.",
            "pozor",
        )


def _chybejici_udaje(akce) -> str | None:
    """Proc nektere skeny zustaly lezet. Bez kategorie i bez platice je zaradit
    nejde - platic urcuje, komu se doklad proplaci, takze se neda uhodnout."""
    bez_kategorie = [s for s in akce.skeny if s.slozka_id is None and s.kategorie_id is None]
    bez_platice = [
        s
        for s in akce.skeny
        if s.slozka_id is None and s.kategorie_id is not None and s.clovek_id is None
    ]
    duvody = []
    if bez_kategorie:
        duvody.append(f"{len(bez_kategorie)} bez kategorie")
    if bez_platice:
        duvody.append(f"{len(bez_platice)} bez plátce")
    if not duvody:
        return None
    return f"Nezařazené skeny: {', '.join(duvody)}."


@router.post("/akce/{akce_id}/slozky/roztridit")
async def roztridit(request: Request, akce_id: int, sezeni: Databaze):
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    nove = trideni.roztrid(sezeni, akce)
    sezeni.commit()

    if nove:
        oznam(request, f"Vytvořeno {len(nove)} nových složek.")
    else:
        oznam(request, "Nic nového k roztřídění.")
    if (chybi := _chybejici_udaje(akce)) is not None:
        oznam(request, chybi, "chyba")
    _varovani_nezkontrolovanych(request, akce)
    return zpet(f"/akce/{akce_id}/slozky")


@router.post("/akce/{akce_id}/slozky/pretridit")
async def pretridit(request: Request, akce_id: int, sezeni: Databaze):
    """Zahodi vsechny slozky a poskláda je znovu podle pravidla.

    Oddelene tlacitko schvalne: bezne roztrideni sahá jen na nezarazene skeny,
    tohle prepise i to, co Honza ve slozkach prerovnal a prejmenoval rucne.
    """
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    nove = trideni.vsechno_znovu(sezeni, akce)
    sezeni.commit()

    oznam(request, f"Akce přetříděna od nuly — vzniklo {len(nove)} složek.")
    if (chybi := _chybejici_udaje(akce)) is not None:
        oznam(request, chybi, "chyba")
    _varovani_nezkontrolovanych(request, akce)
    return zpet(f"/akce/{akce_id}/slozky")


@router.post("/akce/{akce_id}/slozky/nova")
async def nova(
    request: Request,
    akce_id: int,
    sezeni: Databaze,
    nazev: Annotated[str, Form(max_length=mez_delky(Slozka.nazev))],
):
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    if not nazev.strip():
        oznam(request, "Složka musí mít název.", "chyba")
        return zpet(f"/akce/{akce_id}/slozky")

    poradi = dalsi_poradi(s.poradi for s in akce.slozky)
    sezeni.add(Slozka(akce=akce, nazev=nazev.strip(), poradi=poradi))
    sezeni.commit()
    oznam(request, f"Složka „{nazev.strip()}“ vytvořena.")
    return zpet(f"/akce/{akce_id}/slozky")


@router.post("/akce/{akce_id}/slozky/ulozit")
async def ulozit(request: Request, akce_id: int, sezeni: Databaze):
    """Ulozi prejmenovani slozek i presuny skenu mezi nimi najednou."""
    akce = nacti_otevrenou_akci(sezeni, akce_id)
    formular = await nacti_formular(request)
    over_delky(formular, {"nazev_slozky": Slozka.nazev, "ucel_slozky": Slozka.ucel_platby})

    for slozka in akce.slozky:
        novy_nazev = prazdne_na_none(text_z_formulare(formular, f"nazev_slozky_{slozka.id}"))
        if novy_nazev:
            slozka.nazev = novy_nazev
        if f"kdo_slozky_{slozka.id}" in formular:
            kdo = text_z_formulare(formular, f"kdo_slozky_{slozka.id}")
            # Neexistujici nebo nectitelne ID se preskoci - cizi klic by jinak
            # shodil ulozeni chybou 500.
            if not kdo:
                slozka.clovek_id = None
            elif (cislo := id_z_textu(kdo)) is not None and sezeni.get(Clovek, cislo):
                slozka.clovek_id = cislo
        # Prazdne pole neznamena "nic" - znamena "vezmi navrh z kategorie
        # a obchodniku". Proto se uklada None, ne prazdny retezec.
        if f"ucel_slozky_{slozka.id}" in formular:
            slozka.ucel_platby = prazdne_na_none(
                text_z_formulare(formular, f"ucel_slozky_{slozka.id}")
            )

    # Slozka z formulare musi patrit teze akci - jinak by se ucenka (a jeji
    # castka) tise presunula do vyuctovani jine akce.
    slozky_akce = {slozka.id for slozka in akce.slozky}
    presunuto = 0
    for sken in akce.skeny:
        if f"slozka_skenu_{sken.id}" not in formular:
            continue
        hodnota = text_z_formulare(formular, f"slozka_skenu_{sken.id}")
        nova_slozka_id = id_z_textu(hodnota)
        if hodnota and nova_slozka_id is None:
            continue
        if nova_slozka_id is not None and nova_slozka_id not in slozky_akce:
            continue
        if nova_slozka_id != sken.slozka_id:
            sken.slozka_id = nova_slozka_id
            presunuto += 1

    sezeni.commit()

    # Pri prubeznem ukladani se prazdne slozky neuklizeji. Stranka se
    # neprekresluje, takze by uzivateli zmizela slozka, kterou ma porad na
    # obrazovce - a dalsi ulozeni by se odkazovalo na uz neexistujici id.
    if (hotovo := prubezne_ulozeni(request)) is not None:
        return hotovo

    smazano = trideni.uklid_prazdne_slozky(sezeni, akce)
    sezeni.commit()
    oznam(request, f"Uloženo. Přesunuto skenů: {presunuto}.")
    if smazano:
        oznam(request, f"Prázdné složky jsem odstranil: {smazano}.")
    return zpet(f"/akce/{akce_id}/slozky")


@router.post("/slozka/{slozka_id}/smazat")
async def smazat(request: Request, slozka_id: int, sezeni: Databaze):
    """Smaze slozku. Skeny v ni zustanou, jen se stanou nezarazenymi."""
    slozka = sezeni.get(Slozka, slozka_id)
    if slozka is None:
        return await uz_smazano_kam(request)
    over_otevrenou(slozka.akce)
    akce_id = slozka.akce_id
    pocet = len(slozka.skeny)
    for sken in slozka.skeny:
        sken.slozka_id = None
    sezeni.delete(slozka)
    sezeni.commit()
    oznam(request, f"Složka smazána, {pocet} skenů je teď nezařazených.")
    return zpet(f"/akce/{akce_id}/slozky")
