"""Ciselnik lidi, kteri na akcich plati a prijimaji penize.

Stejny princip jako kategorie: jednou se clovek zalozi, pak uz se jen vybira
ze seznamu. Duvod je papir - jmeno je na tiskopisu PPD/VPD dvakrat, v kolonce
"Vyplaceno komu" a pod podpisovou linkou, takze preklep ve volnem textu byl
videt az na vytistenem dokladu.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import auth
from app.chyby_poli import StavFormulare, stav_s_chybami
from app.db import zamkni_zapis
from app.formulare import ClovekForm, formular_s_chybami, over_delky, poradi_z_textu
from app.models import (
    STAV_EMAILU_LOKALNE,
    Clovek,
    Slozka,
    Uzivatel,
)
from app.routes.spolecne import Databaze, nacti_formular, prazdne_na_none, text_z_formulare
from app.sablony import oznam, strana, strana_s_chybou, zpet
from app.services import cestaky, posta
from app.services.ciselniky import (
    dalsi_poradi,
    pouziti_lidi,
    prejmenuj_bez_kolizi,
    proc_nejde_smazat,
    uz_existuje,
    vsichni_lide,
)
from app.services.uzivatele import over_email, posledni_pozvanky, pozvi_cloveka

router = APIRouter()

# Ciselnik patri vsem akcim, takze ho upravuje a maze jen spravce.
# Na jednotlivych routach, ne na celem routeru - cist ho musi kazdy, formulare
# akci z nej vybiraji.
JEN_SPRAVCE = [Depends(auth.vyzaduj_spravce)]


def _strana_lidi(request: Request, sezeni: Session, stav: StavFormulare | None = None):
    """Prehled lidi; se `stav` je to znovu vykresleny formular pridani s chybou u pole."""
    # Napojeny ucet cte sablona u kazdeho cloveka - dohromady, ne po jednom.
    lide = list(
        sezeni.scalars(
            select(Clovek).options(selectinload(Clovek.ucet)).order_by(Clovek.poradi, Clovek.jmeno)
        )
    )
    pozvanky = posledni_pozvanky(sezeni, [c.uzivatel_id for c in lide if c.uzivatel_id is not None])
    kontext = {"lide": lide, "pouziti": pouziti_lidi(sezeni), "posledni_pozvanky": pozvanky}
    if stav is not None:
        return strana_s_chybou(request, "lide.html", stav, **kontext)
    return strana(request, "lide.html", **kontext)


@router.get("/lide", response_class=HTMLResponse)
async def prehled(request: Request, sezeni: Databaze):
    return _strana_lidi(request, sezeni)


@router.post("/lide/pridat")
async def pridat(
    request: Request,
    sezeni: Databaze,
    data: Annotated[ClovekForm | StavFormulare, Depends(formular_s_chybami(ClovekForm))],
):
    if isinstance(data, StavFormulare):
        return _strana_lidi(request, sezeni, data)
    jmeno = data.jmeno
    if not jmeno:
        return _strana_lidi(
            request, sezeni, stav_s_chybami(request, jmeno="Člověk musí mít jméno.")
        )
    if uz_existuje(jmeno, sezeni.scalars(select(Clovek.jmeno))):
        return _strana_lidi(
            request, sezeni, stav_s_chybami(request, jmeno="Tohle jméno už v seznamu je.")
        )

    poradi = dalsi_poradi(sezeni.scalars(select(Clovek.poradi)), zacatek=1)
    sezeni.add(Clovek(jmeno=jmeno, poradi=poradi))
    sezeni.commit()
    oznam(request, "Přidáno do seznamu.")
    return zpet("/lide")


@router.post("/lide/ulozit", dependencies=JEN_SPRAVCE)
async def ulozit(request: Request, sezeni: Databaze):
    """Prejmenovani se propise vsude naráz - u uctenek i na uz vytistenych
    dokladech se jmeno cte pres odkaz, ne jako opsany text."""
    formular = await nacti_formular(request)
    over_delky(
        formular,
        {
            "jmeno": Clovek.jmeno,
            "adresa": Clovek.adresa,
            "kontakt": Clovek.kontakt,
            "email": Clovek.email,
        },
    )
    # Zmena bydliste ci kontaktu se promita do cestaku: "cti -> pocitej -> zapis"
    # pod zamkem, aby souběžný výběr cestujícího neopsal starou hodnotu.
    zamkni_zapis(sezeni)
    lide = vsichni_lide(sezeni)
    # Jmeno, ktere uz patri jinemu cloveku, se neprejmenuje - jinak by
    # commit spadl na unikatnim sloupci a zahodil by i ostatni upravy.
    odmitnuta = prejmenuj_bez_kolizi(
        lide, {c.id: text_z_formulare(formular, f"jmeno_{c.id}") for c in lide}, "jmeno"
    )
    chyby: list[str] = [
        f"„{jmeno}“ už v seznamu je, přejmenování jsem neuložil." for jmeno in odmitnuta
    ]
    for clovek in lide:
        clovek.adresa = prazdne_na_none(text_z_formulare(formular, f"adresa_{clovek.id}"))
        clovek.kontakt = prazdne_na_none(text_z_formulare(formular, f"kontakt_{clovek.id}"))
        # Cestaky otevrenych akci dostanou nove udaje, uzavrene ne.
        cestaky.promit_udaje_cloveka(sezeni, clovek)
        novy_email = prazdne_na_none(text_z_formulare(formular, f"email_{clovek.id}"))
        # Z e-mailu cloveka jde pozvanka - seznam adres by ji poslal vice lidem
        #. Tvar se kontroluje jen u zmeneneho e-mailu, starsi hodnota
        # nema blokovat kazde ulozeni tabulky.
        chyba_emailu = over_email(novy_email) if novy_email and novy_email != clovek.email else None
        if chyba_emailu:
            chyby.append(f"„{clovek.jmeno}“: {chyba_emailu}")
        else:
            clovek.email = novy_email
        poradi = text_z_formulare(formular, f"poradi_{clovek.id}")
        if (cislo := poradi_z_textu(poradi)) is not None:
            clovek.poradi = cislo
    sezeni.commit()
    for chyba in chyby:
        oznam(request, chyba, "chyba")
    if not chyby:
        oznam(request, "Uloženo.")
    return zpet("/lide")


@router.post("/lide/{clovek_id}/smazat", dependencies=JEN_SPRAVCE)
async def smazat(request: Request, clovek_id: int, sezeni: Databaze):
    clovek = sezeni.get(Clovek, clovek_id)
    if clovek is None:
        return zpet("/lide")

    if duvod := proc_nejde_smazat(sezeni, clovek):
        oznam(request, duvod, "chyba")
        return zpet("/lide")

    sezeni.query(Slozka).filter(Slozka.clovek_id == clovek_id).update({"clovek_id": None})
    sezeni.delete(clovek)
    sezeni.commit()
    oznam(request, "Smazáno ze seznamu.")
    return zpet("/lide")


@router.post("/lide/{clovek_id}/pozvat")
def pozvat(
    request: Request,
    clovek_id: int,
    sezeni: Databaze,
    aktualni: Uzivatel = Depends(auth.vyzaduj_spravce),
):
    clovek = sezeni.get(Clovek, clovek_id)
    if clovek is None:
        return zpet("/lide")

    try:
        _ucet, zaznam = pozvi_cloveka(sezeni, clovek, aktualni)
    except ValueError as chyba:
        sezeni.rollback()
        oznam(request, str(chyba), "chyba")
        return zpet("/lide")

    sezeni.commit()
    if zaznam is None:
        oznam(request, "Člověk má už účet se stejným e-mailem — jen propojen.")
    elif posta.neodeslano(zaznam):
        oznam(request, f"Pozvánka se neodeslala. {posta.hlaska_neodeslano(zaznam)}", "chyba")
    elif zaznam.stav == STAV_EMAILU_LOKALNE:
        oznam(request, f"Pozvánka se neodeslala. {posta.HLASKA_LOKALNE}", "pozor")
    else:
        oznam(request, "Pozvánka odeslána.")
    return zpet("/lide")


@router.post("/lide/{clovek_id}/odpojit")
async def odpojit(
    request: Request,
    clovek_id: int,
    sezeni: Databaze,
    _aktualni: Uzivatel = Depends(auth.vyzaduj_spravce),
):
    """Odpoji ucet od cloveka v ciselniku. Ucet samotny (i pripadna historie
    podpisu) zustava - jen uz nema vazbu na tohoto cloveka, takze appka
    prestane nabizet napr. "Poslat pozvánku znovu"."""
    clovek = sezeni.get(Clovek, clovek_id)
    if clovek is None:
        return zpet("/lide")

    clovek.uzivatel_id = None
    sezeni.commit()
    oznam(request, "Účet odpojen.")
    return zpet("/lide")
