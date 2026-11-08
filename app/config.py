"""Nastaveni aplikace.

Vsechno citlive (API klic, heslo) se cte ze souboru .env, ktery neni v gitu.
Diky tomu je kod verejne sdilitelny, aniz by obsahoval tajne udaje.
"""

import ipaddress
from decimal import Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

KOREN_PROJEKTU = Path(__file__).resolve().parent.parent

# Hodnoty SECRET_KEY, ktere znamenaji "jeste nevyplneno": vychozi hodnota
# v kodu a zastupny text z drivejsiho .env.example. Oboje je verejne na
# GitHubu - kdo je zna, podepise si vlastni prihlasovaci cookie.
ZASTUPNE_KLICE = {"", "zmen-me-pred-nasazenim", "vygenerujeme-pozdeji"}
MIN_DELKA_KLICE = 32


class Nastaveni(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=KOREN_PROJEKTU / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Tajna pole maji repr=False, aby se hodnota nikdy neukazala v repr/str
    # nastaveni - tedy ani v chybovych hlaskach, tracebacich a vypisech padlych
    # testu.
    google_api_key: str = Field(default="", repr=False)
    # Druhy klic ze zpoplatneneho projektu. Kdyz zustane prazdny, prepinac
    # tarifu se v aplikaci vubec neukaze a cte se jen zdarma.
    google_api_key_placeny: str = Field(default="", repr=False)
    gemini_model: str = "gemini-3.5-flash"
    # APP_USERNAME a APP_PASSWORD_HASH uz appka pro prihlaseni nepouziva -
    # ucty jsou v databazi. Zustavaji tu jen kvuli prechodu: migrace
    # b42fe20a9964 z nich pri prvnim spusteni po upgradu zalozi spravce.
    app_username: str = "honza"
    app_password_hash: str = Field(default="", repr=False)
    secret_key: str = Field(default="zmen-me-pred-nasazenim", repr=False)
    # "server" nastavuje systemd sluzba uctovani.service (Environment=PROSTREDI=server),
    # schvalne ne .env: radek v .env se da pri kopirovani lokalniho souboru
    # zapomenout nebo prepsat, soubor sluzby je v gitu. Serverovy rezim se
    # navic pozna z VEREJNA_URL mimo localhost (viz zkontroluj_nastaveni).
    prostredi: Literal["vyvoj", "server"] = "vyvoj"
    # Na serveru za HTTPS (Caddy) nastav v .env na true - prihlasovaci cookie
    # se pak posila jen sifrovane. Lokalne na http://localhost musi zustat
    # false, jinak by se prohlizec vubec neprihlasil.
    cookie_https_only: bool = False
    data_dir: Path = Path("data")
    port: int = 8000
    # Na jake adrese appka poslouchá (HOST). Vychozi 127.0.0.1 = jen z tohoto
    # pocitace (na serveru k ni pristupuje jen Caddy). Jina adresa je potreba,
    # kdyz Caddy bezi v Dockeru: pak appka slucha na brane Docker site
    # (napr. 172.18.0.1). 0.0.0.0 server odmitne - appka by byla dostupna
    # zvenku bez HTTPS (viz zkontroluj_nastaveni).
    host: str = "127.0.0.1"
    # Odkud uvicorn veri hlavicce X-Forwarded-For (FORWARDED_ALLOW_IPS):
    # carkou oddelene IP adresy nebo site v CIDR (napr. 172.18.0.0/16).
    # Komu se veri, ten si muze zvolit, jakou IP klienta appka uvidi - proto
    # jen adresy reverse proxy, nikdy "*" (server to odmitne).
    forwarded_allow_ips: str = "127.0.0.1"
    # Nejnizsi uroven, kterou appka zapisuje do logu (LOG_UROVEN). Pravidla
    # urovni jsou v app/logovani.py. Na serveru nech INFO, DEBUG jen lokalne.
    log_uroven: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Automaticky sledovat zmeny souboru a restartovat server - hodi se pri
    # vyvoji, na serveru ne (zbytecna zatez a sledovani souboru, ktere se
    # nemeni). Vychozi false je bezpecne pro produkci; lokalne si Honza
    # zapina pres RELOAD=true v .env.
    reload: bool = False
    # Kurz pro odhad utraty za placeny klic ke Gemini (app/services/tarif.py).
    # Neni to nic presneho, jen orientacni castka - staci obcas rucne opravit.
    kurz_usd_czk: Decimal = Decimal(23)
    # Klic z developer.mapy.com pro planovani trasy cestovnich prikazu.
    # Kdyz zustane prazdny, mapa se v cestaku vubec neukaze a jizdy se pisou rucne.
    mapy_api_key: str = Field(default="", repr=False)
    # SMTP pro odesilani e-mailu (pozvanky, reset hesla...). Kdyz zustane
    # smtp_host prazdny, e-mail se misto odeslani jen ulozi do data/posta/
    # jako .eml soubor - pouziva se tak ve vyvoji a v testech.
    smtp_host: str = ""
    smtp_port: int = 587
    # starttls = port 587, ssl = port 465 (spojeni je sifrovane od zacatku)
    smtp_sifrovani: Literal["starttls", "ssl"] = "starttls"
    smtp_user: str = ""
    smtp_password: str = Field(default="", repr=False)
    smtp_odesilatel: str = ""
    # Verejna adresa appky, na kterou se odkazuje z e-mailu (pozvanka, reset
    # hesla...). Na serveru musi byt https adresa domeny, lokalne zustava
    # localhost.
    verejna_url: str = "http://localhost:8000"

    # Udaje strediska v hlavicce vsech tiskopisu, cestaku a Excelu. Menit je
    # staci tady nebo v .env (napr. pri stehovani sidla), nic v kodu. Vychozi
    # hodnoty jsou jen zastupne - kazdy provozovatel si vlastni udaje nastavi
    # v .env; cislo jednotky se predvyplnuje na kazdem cestovnim prikazu, ale
    # da se tam prepsat.
    stredisko_nazev: str = "Název střediska"
    stredisko_sidlo: str = "Adresa střediska"
    stredisko_ico: str = ""
    stredisko_dic: str = ""
    stredisko_jednotka: str = ""

    # Limity nahravani souboru. Jeden soubor a pocet na jedno nahrani
    # jsou technicka ochrana serveru pred zaplnenim pameti/disku - stejna
    # pro celou appku. Vychozi misto na akci jde prebit u konkretni akce
    # (Akce.misto_mb_limit, nastavuje spravce na strance akce).
    max_velikost_souboru_mb: int = 20
    max_souboru_na_nahrani: int = 100
    max_velikost_xlsx_mb: int = 20
    vychozi_misto_akce_mb: int = 1024
    # Zaloha akce nikdy neni vetsi nez soucet jejich souboru (limit vyse) plus
    # male Excely a data.json - 2048 MB je bezpecna rezerva i pro akci, ktere
    # spravce misto_mb_limit zvedl vys nez vychozi hodnotu.
    max_velikost_zalohy_mb: int = 2048

    # Obrana proti "zip bombe" (B-037c): xlsx export i zaloha appky jsou ZIP
    # archivy, kde muze byt sharedStrings.xml nebo data.json samo male, ale po
    # rozbaleni zabrat stovky MB pameti. Kontroluje se soucet ZipInfo.file_size
    # vsech polozek, ne skutecna komprimovana velikost souboru (ta uz hlida
    # max_velikost_xlsx_mb/max_velikost_zalohy_mb vyse).
    max_rozbalena_velikost_mb: int = 50
    # Obrazek nad tuhle plochu (pocet px = sirka * vyska) se pri nahrani
    # odmitne - fotka z mobilu ma radove jednotky Mpx, 40 Mpx je bezpecna
    # rezerva (B-037a).
    max_obrazek_px: int = 40_000_000
    # PDF nad tolik stranek se pri nahrani odmitne - ucet nikdy nema tolik
    # stranek, ze by je bylo potreba naraz vykreslit (B-037b).
    max_stranek_pdf: int = 50

    @property
    def smtp_nastaveno(self) -> bool:
        return bool(self.smtp_host)

    @property
    def secret_key_je_slaby(self) -> bool:
        """Zastupna hodnota nebo klic kratsi nez 32 znaku."""
        return self.secret_key in ZASTUPNE_KLICE or len(self.secret_key) < MIN_DELKA_KLICE

    @property
    def je_localhost(self) -> bool:
        return (urlsplit(self.verejna_url).hostname or "") in ("localhost", "127.0.0.1", "::1")

    @property
    def je_server(self) -> bool:
        """Serverovy rezim: explicitne PROSTREDI=server, nebo adresa appky mimo
        localhost. Druhy zpusob jistí, ze kontroly nevypne zapomenuty radek."""
        return self.prostredi == "server" or not self.je_localhost

    @field_validator("data_dir")
    @classmethod
    def _prevest_na_absolutni_cestu(cls, hodnota: Path) -> Path:
        """Aby aplikace nasla data at ji spustis z jakekoliv slozky."""
        return hodnota if hodnota.is_absolute() else KOREN_PROJEKTU / hodnota

    @property
    def slozka_uploadu(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def soubor_databaze(self) -> Path:
        return self.data_dir / "akce.db"

    @property
    def slozka_tmp(self) -> Path:
        """Rozpracovane ZIPy ke stazeni; pri startu se vyprazdni."""
        return self.data_dir / "tmp"

    @property
    def slozka_posty(self) -> Path:
        return self.data_dir / "posta"

    def zaloz_slozky(self) -> None:
        """Slozka data/ neni v gitu, takze si ji aplikace vytvari sama."""
        self.slozka_uploadu.mkdir(parents=True, exist_ok=True)
        self.slozka_posty.mkdir(parents=True, exist_ok=True)
        self.slozka_tmp.mkdir(parents=True, exist_ok=True)


def _chyby_adres(n: Nastaveni) -> list[str]:
    """Kontrola HOST a FORWARDED_ALLOW_IPS pro server. Uvicorn neplatnou IP nebo
    sit v FORWARDED_ALLOW_IPS potichu zahodi (bere ji jako obycejny text), takze
    preklep by tiše vypnul duveru k proxy a appka by videla jen IP proxy."""
    chyby: list[str] = []

    try:
        host = ipaddress.ip_address(n.host)
    except ValueError:
        chyby.append("HOST musí být IP adresa (např. 127.0.0.1 nebo 172.18.0.1), ne název.")
    else:
        if host.is_unspecified:
            chyby.append(
                "HOST nesmí být 0.0.0.0 ani :: — appka by byla dostupná zvenku bez HTTPS. "
                "Použij adresu, na kterou se dostane jen reverse proxy (127.0.0.1, "
                "u Caddy v Dockeru brána Docker sítě)."
            )

    duveryhodne = [
        polozka.strip() for polozka in n.forwarded_allow_ips.split(",") if polozka.strip()
    ]
    if not duveryhodne:
        chyby.append("FORWARDED_ALLOW_IPS je prázdný — vyplň IP adresy nebo sítě reverse proxy.")
    for polozka in duveryhodne:
        if polozka == "*":
            chyby.append(
                "FORWARDED_ALLOW_IPS nesmí být * — hlavičce X-Forwarded-For by věřil kdokoli "
                "a mohl by si zvolit IP, kterou appka uvidí. Vypiš jen adresy reverse proxy."
            )
            continue
        try:
            sit = ipaddress.ip_network(polozka)
        except ValueError:
            chyby.append(
                f"FORWARDED_ALLOW_IPS: „{polozka}“ není platná IP adresa ani síť v zápisu CIDR "
                "(např. 127.0.0.1 nebo 172.18.0.0/16)."
            )
            continue
        if sit.prefixlen == 0:
            chyby.append(
                f"FORWARDED_ALLOW_IPS: „{polozka}“ pokrývá všechny adresy — hlavičce by věřil kdokoli."
            )
    return chyby


def zkontroluj_nastaveni(n: Nastaveni | None = None) -> tuple[list[str], list[str]]:
    """Vrati (chyby, varovani). Chyba zastavi start appky, varovani se jen vypise.

    Na serveru je chyba kazde nastaveni z vyvoje, ktere by potichu oslabilo
    zabezpeceni nebo rozbilo e-maily. Lokalne jsou
    chyby jen ty, ktere rozbiji odesilani i ve vyvoji.
    """
    n = n or nastaveni
    chyby: list[str] = []
    varovani: list[str] = []

    if n.smtp_host and not n.smtp_odesilatel:
        chyby.append(
            "SMTP_HOST je vyplněný, ale SMTP_ODESILATEL je prázdný — e-maily by odcházely "
            "s prázdným odesílatelem. Vyplň v .env adresu odesílatele."
        )

    if n.je_server:
        if n.secret_key_je_slaby:
            chyby.append(
                f"SECRET_KEY v .env je prázdný, zástupný nebo kratší než {MIN_DELKA_KLICE} znaků. "
                "Kdo ho zná, podepíše si vlastní přihlášení. Vyrob nový: "
                'uv run python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        if not n.verejna_url.startswith("https://"):
            chyby.append(
                "VEREJNA_URL musí na serveru začínat https:// (adresa domény), jinak by odkazy "
                "v e-mailech nefungovaly."
            )
        if not n.cookie_https_only:
            chyby.append(
                "COOKIE_HTTPS_ONLY musí být na serveru true, jinak by se přihlášení posílalo "
                "i nešifrovaně."
            )
        if not n.smtp_host:
            chyby.append(
                "SMTP_HOST je prázdný — na serveru se e-maily (pozvánky, obnova hesla) "
                "neodešlou, jen se uloží na disk."
            )
        if n.reload:
            chyby.append("RELOAD musí být na serveru false.")
        chyby.extend(_chyby_adres(n))
        # Na serveru se čte jen placeným klíčem — bezplatný se nepoužije.
        # Není to chyba startu: appka jede i bez AI, účtenky se pak píšou ručně.
        if not n.google_api_key_placeny:
            varovani.append(
                "Chybí GOOGLE_API_KEY_PLACENY — na serveru se čtení účtenek pomocí AI "
                "nepoužije, bezplatný klíč tam nesmí."
            )
    else:
        if n.secret_key_je_slaby:
            varovani.append(
                "SECRET_KEY je výchozí, prázdný nebo krátký — před nasazením na server ho změň."
            )
        if not n.smtp_host:
            varovani.append("Vývojový režim: e-maily se neodesílají, ukládají se do data/posta/.")

    if not n.je_server and not n.google_api_key:
        varovani.append("Chybí GOOGLE_API_KEY — čtení účtenek pomocí AI nepůjde.")

    return chyby, varovani


nastaveni = Nastaveni()
