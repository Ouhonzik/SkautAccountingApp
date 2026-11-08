/* Chovani radku tabulek: tuzticka (prepnuti radku do upravy), cerveny priznak
   nezkontrolovaneho radku s pocitadlem, vyber dokladu u prilohy a druhy volny radek
   v rozdeleni uctenky. Nacita se jen v sablonach, ktere tyhle prvky maji. */

(function () {
  "use strict";

  // Bublina vpravo dole je ve spolecne.js; tady se jen pouziva.
  var ukazStav = window.appka.ukazStav;
  var jenCteni = window.appka.jenCteni;

  // ---- 3. Tuzticka: prepnuti radku do rezimu upravy ----

  function nastavTuzticku(tlacitko) {
    // Obe podoby ikonky chodi ze sablony - texty patri tam, ne sem. Kdyz
    // sablona posle jen jednu, glyf se nemeni a chova se to jako driv.
    var ikonaUprav = tlacitko.dataset.ikona || tlacitko.textContent;
    var ikonaHotovo = tlacitko.dataset.ikonaHotovo;

    tlacitko.addEventListener("click", function () {
      // Radek tabulky (`tr`), nebo cely blok jako hlavicka cestaku
      // (`.radek-upravy`) - tuzticka prepina, co je blize.
      var radek = tlacitko.closest("tr, .radek-upravy");
      if (!radek) return;
      var upravuje = radek.classList.toggle("upravuje-se");
      tlacitko.setAttribute("aria-pressed", upravuje ? "true" : "false");
      tlacitko.title = upravuje ? "Hotovo, uložit řádek" : "Upravit řádek";
      // Čtečka obrazovky bere jméno tlačítka z aria-label, ne z title.
      // Řádek, ke kterému tužtička patří, posílá šablona v data-radek.
      if (tlacitko.dataset.radek !== undefined) {
        tlacitko.setAttribute(
          "aria-label",
          (upravuje ? "Hotovo, uložit řádek " : "Upravit řádek ") + tlacitko.dataset.radek
        );
      }
      if (ikonaHotovo) tlacitko.textContent = upravuje ? ikonaHotovo : ikonaUprav;
      if (upravuje) {
        var prvni = radek.querySelector(".uprava");
        if (prvni) prvni.focus();
      }
    });
  }

  // ---- 4. Cerveny priznak nezkontrolovaneho radku ----
  // Cervena z radku zmizi hned po zaskrtnuti, bez znovunacteni stranky.

  function nastavPriznakKontroly(policko) {
    var radek = policko.closest("tr");
    if (!radek) return;
    function prekresli() {
      radek.classList.toggle("nezkontrolovano", !policko.checked);
      policko.title = policko.checked ? "Zkontrolováno" : "Zatím nezkontrolováno";
    }
    policko.addEventListener("change", prekresli);
    prekresli();
  }

  // ---- 4b. Priloha: ke kteremu dokladu patri ----
  // Vyber "patri k" ma smysl jen u prilohy, tak se ukazuje az po zaskrtnuti.
  // Pri odskrtnuti se vyprazdni - jinak by v nem zustal doklad, ke kteremu uz
  // se nic nevaze, a pri pristim zaskrtnuti by se tise vratil.
  //
  // Moznosti vyberu (vsechny uctenky s vlastnim radkem) sablona nepise do kazdeho
  // radku - HTML by rostlo s druhou mocninou poctu uctenek. Plny vyber ma
  // jen radek, ktery prilohou uz je; ostatni si moznosti pri zaskrtnuti zkopiruji
  // z jedine <template id="moznosti-priloh">. Sama sebe uctenka v nabidce nema.

  function doplnMoznostiPrilohy(vyber, radek) {
    var sablona = document.getElementById("moznosti-priloh");
    // Vic nez zastupna volba = vyber uz je plny (priloha byla prilohou od zacatku
    // nebo se doplnil pri predchozim zaskrtnuti).
    if (!sablona || vyber.options.length > 1) return;
    var vlastni = radek ? radek.dataset.sken : "";
    [].forEach.call(sablona.content.querySelectorAll("option"), function (moznost) {
      if (moznost.value !== vlastni) vyber.appendChild(moznost.cloneNode(true));
    });
  }

  function nastavParovaniPrilohy(policko) {
    var bunka = policko.closest("td");
    var vyber = bunka && bunka.querySelector("select.priloha-k");
    if (!vyber) return;
    // Radek se zjistuje hned: na telefonu je policko behem upravy v okne (js/okno.js),
    // mimo radek tabulky, a pozdejsi `closest("tr")` by nenasel nic.
    var radek = policko.closest("tr");
    function prekresli() {
      if (policko.checked) doplnMoznostiPrilohy(vyber, radek);
      vyber.hidden = !policko.checked;
      if (!policko.checked) vyber.value = "";
    }
    policko.addEventListener("change", prekresli);
    prekresli();
  }

  // Odznacek "X nezkontrolovanych uctenek" v bloku Prehled se prepocita hned
  // po zaskrtnuti. Bez toho by cislo nahore tvrdilo neco jineho nez tabulka
  // pod nim, dokud clovek stranku znovu nenacte.
  //
  // Ceske sklonovani (1 uctenka / 2 uctenky / 5 uctenek) zustava v sablone,
  // odkud sem chodi vsechny tri tvary v data-atributech. Texty patri do
  // sablony, ne do JavaScriptu.

  function nastavPocitadloKontroly() {
    var odznacek = document.querySelector("[data-pocitadlo]");
    var hotovych = document.querySelector("[data-pocet-ok]");
    var policka = [].slice.call(document.querySelectorAll("input.ok-zaskrtavatko"));
    if (!odznacek || !policka.length) return;

    // Pravidlo musi sedet s app/formaty.py:mnozne (tvar podle celeho cisla).
    function tvar(pocet) {
      if (pocet === 1) return odznacek.dataset.jedna;
      if (pocet <= 4) return odznacek.dataset.dve;
      return odznacek.dataset.pet;
    }

    function prepocitej() {
      var zbyva = policka.filter(function (p) { return !p.checked; }).length;
      odznacek.textContent = zbyva ? zbyva + " " + tvar(zbyva) : odznacek.dataset.hotovo;
      odznacek.className = "stitek " + (zbyva ? "chyba-stitek" : "ok");
      if (hotovych) hotovych.textContent = String(policka.length - zbyva);
    }

    policka.forEach(function (policko) {
      policko.addEventListener("change", prepocitej);
    });
  }

  // ---- 5b. Rozdeleni uctenky: druhy prazdny radek az po vyplneni prvniho ----
  // Sablona posila 2 prazdne radky navic (viz komentar v skeny.html), aby bylo
  // kam pridat dalsi cast i bez znovunacteni stranky. Vizualne je ale schovany
  // vsechny krome prvniho - dalsi se odkryje, az predchozi neco obsahuje.

  function nastavRozdeleniUctenky(tabulka) {
    var prazdne = [].slice.call(tabulka.querySelectorAll("tr[data-prazdny]"));
    if (!prazdne.length) return;

    function maHodnotu(radek) {
      return [].some.call(radek.querySelectorAll("input, select"), function (policko) {
        return policko.value !== "";
      });
    }

    function prekresli() {
      var predchoziVyplneny = true;
      prazdne.forEach(function (radek) {
        radek.hidden = !predchoziVyplneny;
        if (predchoziVyplneny) predchoziVyplneny = maHodnotu(radek);
      });
    }

    tabulka.addEventListener("input", prekresli);
    tabulka.addEventListener("change", prekresli);
    prekresli();
  }

  if (!jenCteni) {
    document.querySelectorAll("[data-upravit]").forEach(nastavTuzticku);
    document.querySelectorAll("input.ok-zaskrtavatko").forEach(nastavPriznakKontroly);
    document.querySelectorAll("input.priloha-zaskrtavatko").forEach(nastavParovaniPrilohy);
    nastavPocitadloKontroly();
  }
  // Rozdeleni uctenky jen schovava prazdne radky, nic neuklada - plati i pro revizora.
  document.querySelectorAll("table.rozdeleni-radky").forEach(nastavRozdeleniUctenky);
})();
