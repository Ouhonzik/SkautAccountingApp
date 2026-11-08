# Ručně přibalené knihovny

Tyhle dvě knihovny nejsou v `pyproject.toml` ani v `uv.lock`, takže je nehlídá
`pip-audit` ani `uv lock --upgrade`. Verzi a zdroj proto držíme tady; při každé kontrole
zranitelností před nasazením porovnej verzi s nejnovějším vydáním na GitHubu.

| Knihovna | Verze | Soubory | Vydání a hlášení zranitelností |
|---|---|---|---|
| Leaflet 1.9.4 (mapa) | 1.9.4 | `leaflet/leaflet.js`, `leaflet/leaflet.css`, `leaflet/images/` | https://github.com/Leaflet/Leaflet/releases |
| Pico CSS 2.1.1 (základní vzhled) | 2.1.1 | `pico.min.css` | https://github.com/picocss/pico/releases |

Verze je v prvních řádcích souborů `leaflet.js` a `pico.min.css`.

Po výměně knihovny: změň verzi tady, ověř mapu a vzhled v prohlížeči.
