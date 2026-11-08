"""Nastaveni logovani appky - jedno misto pro format a urovne.

Pravidlo urovni:
  error   - selhala operace, kterou musi nekdo resit (SMTP, obnova, chyba 500)
  warning - selhalo neco, co appka obesla (Gemini, Mapy.com, nahled, chyby 4xx)
  info    - start appky a jednorazove dulezite akce
  debug   - jen lokalne pri ladeni

Do logu se nikdy nezapisuje token, heslo, query string ani osobni udaj
. Cesty s tokenem maskuje `auth.cesta_pro_log`.
"""

import logging

# Tyhle knihovny na urovni INFO zapisuji celou URL odchozich pozadavku
# (napr. Mapy.com s hledanou adresou v `query=`) - to je osobni udaj.
TISI_KNIHOVNY = ("httpx", "httpcore", "google_genai")


def nastav_logovani(uroven: str) -> None:
    """Zavola se jednou pri importu app/main.py (ne v `run()`), aby nastaveni
    platilo i v podprocesu, ktery uvicorn pri RELOAD=true spousti sam."""
    logging.basicConfig(level=uroven, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for jmeno in TISI_KNIHOVNY:
        logging.getLogger(jmeno).setLevel(logging.WARNING)
