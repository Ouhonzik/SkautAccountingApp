/* Prehled akci na telefonu. Na pocitaci nedela nic.
   Radek tabulky je na telefonu nazev, termin, stav a sipka; klepnuti na radek
   (nebo na tlacitko se sipkou, to je pro klavesnici a ctecku) ukaze zbytek udaju.
   Hranice telefonu je stejna jako v app.css (37,5 rem). */

(function () {
  "use strict";

  var telefon = window.matchMedia("(max-width: 37.5rem)");

  function prepni(radek) {
    var rozbaleno = !radek.classList.contains("rozbaleno");
    radek.classList.toggle("rozbaleno", rozbaleno);
    var tlacitko = radek.querySelector(".akce-rozbalit");
    if (tlacitko) tlacitko.setAttribute("aria-expanded", rozbaleno ? "true" : "false");
  }

  // Odkaz na akci se ma otevrit, ne rozbalit; oznaceny text radek taky nerozbali.
  document.querySelectorAll(".akce-tabulka").forEach(function (tabulka) {
    tabulka.addEventListener("click", function (udalost) {
      if (!telefon.matches) return;
      var radek = udalost.target.closest(".akce-radek");
      if (!radek || udalost.target.closest("a")) return;
      var vyber = window.getSelection();
      if (vyber && !vyber.isCollapsed && radek.contains(vyber.anchorNode)) return;
      prepni(radek);
    });
  });
})();
