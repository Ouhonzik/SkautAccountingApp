/* Okno pro upravy radku na telefonu.
   Siroka tabulka s mnoha policky se na telefonu nevejde, proto je v ni misto radku
   jen kratky radek (nazev a castka) a po klepnuti se otevre okno <dialog> s polickami.
   Na pocitaci (nad 37,5 rem) se nic nedeje, tabulka zustava jak byla.

   Znacky v sablone (formular s tabulkou, napr. prijmy.html):
   - `data-okno-seznam` na formulari a `data-okno-nadpis="..."` s nadpisem okna,
   - `data-okno-radek` na <tr>; v nem
       <td class="okno-kratky"> s tlacitkem `.okno-otevrit` (a v nem `data-zrcadlo="jmeno_pole"`
         u textu, ktery ma ukazovat hodnotu pole; `data-format="koruny"`, `data-prazdne="..."`),
       <td data-okno-pole="Popisek"> s polem (jde do okna s popiskem),
       <td data-okno-akce> s tlacitkem, ktere se v okne ukaze dole (napr. Smazat).
     Radek nemusi byt tabulka: stejne znacky ma i blok auta ve vozidla.html (<div>, <label>,
     <span>) - bunkou je libovolny prvek se znackou, jeho obsah se presouva.

   Policka se do okna PRESUNOU (ne zkopiruji): zustavaji v tomtez formulari, majici
   stejna jmena i stejne prvky, takze prubezne ukladani (js/prubezne.js) funguje
   beze zmeny a nic se neposila dvakrat. Po zavreni okna se vrati do sve bunky.
   Bez JavaScriptu se tabulka na telefonu poskladá pod sebe (viz app.css). */

