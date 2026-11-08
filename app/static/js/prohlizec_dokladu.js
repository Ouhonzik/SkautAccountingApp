/* Prohlizec dokladu: na telefonu je doklad na strance zmenseny (CSS `zoom`
   v tisk.css), aby se vesel na sirku. Tlacitko "Priblizit doklad" (nebo klepnuti
   na doklad) ho otevre pres celou obrazovku v puvodni velikosti A4, kde jde
   priblizit a posouvat - bez zoomovani cele aplikace. Nacita se na strankach
   dokladu (doklady.html, cestak_tisk.html); na pocitaci nic nedela. */

(function () {
  "use strict";

  var dialog = document.querySelector("dialog.prohlizec");
  // Bez podpory <dialog> zustane doklad zmenseny jako dosud.
  if (!dialog || !dialog.showModal) return;

  var telefon = window.matchMedia("(max-width: 37.5rem)");
  var plocha = dialog.querySelector(".prohlizec-plocha");
  var papir = dialog.querySelector(".prohlizec-papir");
  var tlOddalit = dialog.querySelector(".prohlizec-oddalit");
  var tlPuvodni = dialog.querySelector(".prohlizec-puvodni");
  var tlPriblizit = dialog.querySelector(".prohlizec-priblizit");

  var NEJVETSI_ZOOM = 4;
  var KROK_ZOOMU = 1.25;
  var ZOOM_DVOJKLIKEM = 2.5;
  var DVOJKLIK_MS = 350;

  // Pohled: z = pomer k puvodni velikosti papiru (1 = A4 na sirku 794 px).
  var z = 1;
  var nejmensiZoom = 0.3;
  var otevrel = null;  // tlacitko, na ktere se po zavreni vraci fokus

  var ukazatele = {};  // id prstu/mysi -> posledni poloha {x, y}
  var predchozi = null;  // stred a vzdalenost dvou prstu v minulem kroku
  var klepnuti = null;  // {x, y, cas} pokud je zatim jen klepnuti
  var posledniKlepnuti = null;

  function doklad() {
    return papir.firstElementChild;
  }

  // Nejmensi priblizeni = celou sirku papiru vidi clovek najednou (stejne jako
  // doklad na strance); vetsi nez puvodni velikost to nikdy neni.
  function spocitejNejmensi() {
    var stylPlochy = getComputedStyle(plocha);
    var volno = plocha.clientWidth -
      parseFloat(stylPlochy.paddingLeft) - parseFloat(stylPlochy.paddingRight);
    var d = doklad();
    var puvodni = d ? d.offsetWidth : 794;
    nejmensiZoom = Math.min(1, Math.max(0.1, volno / puvodni));
  }

  function pouzij() {
    z = Math.min(NEJVETSI_ZOOM, Math.max(nejmensiZoom, z));
    // `zoom` (ne `transform`): zmeni i zabrane misto, takze se plocha doopravdy
    // da posouvat a text se pri priblizeni vykresli znovu ostre, ne jako obrazek.
    papir.style.zoom = String(z);
    tlPuvodni.textContent = Math.round(z * 100) + " %";
    tlOddalit.disabled = z <= nejmensiZoom + 0.001;
    tlPriblizit.disabled = z >= NEJVETSI_ZOOM - 0.001;
  }

  // Zmeni priblizeni tak, aby bod papiru pod prstem (bodX, bodY v souradnicich
  // okna) zustal na miste. Meri se podle skutecne poloh papiru na obrazovce, ne
  // podle zoomu, ktery by se v ruznych prohlizecich pocital ruzne.
  function zoomuj(novy, bodX, bodY) {
    var r0 = papir.getBoundingClientRect();
    var pr = plocha.getBoundingClientRect();
    if (bodX === undefined) bodX = pr.left + pr.width / 2;
    if (bodY === undefined) bodY = pr.top + pr.height / 2;
    var podilX = r0.width ? (bodX - r0.left) / r0.width : 0;
    var podilY = r0.height ? (bodY - r0.top) / r0.height : 0;
    z = novy;
    pouzij();
    var r1 = papir.getBoundingClientRect();
    plocha.scrollLeft += r1.left + podilX * r1.width - bodX;
    plocha.scrollTop += r1.top + podilY * r1.height - bodY;
  }

  function otevri(d, tlacitko) {
    otevrel = tlacitko;
    // Klon, ne nova stranka a ne innerHTML: je to tentyz hotovy DOM, jaky
    // server vykreslil (a Jinja uz osetrila), jen presunuty do prohlizece.
    papir.replaceChildren(d.cloneNode(true));
    dialog.showModal();
    spocitejNejmensi();
    // Otevira se na "celou sirku displeje" (nejmensi priblizeni), aby byl videt
    // cely doklad; dal ho priblizuje clovek sam (jako lupu u uctenek).
    z = nejmensiZoom;
    pouzij();
    plocha.scrollLeft = 0;
    plocha.scrollTop = 0;
    plocha.focus({ preventScroll: true });
  }

  function stredADalka() {
    var id = Object.keys(ukazatele);
    var a = ukazatele[id[0]];
    var b = ukazatele[id[1]];
    return {
      x: (a.x + b.x) / 2,
      y: (a.y + b.y) / 2,
      dalka: Math.hypot(a.x - b.x, a.y - b.y) || 1,
    };
  }

  function stisk(u) {
    if (u.button > 0) return;
    ukazatele[u.pointerId] = { x: u.clientX, y: u.clientY };
    var pocet = Object.keys(ukazatele).length;
    klepnuti = pocet === 1 ? { x: u.clientX, y: u.clientY, cas: Date.now() } : null;
    predchozi = pocet === 2 ? stredADalka() : null;
    try { plocha.setPointerCapture(u.pointerId); } catch (chyba) { /* bez zachyceni to jde taky */ }
  }

  function tah(u) {
    var ukazatel = ukazatele[u.pointerId];
    if (!ukazatel) return;
    var pocet = Object.keys(ukazatele).length;
    if (pocet === 1) {
      // Posouvani vlastnim kodem (v CSS je `touch-action: none`): prohlizec by
      // jinak dvouprstove gesto bral jako zoom nebo posun CELE stranky.
      plocha.scrollLeft -= u.clientX - ukazatel.x;
      plocha.scrollTop -= u.clientY - ukazatel.y;
      ukazatel.x = u.clientX;
      ukazatel.y = u.clientY;
    } else if (pocet === 2) {
      ukazatel.x = u.clientX;
      ukazatel.y = u.clientY;
      var ted = stredADalka();
      if (predchozi) {
        zoomuj(z * ted.dalka / predchozi.dalka, ted.x, ted.y);
        plocha.scrollLeft -= ted.x - predchozi.x;
        plocha.scrollTop -= ted.y - predchozi.y;
      }
      predchozi = ted;
    }
    if (klepnuti && Math.hypot(u.clientX - klepnuti.x, u.clientY - klepnuti.y) > 10) {
      klepnuti = null;
    }
  }

  function pust(u) {
    var bylKlepnuti = u.type === "pointerup" && klepnuti && Object.keys(ukazatele).length === 1;
    delete ukazatele[u.pointerId];
    predchozi = Object.keys(ukazatele).length === 2 ? stredADalka() : null;
    if (!Object.keys(ukazatele).length) klepnuti = null;
    if (!bylKlepnuti) return;
    // Dvojite klepnuti si hlidam sam: `dblclick` na dotykovych displejich
    // nechodi spolehlive (hlavne v Safari).
    var ted = Date.now();
    if (posledniKlepnuti && ted - posledniKlepnuti.cas < DVOJKLIK_MS &&
        Math.hypot(u.clientX - posledniKlepnuti.x, u.clientY - posledniKlepnuti.y) < 30) {
      posledniKlepnuti = null;
      // Celá šířka displeje <-> 250 %.
      if (z < ZOOM_DVOJKLIKEM * 0.6) zoomuj(ZOOM_DVOJKLIKEM, u.clientX, u.clientY);
      else zoomuj(nejmensiZoom, u.clientX, u.clientY);
    } else {
      posledniKlepnuti = { x: u.clientX, y: u.clientY, cas: ted };
    }
  }

  function kolecko(u) {
    // Ctrl+kolecko (a stisknuti dvou prstu na touchpadu) by jinak zoomovalo
    // celou stranku pod prohlizecem.
    u.preventDefault();
    if (!u.ctrlKey) {
      plocha.scrollLeft += u.deltaX;
      plocha.scrollTop += u.deltaY;
      return;
    }
    var posun = Math.max(-50, Math.min(50, u.deltaY));
    zoomuj(z * Math.exp(-posun * 0.01), u.clientX, u.clientY);
  }

  plocha.addEventListener("pointerdown", stisk);
  plocha.addEventListener("pointermove", tah);
  plocha.addEventListener("pointerup", pust);
  plocha.addEventListener("pointercancel", pust);
  dialog.addEventListener("wheel", kolecko, { passive: false });
  // Starsi Safari na iPhonu posila pro stisknuti dvou prstu vlastni udalost.
  ["gesturestart", "gesturechange"].forEach(function (nazev) {
    dialog.addEventListener(nazev, function (u) { u.preventDefault(); });
  });

  tlPriblizit.addEventListener("click", function () { zoomuj(z * KROK_ZOOMU); });
  tlOddalit.addEventListener("click", function () { zoomuj(z / KROK_ZOOMU); });
  tlPuvodni.addEventListener("click", function () { zoomuj(1); });
  dialog.querySelector(".prohlizec-zavrit").addEventListener("click", function () {
    dialog.close();
  });

  dialog.addEventListener("keydown", function (u) {
    if (u.key === "+" || u.key === "=") { u.preventDefault(); zoomuj(z * KROK_ZOOMU); }
    else if (u.key === "-" || u.key === "_") { u.preventDefault(); zoomuj(z / KROK_ZOOMU); }
    else if (u.key === "0") { u.preventDefault(); zoomuj(1); }
  });

  dialog.addEventListener("close", function () {
    papir.replaceChildren();
    ukazatele = {};
    predchozi = null;
    klepnuti = null;
    posledniKlepnuti = null;
    if (otevrel) otevrel.focus({ preventScroll: true });
  });

  window.addEventListener("resize", function () {
    if (!dialog.open) return;
    spocitejNejmensi();
    pouzij();
  });
  // Po otoceni na siroky displej (nebo zvetseni okna) prohlizec nedava smysl.
  telefon.addEventListener("change", function () {
    if (!telefon.matches && dialog.open) dialog.close();
  });

  // ---- Tlacitka u dokladu ----
  // Vytvari je skript, ne sablona: tri ruzne stranky maji jeden doklad vedle
  // druheho a tlacitko patri vzdy k nemu. Na pocitaci je ovladani schovane CSS.
  [].forEach.call(document.querySelectorAll(".doklady-plocha > article.doklad"), function (d) {
    var nazev = d.querySelector(".doklad-nazev, .titulni-nadpis");
    var obal = document.createElement("p");
    obal.className = "tlacitka netisknout prohlizec-ovladani";
    var tlacitko = document.createElement("button");
    tlacitko.type = "button";
    tlacitko.className = "outline drobne prohlizec-otevrit";
    tlacitko.textContent = "Přiblížit doklad";
    tlacitko.setAttribute("aria-label",
      "Přiblížit doklad na celou obrazovku" + (nazev ? ": " + nazev.textContent.trim() : ""));
    tlacitko.addEventListener("click", function () { otevri(d, tlacitko); });
    obal.appendChild(tlacitko);
    d.parentNode.insertBefore(obal, d);

    // Klepnuti na samotny doklad ho otevre taky (jen na telefonu).
    d.addEventListener("click", function (u) {
      if (!telefon.matches) return;
      if (u.target.closest("a, button")) return;
      otevri(d, tlacitko);
    });
  });
})();
