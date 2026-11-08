/* Stranka Log cinnosti. Jen nadstavba pro telefon, na pocitaci nedela nic:
   - nabidka "Filtr" je v HTML otevrena (pocitac a bez JavaScriptu je filtr videt);
     na telefonu se zavre, pokud se nefiltruje (data-aktivni = filtruje se),
   - zaznam v seznamu je na telefonu na jeden radek; klepnuti na radek (nebo na
     tlacitko u nej, to je pro klavesnici a ctecku) ukaze cely text.
   Hranice telefonu je stejna jako v app.css (37,5 rem). */

(function () {
  "use strict";

  var telefon = window.matchMedia("(max-width: 37.5rem)");

  var filtr = document.querySelector("details.log-filtr");
  if (filtr) {
    var nastavFiltr = function () {
      filtr.open = !telefon.matches || filtr.hasAttribute("data-aktivni");
    };
    nastavFiltr();
    telefon.addEventListener("change", nastavFiltr);
  }

  function prepni(polozka) {
    var rozbaleno = !polozka.classList.contains("rozbaleno");
    polozka.classList.toggle("rozbaleno", rozbaleno);
    var tlacitko = polozka.querySelector(".log-rozbalit");
    if (tlacitko) tlacitko.setAttribute("aria-expanded", rozbaleno ? "true" : "false");
  }

  // Jeden posluchac na cely seznam. Odkaz na akci se ma otevrit, ne rozbalit;
  // oznaceny text (kopirovani) radek taky nerozbali.
  document.querySelectorAll(".log-polozky").forEach(function (seznam) {
    seznam.addEventListener("click", function (udalost) {
      if (!telefon.matches) return;
      var polozka = udalost.target.closest(".log-polozka");
      if (!polozka || udalost.target.closest("a")) return;
      var vyber = window.getSelection();
      if (vyber && !vyber.isCollapsed && polozka.contains(vyber.anchorNode)) return;
      prepni(polozka);
    });
  });
})();