(function () {
  "use strict";

  // Stejna hranice jako v app.css (37,5 rem) - mente obe misto.
  var telefon = window.matchMedia("(max-width: 37.5rem)");
  var jenCteni = window.appka && window.appka.jenCteni;
  var NBSP = " ";
  var cislo = 0;

  // "19200" nebo "19 200,5" -> "19 200,50 Kč" (stejne jako filtr `koruny` na serveru);
  // co se cislem nedá cist, zustane jak to clovek napsal.
  function koruny(text) {
    var cista = text.replace(/[\s ]/g, "").replace(",", ".");
    if (!/^-?\d+(\.\d+)?$/.test(cista)) return text.trim();
    var casti = Number(cista).toFixed(2).split(".");
    return casti[0].replace(/\B(?=(\d{3})+(?!\d))/g, NBSP) + "," + casti[1] + NBSP + "Kč";
  }

  function textPole(pole) {
    if (pole.tagName === "SELECT") {
      var volba = pole.options[pole.selectedIndex];
      return volba ? volba.text : "";
    }
    return pole.value;
  }

  function nastav(seznam) {
    var dialog = document.createElement("dialog");
    if (!dialog.showModal) return; // starsi prohlizec: zustane skladani pod sebe

    cislo += 1;
    var nadpisId = "okno-nadpis-" + cislo;
    dialog.className = "okno-radku";
    dialog.setAttribute("aria-labelledby", nadpisId);
    dialog.innerHTML =
      "<article>" +
      '<div class="okno-hlavicka"><h3></h3>' +
      '<button type="button" class="outline okno-zavrit">Zavřít</button></div>' +
      '<div class="okno-telo"></div><div class="okno-paticka"></div>' +
      "</article>";
    var nadpis = dialog.querySelector("h3");
    nadpis.id = nadpisId;
    nadpis.textContent = seznam.dataset.oknoNadpis || "Úpravy";
    var telo = dialog.querySelector(".okno-telo");
    var paticka = dialog.querySelector(".okno-paticka");
    seznam.appendChild(dialog);
    seznam.classList.add("okno-aktivni");

    // Co je zrovna v okne: bunky, odkud se prvky vzaly, a tlacitko, ke kteremu se vratit.
    var otevreno = null;

    function otevri(radek, tlacitko) {
      otevreno = { tlacitko: tlacitko, vraceni: [] };
      telo.textContent = "";
      paticka.textContent = "";
      radek.querySelectorAll("[data-okno-pole], [data-okno-akce]").forEach(function (bunka) {
        // Bunka akce bez tlacitka (napr. kategorie, kterou nejde smazat) okno nezaplni prazdnou patickou.
        if (bunka.hasAttribute("data-okno-akce") && !bunka.children.length) return;
        // Prazdna bunka pole (napr. Pozvanka u uctu, ktery uz je aktivni) okno nezaplni popiskem bez obsahu.
        if (bunka.hasAttribute("data-okno-pole") && !bunka.children.length && !bunka.textContent.trim()) return;
        var obal;
        if (bunka.hasAttribute("data-okno-pole")) {
          // Popisek je skutecny <label>, takze klepnuti na nej pole aktivuje. Bunka s vic prvky
          // (tlacitka, odkazy, vlastni popisky posuvniku) je skupina: <label> v <label> by klepnuti
          // na vnitrni popisek zmatlo, proto je obalem <div>. (Bez role=group: Pico z nej dela rad tlacitek.)
          var skupina = !!bunka.querySelector("label, button, a");
          obal = document.createElement(skupina ? "div" : "label");
          var popisek = document.createElement("span");
          popisek.className = "okno-popisek";
          popisek.textContent = bunka.getAttribute("data-okno-pole");
          obal.className = "okno-pole";
          obal.appendChild(popisek);
          telo.appendChild(obal);
        } else {
          obal = document.createElement("div");
          paticka.appendChild(obal);
        }
        var prvky = [].slice.call(bunka.childNodes);
        otevreno.vraceni.push({ bunka: bunka, prvky: prvky });
        prvky.forEach(function (prvek) { obal.appendChild(prvek); });
      });
      pridejUlozit();
      if (jenCteni) {
        // Revizor si okno muze otevrit, ale nic v nem nezmeni (server by to stejne odmitl).
        dialog.querySelectorAll("input, textarea").forEach(function (pole) { pole.readOnly = true; });
        dialog.querySelectorAll("select").forEach(function (pole) { pole.disabled = true; });
      }
      dialog.showModal();
    }

    // Tlacitko Ulozit dole v okne: ulozi a okno zavre (Honza, Z3). Je jen tam, kde ma
    // formular vlastni tlacitko Ulozit (revizor a uzavrena akce ho nemaji). Formular
    // s `data-prubezne` se uklada na pozadi - okno se zavre az po doběhnutí ulození
    // (a zustane otevrene, kdyz ulozeni selze, aby se zmeny neztratily).
    function pridejUlozit() {
      var hlavni = seznam.querySelector(":scope > button[type=submit]");
      if (!hlavni || jenCteni) return;
      var tlacitko = document.createElement("button");
      tlacitko.type = "button";
      tlacitko.className = "zlute okno-ulozit";
      tlacitko.textContent = "Uložit";
      var obal = paticka.firstElementChild;
      if (!obal) {
        obal = document.createElement("div");
        paticka.insertBefore(obal, paticka.firstChild);
      }
      obal.insertBefore(tlacitko, obal.firstChild);
      tlacitko.addEventListener("click", function () {
        if (!seznam.hasAttribute("data-prubezne")) {
          // Kontrola formulare PRED zavrenim: pole jsou jeste v okne a viditelna,
          // takze prohlizec ukaze bublinu u prvniho spatneho. Po zavreni by se pole vratila
          // do skryte bunky tabulky a odeslani by tise selhalo ("not focusable").
          var spatne = [].find.call(dialog.querySelectorAll("input, select, textarea"), function (pole) {
            return !pole.checkValidity();
          });
          if (spatne) {
            spatne.reportValidity();
            return;
          }
          zavri();
          seznam.requestSubmit();
          return;
        }
        tlacitko.disabled = true;
        var detail = { sliby: [] };
        seznam.dispatchEvent(new CustomEvent("appka:uloz-ted", { detail: detail }));
        Promise.all(detail.sliby).then(function (vysledky) {
          tlacitko.disabled = false;
          if (vysledky.every(function (ok) { return ok !== false; })) zavri();
        });
      });
    }

    // Policka se vraceji do tabulky a fokus na radek, ktery okno otevrel. Udalost `close`
    // prichazi az po chvili, proto tlacitko a klepnuti mimo vraci policka hned (`zavri`);
    // Esc a zmena velikosti okna jdou pres `close`. Volani navic nic nedela.
    function vrat() {
      if (!otevreno) return;
      otevreno.vraceni.forEach(function (zaznam) {
        zaznam.prvky.forEach(function (prvek) { zaznam.bunka.appendChild(prvek); });
      });
      telo.textContent = "";
      paticka.textContent = "";
      var tlacitko = otevreno.tlacitko;
      otevreno = null;
      tlacitko.focus();
    }
    function zavri() {
      vrat();
      dialog.close();
    }
    dialog.addEventListener("close", vrat);

    dialog.querySelector(".okno-zavrit").addEventListener("click", zavri);
    // Klepnuti na ztmavene pozadi (cele <dialog> je pruhledny prekryv, panel je v nem).
    // Druhe klepnuti dvojkliku (`detail` 2) se nebere: prvni okno otevrelo a druhe by
    // dopadlo na pozadi a hned ho zase zavrelo.
    dialog.addEventListener("click", function (udalost) {
      if (udalost.target === dialog && udalost.detail < 2) zavri();
    });
    telefon.addEventListener("change", function () {
      if (!telefon.matches && dialog.open) zavri();
    });

    seznam.addEventListener("click", function (udalost) {
      var tlacitko = udalost.target.closest(".okno-otevrit");
      if (!tlacitko || !telefon.matches) return;
      var radek = tlacitko.closest("[data-okno-radek]");
      if (radek) otevri(radek, tlacitko);
    });

    // Kratky radek ukazuje to, co je prave v poli - i po uprave v okne.
    function zrcadli(udalost) {
      var pole = udalost.target;
      if (!pole.name) return;
      seznam.querySelectorAll('[data-zrcadlo="' + pole.name + '"]').forEach(function (misto) {
        var text = textPole(pole).trim();
        if (misto.dataset.format === "koruny" && text) text = koruny(text);
        misto.textContent = text || misto.dataset.prazdne || "";
      });
    }
    seznam.addEventListener("input", zrcadli);
    seznam.addEventListener("change", zrcadli);
  }

  document.querySelectorAll("[data-okno-seznam]").forEach(nastav);
})();
