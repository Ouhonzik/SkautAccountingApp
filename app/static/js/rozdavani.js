/* Rozdavani veci obdarovanym pretahovanim mysi. Nacita se jen na strance
   obdarovanych (obdarovani.html). */

(function () {
  "use strict";

  // Ulozeni na pozadi a bublina jsou ve spolecne.js; tady se jen pouzivaji.
  var ulozNaPozadi = window.appka.ulozNaPozadi;
  var jenCteni = window.appka.jenCteni;

  // ---- 8. Rozdavani veci obdarovanym ----
  // Karticka s veci se chytne mysi a pusti u jmena, nebo zpatky do zasobniku.
  // Kdo tahat nechce (nebo nemuze), klikne na karticku a pak na radek cloveka -
  // dela to presne totez.
  //
  // Prihradka = misto, kam vec patri: bunka u cloveka (data-prihradka="12")
  // nebo zasobnik (data-prihradka=""). Kdyz se karticka presune, posle se to
  // rovnou na server a stranka se neprekresluje - proto se pocty a hlasky
  // "nic" musi dopocitat tady.

  function prihradkaKarticky(karticka) {
    return karticka.closest("[data-prihradka]");
  }

  function prekresliPrihradku(prihradka) {
    if (!prihradka) return;
    var prazdno = prihradka.querySelector("[data-prazdno]");
    if (prazdno) prazdno.hidden = prihradka.querySelector(".dar") !== null;
  }

  function prekresliZasobnik(plocha) {
    var zasobnik = plocha.querySelector('[data-prihradka=""]');
    var pocitadlo = plocha.querySelector("[data-pocet-zasobniku]");
    if (zasobnik && pocitadlo) {
      pocitadlo.textContent = zasobnik.querySelectorAll(".dar").length;
    }
    prekresliPrihradku(zasobnik);
  }

  // Vybrana vec: stav drzi kartu, aria-pressed ji ohlasi ctecce obrazovky a
  // tlacitka "Dat sem" u lidi se ukazou jen dokud je co davat - jinak
  // by jich bylo v klavesnicovem poradi tolik, kolik je lidi, a nic by nedelaly.
  function ukazDatSem(plocha, ukaz) {
    plocha.querySelectorAll("[data-dat-sem]").forEach(function (t) { t.hidden = !ukaz; });
  }

  function ohlas(plocha, text) {
    var hlaseni = plocha.querySelector("[data-hlaseni]");
    if (hlaseni) hlaseni.textContent = text;
  }

  function nazevKarticky(karticka) {
    return karticka.querySelector(".dar-nazev").textContent.trim();
  }

  function vyber(stav, karticka) {
    stav.vybrana = karticka;
    karticka.classList.add("dar-vybrany");
    karticka.querySelector(".dar-nazev").setAttribute("aria-pressed", "true");
    ukazDatSem(stav.plocha, true);
    ohlas(stav.plocha, "Vybráno: " + nazevKarticky(karticka) +
      ". U člověka stiskni tlačítko Dát sem, Escape výběr zruší.");
  }

  function odznac(stav) {
    if (stav.vybrana) {
      stav.vybrana.classList.remove("dar-vybrany");
      stav.vybrana.querySelector(".dar-nazev").setAttribute("aria-pressed", "false");
    }
    stav.vybrana = null;
    ukazDatSem(stav.plocha, false);
  }

  function posli(plocha, adresa, telo, karticka, puvodni) {
    ulozNaPozadi(adresa, telo, { chyba: "Nepodařilo se uložit — zkus to znovu" }).then(
      function (ok) {
        if (ok) return;
        // Server o presunu nevi, tak at ho nevidi ani obrazovka - jinak by
        // Honza rozdal veci, ktere se ve skutecnosti nikam neulozily.
        if (puvodni && karticka) puvodni.appendChild(karticka);
        prekresliPrihradku(puvodni);
        prekresliZasobnik(plocha);
      }
    );
  }

  // Krizek dela neco jineho podle toho, kde vec prave lezi. Po presunu se
  // proto prepise i jeho popisek - jinak by u cloveka slibovoval smazani.
  function nastavKrizek(karticka) {
    var vZasobniku = prihradkaKarticky(karticka).dataset.prihradka === "";
    var krizek = karticka.querySelector(".dar-pryc");
    if (krizek) {
      krizek.title = vZasobniku ? "Smazat věc" : "Vrátit do zásobníku";
      // Ctecka obrazovky bere jmeno z aria-label, ne z title.
      krizek.setAttribute(
        "aria-label",
        (vZasobniku ? "Smazat věc " : "Vrátit do zásobníku věc ") + nazevKarticky(karticka)
      );
    }
  }

  function presun(plocha, karticka, cil) {
    var puvodni = prihradkaKarticky(karticka);
    if (!cil || cil === puvodni) return;

    // Prazdna hlaska ("nic") je taky v prihradce, takze se karticka vklada
    // pred ni - jinak by visela pod textem.
    var prazdno = cil.querySelector("[data-prazdno]");
    cil.insertBefore(karticka, prazdno);
    prekresliPrihradku(puvodni);
    prekresliPrihradku(cil);
    prekresliZasobnik(plocha);
    nastavKrizek(karticka);

    var telo = new FormData();
    telo.append("obdarovany", cil.dataset.prihradka);
    posli(plocha, "/dar/" + karticka.dataset.dar + "/priradit", telo, karticka, puvodni);
  }

  // Tazeni mysi (HTML drag & drop): karticka se chyti, nad prihradkou se
  // zvyrazni cil a pusteni ji presune. `presunKarticku(karticka, cil)`.
  function napojTazeniKarticek(plocha, stav, presunKarticku) {
    plocha.addEventListener("dragstart", function (u) {
      var karticka = u.target.closest(".dar");
      if (!karticka) return;
      odznac(stav);
      u.dataTransfer.setData("text/plain", karticka.dataset.dar);
      u.dataTransfer.effectAllowed = "move";
      karticka.classList.add("dar-taha-se");
    });

    plocha.addEventListener("dragend", function (u) {
      var karticka = u.target.closest(".dar");
      if (karticka) karticka.classList.remove("dar-taha-se");
    });

    plocha.addEventListener("dragover", function (u) {
      var cil = u.target.closest("[data-prihradka]");
      if (!cil) return;
      // Bez tohohle prohlizec pusteni odmitne - vychozi chovani je "sem ne".
      u.preventDefault();
      u.dataTransfer.dropEffect = "move";
      cil.classList.add("prihradka-cil");
    });

    plocha.addEventListener("dragleave", function (u) {
      var cil = u.target.closest("[data-prihradka]");
      if (cil) cil.classList.remove("prihradka-cil");
    });

    plocha.addEventListener("drop", function (u) {
      var cil = u.target.closest("[data-prihradka]");
      if (!cil) return;
      u.preventDefault();
      cil.classList.remove("prihradka-cil");
      var karticka = plocha.querySelector(
        '.dar[data-dar="' + u.dataTransfer.getData("text/plain") + '"]'
      );
      if (karticka) presunKarticku(karticka, cil);
    });
  }

  // Krizek na karticce: ze zasobniku vec smaze (po dotazu), u cloveka ji vrati
  // do zasobniku.
  function obsluzKrizek(plocha, u, krizek, presunKarticku) {
    // Formular u krizku je zaloha pro prohlizec bez JavaScriptu. Tady se
    // stejna vec udela na miste, bez znovunacteni stranky.
    u.preventDefault();
    var karticka = krizek.closest(".dar");
    var kde = prihradkaKarticky(karticka);
    if (kde.dataset.prihradka !== "") {
      presunKarticku(karticka, plocha.querySelector('[data-prihradka=""]'));
      return;
    }
    // Smazani veci ze zasobniku je nevratne. Text se sklada tady, ne
    // z formulare: vec presunuta ze cloveka do zasobniku ma porad formular
    // "priradit", ktery nic nemaze.
    var jmenoVeci = karticka.querySelector(".dar-nazev").textContent;
    if (!window.confirm("Smazat věc „" + jmenoVeci + "“? Vrátit to nejde.")) return;
    karticka.remove();
    prekresliZasobnik(plocha);
    posli(plocha, "/dar/" + karticka.dataset.dar + "/smazat", new FormData(), null, null);
  }

  // Klikani: karticka se vybere a pak se klikne na radek cloveka.
  function napojKlikaniKarticek(plocha, stav, presunKarticku) {
    plocha.addEventListener("click", function (u) {
      var krizek = u.target.closest(".dar-pryc");
      if (krizek) {
        obsluzKrizek(plocha, u, krizek, presunKarticku);
        return;
      }

      var naKarticce = u.target.closest(".dar");
      if (naKarticce) {
        var uzVybrana = naKarticce === stav.vybrana;
        odznac(stav);
        if (!uzVybrana) vyber(stav, naKarticce);
        return;
      }

      // Kliknuti do prihradky ma smysl jen tehdy, kdyz uz je vec vybrana.
      // Jinak by se cesta "kliknu na cloveka a pak na vec" tvarila, ze nefunguje.
      var cil = u.target.closest("[data-prihradka]");
      if (cil && stav.vybrana) {
        var presouvana = stav.vybrana;
        presunKarticku(presouvana, cil);
        odznac(stav);
        // Tlacitko "Dat sem", na ktere stal fokus, se tim schovalo; fokus by
        // spadl na zacatek stranky, tak ho vratime na presunutou vec.
        if (u.target.closest("[data-dat-sem]")) {
          presouvana.querySelector(".dar-nazev").focus();
          ohlas(stav.plocha, "Věc " + nazevKarticky(presouvana) + " je přesunutá.");
        }
      }
    });

    // Z klavesnice: Enter nebo mezera na vybirani veci, Escape vyber zrusi.
    // Veci jsou <span role="button">, ne <button> - <button> uvnitr tazitelneho
    // prvku by ve Firefoxu zabranil pretahovani mysi.
    plocha.addEventListener("keydown", function (u) {
      var nazev = u.target.closest(".dar-nazev");
      if ((u.key === "Enter" || u.key === " ") && nazev && u.target === nazev) {
        u.preventDefault();
        nazev.click();
      } else if (u.key === "Escape" && stav.vybrana) {
        var vybrana = stav.vybrana;
        odznac(stav);
        vybrana.querySelector(".dar-nazev").focus();
        ohlas(stav.plocha, "Výběr zrušen.");
      }
    });
  }

  function nastavRozdavani(plocha) {
    var stav = { vybrana: null, plocha: plocha };  // karticka vybrana klikem
    var presunKarticku = function (karticka, cil) { presun(plocha, karticka, cil); };

    plocha.querySelectorAll(".dar").forEach(nastavKrizek);
    napojTazeniKarticek(plocha, stav, presunKarticku);
    napojKlikaniKarticek(plocha, stav, presunKarticku);
  }

  if (!jenCteni) {
    document.querySelectorAll("[data-rozdavani]").forEach(nastavRozdavani);
  }
})();
