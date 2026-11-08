/* Prubezne ukladani formularu misto tlacitka Ulozit.
   Formular s atributem `data-prubezne` se po kazde zmene sam odesle na server
   (hlavicka X-Prubezne-Ulozeni) a bublina ukaze stav. Nacita se v base.html. */

(function () {
  "use strict";

  // Bublina a ulozeni na pozadi jsou ve spolecne.js; tady se jen pouzivaji.
  var ukazStav = window.appka.ukazStav;
  var ulozNaPozadi = window.appka.ulozNaPozadi;
  var schovejBezJs = window.appka.schovejBezJs;
  var jenCteni = window.appka.jenCteni;

  // Jak dlouho se ceka po posledni zmene, nez se ulozi. Kratsi cas = vic
  // dotazu na server pri psani, delsi = uzivatel dele necha nesave zmeny.
  var ZPOZDENI_MS = 700;

  // ---- 1. Prubezne ukladani ----
  //
  // Posila se jen to, co se od posledniho ulozeni zmenilo. Stranka je
  // otevrena minuty az hodiny a mezitim muze stejny radek upravit jina karta
  // nebo jiny uzivatel - kdyby se posilal cely formular, prepsala by se jeho
  // zmena hodnotou, kterou ma tahle stranka od nacteni. Server proto bere
  // jen pole, ktera prisla (viz `zaskrtnuto` v app/routes/spolecne.py).

  // Zaskrtavatko posila "on" kdyz je zaskrtnute a prazdnou hodnotu, kdyz ne -
  // prohlizec by odskrtnute pole jinak vynechal a server by nepoznal rozdil
  // mezi "odskrtnuto" a "nezmeneno".
  function hodnota(prvek) {
    if (prvek.type === "checkbox") return prvek.checked ? prvek.value || "on" : "";
    return prvek.value;
  }

  // Hodnota, kterou prvek mel pri nacteni stranky (tj. kterou ma server).
  function vychozi(prvek) {
    if (prvek.type === "checkbox") return prvek.defaultChecked ? prvek.value || "on" : "";
    if (prvek.tagName === "SELECT") {
      var vybrana = [].filter.call(prvek.options, function (o) { return o.defaultSelected; })[0];
      var prvni = prvek.options[0];
      return vybrana ? vybrana.value : prvni ? prvni.value : "";
    }
    return prvek.defaultValue;
  }

  // Co se uklada: pole s nazvem, ale ne soubory a tlacitka.
  function jeSledovany(prvek) {
    return (
      prvek.name &&
      !prvek.disabled &&
      ["file", "submit", "button", "reset", "image"].indexOf(prvek.type) < 0
    );
  }

  function nastavPrubezneUkladani(formular) {
    var casovac = null;
    var posledniSelhalo = false;
    var ulozeno = new Map(); // prvek -> hodnota, kterou ma server

    function sledovane() {
      return [].filter.call(formular.elements, jeSledovany);
    }

    sledovane().forEach(function (prvek) {
      ulozeno.set(prvek, vychozi(prvek));
    });

    // Tlacitko Ulozit uz neni potreba - ma smysl jen pro prohlizec bez JS.
    schovejBezJs(formular);

    // Prvky, ktere se maji poslat: zmenene a cele skupiny, do kterych patri
    // (`data-skupina`) - rozpis uctenky je jeden celek, cast se v nem maze
    // vymazanim castky, takze by server z poloviny rozpisu poznal chybne smazani.
    function kPosilani() {
      var vse = sledovane();
      var zmenene = vse.filter(function (prvek) {
        var mene = ulozeno.has(prvek) ? ulozeno.get(prvek) : vychozi(prvek);
        return hodnota(prvek) !== mene;
      });
      var skupiny = {};
      zmenene.forEach(function (prvek) {
        if (prvek.dataset.skupina) skupiny[prvek.dataset.skupina] = true;
      });
      return vse.filter(function (prvek) {
        return zmenene.indexOf(prvek) >= 0 || skupiny[prvek.dataset.skupina];
      });
    }

    function sestavTelo(prvky) {
      var telo = new FormData();
      prvky.forEach(function (prvek) {
        telo.append(prvek.name, hodnota(prvek));
      });
      return telo;
    }

    function ulozit() {
      var prvky = kPosilani();
      if (!prvky.length) {
        // Zmena se vratila na puvodni hodnotu - co je na strance, to server ma.
        posledniSelhalo = false;
        ukazStav("Uloženo", "hotovo");
        return Promise.resolve(true);
      }
      // Hodnoty v okamziku odeslani: kdyz clovek mezitim pise dal, "ulozeno"
      // se posune jen k tomu, co opravdu odeslo.
      var poslano = prvky.map(function (prvek) { return [prvek, hodnota(prvek)]; });
      return ulozNaPozadi(formular.action, sestavTelo(prvky), { znovu: ulozit }).then(function (ok) {
        posledniSelhalo = !ok;
        if (ok) poslano.forEach(function (dvojice) { ulozeno.set(dvojice[0], dvojice[1]); });
        return ok;
      });
    }

    function naplanuj(udalost) {
      if (udalost.target && udalost.target.type === "file") return;
      clearTimeout(casovac);
      casovac = setTimeout(ulozit, ZPOZDENI_MS);
    }

    // Tlacitko Ulozit v okne na telefonu (js/okno.js) chce ulozit hned a vedet, kdy je hotovo.
    formular.addEventListener("appka:uloz-ted", function (udalost) {
      clearTimeout(casovac);
      udalost.detail.sliby.push(ulozit());
    });

    formular.addEventListener("input", naplanuj);
    formular.addEventListener("change", naplanuj);

    // Kdyby uzivatel odesel ze stranky drive, nez zpozdeni dobehne. Beacon
    // neumi poslat vlastni hlavicku, tak se priznak preda v adrese. Beacon
    // vysledek nehlasi: kdyz ho prohlizec ani nepřijme, nebo uz predtim
    // ukladani selhalo, zeptame se, jestli opravdu odejit.
    window.addEventListener("beforeunload", function (udalost) {
      var prvky = kPosilani();
      if (!prvky.length) return;
      clearTimeout(casovac);
      var adresa = formular.action + (formular.action.indexOf("?") < 0 ? "?" : "&");
      var prijato = navigator.sendBeacon(adresa + "prubezne=1", sestavTelo(prvky));
      if (!prijato || posledniSelhalo) {
        udalost.preventDefault();
        udalost.returnValue = "";
      }
    });
  }

  if (!jenCteni) {
    document.querySelectorAll("form[data-prubezne]").forEach(nastavPrubezneUkladani);
  }
})();
