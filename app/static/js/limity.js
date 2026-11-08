/* Okno Limity na stránce Uživatelé (jen správce).
   Každý účet má v šabloně svůj <dialog class="okno-limity" id="limity_<id>"> s vlastním formulářem
   (POST /uzivatele/<id>/limity); tlačítko `data-limity="limity_<id>"` ho otevře. Poslouchá se
   na celém dokumentu, protože na telefonu js/okno.js tlačítko přesouvá do okna řádku a tlačítko
   pak není tam, kde bylo; okno Limity se otevře nad oknem řádku. Bez JavaScriptu se okno
   neotevře, takže limity jdou změnit jen s ním. */

(function () {
  "use strict";

  if (!document.createElement("dialog").showModal) return;

  document.addEventListener("click", function (udalost) {
    var cil = udalost.target;
    var otevrit = cil.closest("[data-limity]");
    if (otevrit) {
      var okno = document.getElementById(otevrit.dataset.limity);
      if (okno && !okno.open) okno.showModal();
      return;
    }
    var zavrit = cil.closest(".limity-zavrit");
    if (zavrit) {
      zavrit.closest("dialog").close();
      return;
    }
    // Klepnutí na ztmavené pozadí (celý <dialog> je průhledný překryv, panel je v něm).
    // Druhé klepnutí dvojkliku (`detail` 2) se nebere: první otevřelo okno a druhé by
    // dopadlo na pozadí a hned ho zase zavřelo.
    if (cil.matches("dialog.okno-limity") && udalost.detail < 2) cil.close();
  });

  // Číslo vedle posuvníku ukazuje, co je právě nastavené.
  document.addEventListener("input", function (udalost) {
    var posuvnik = udalost.target;
    if (!posuvnik.matches || !posuvnik.matches("dialog.okno-limity input[type=range]")) return;
    var cislo = posuvnik.closest("label").querySelector("output");
    if (cislo) cislo.value = posuvnik.value;
  });

  // Zavřené okno (Zavřít, Esc, pozadí) zahodí neuložené změny: příště se otevře s tím, co je uložené.
  // Událost `close` nebublá, proto se chytá ve fázi zachycení.
  document.addEventListener(
    "close",
    function (udalost) {
      var okno = udalost.target;
      if (!okno.matches || !okno.matches("dialog.okno-limity")) return;
      okno.querySelector("form").reset();
      okno.querySelectorAll("input[type=range]").forEach(function (posuvnik) {
        var cislo = posuvnik.closest("label").querySelector("output");
        if (cislo) cislo.value = posuvnik.value;
      });
    },
    true
  );
})();
