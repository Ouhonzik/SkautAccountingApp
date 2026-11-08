/* Nahravani souboru: pretahovani mysi do plochy a nahrani hned po vyberu
   (bez tlacitka Nahrat). Nacita se jen tam, kde jsou plochy `data-pretahovani`
   nebo policka `data-odeslat-hned`. */

(function () {
  "use strict";

  // Bublina vpravo dole je ve spolecne.js; tady se jen pouziva.
  var ukazStav = window.appka.ukazStav;
  var schovejBezJs = window.appka.schovejBezJs;
  var jenCteni = window.appka.jenCteni;

  // ---- 2. Pretahovani souboru ----

  function nastavPretahovani(plocha) {
    var vstup = plocha.querySelector("input[type=file]");
    var formular = plocha.closest("form");
    if (!vstup || !formular) return;

    ["dragenter", "dragover"].forEach(function (jmeno) {
      plocha.addEventListener(jmeno, function (u) {
        u.preventDefault();
        plocha.classList.add("pretahuje-se");
      });
    });
    ["dragleave", "drop"].forEach(function (jmeno) {
      plocha.addEventListener(jmeno, function (u) {
        u.preventDefault();
        plocha.classList.remove("pretahuje-se");
      });
    });

    plocha.addEventListener("drop", function (u) {
      if (!u.dataTransfer || !u.dataTransfer.files.length) return;
      vstup.files = u.dataTransfer.files;
      odesli();
    });

    // Vybrane soubory se nahraji rovnou - tlacitko Nahrat uz neni potreba.
    // Vyjimka je rezim "rucne": tam se soubor jen vlozi a ceka na kliknuti.
    var rucne = plocha.dataset.pretahovani === "rucne";
    if (!rucne) {
      vstup.addEventListener("change", odesli);
      // Kdyz se soubory odesilaji samy, tlacitko Nahrat uz nema co delat.
      schovejBezJs(formular);
    }

    // Fotoaparat (jen telefon, tlacitko ukazuje CSS): vyfocena uctenka se vlozi
    // do hlavniho policka a odesle se jako kazde jine nahrani.
    var kamera = plocha.querySelector("input[data-fotoaparat]");
    if (kamera && !rucne) {
      kamera.addEventListener("change", function () {
        if (!kamera.files.length) return;
        vstup.files = kamera.files;
        odesli();
      });
    }

    function odesli() {
      if (!vstup.files.length || rucne) return;
      ukazStav("Nahrávám…", "");
      formular.requestSubmit();
    }
  }

  // ---- 5d. Soubor se nahraje hned po vyberu ----
  // Pro male nahravaci policko (technicak u vozidla), kde se velka plocha
  // na pretahovani nevejde. Policko muze patrit k formulari stojicimu mimo
  // tabulku (atribut `form`), proto `vstup.form`, ne `closest("form")`.

  function nastavOdeslaniHned(vstup) {
    var formular = vstup.form;
    if (!formular) return;
    schovejBezJs(document, '[data-bez-js][form="' + formular.id + '"]');
    vstup.addEventListener("change", function () {
      if (vstup.files.length) formular.requestSubmit();
    });
  }

  if (!jenCteni) {
    document.querySelectorAll("[data-pretahovani]").forEach(nastavPretahovani);
    document.querySelectorAll("input[data-odeslat-hned]").forEach(nastavOdeslaniHned);
  }
})();
