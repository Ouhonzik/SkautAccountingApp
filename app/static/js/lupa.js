/* Lupa: kontrola uctenky pres celou obrazovku - vlevo sken, vpravo udaje,
   sipky mezi uctenkami, Enter oznaci "Zkontrolovano". Nacita se jen na strance
   uctenek (skeny.html). */

(function () {
  "use strict";

  // ---- 5. Lupa: kontrola uctenky pres celou obrazovku ----
  // Vlevo sken, vpravo udaje, ktere z nej precetla AI. Policka v panelu jsou
  // jen ovladatelne kopie policek z tabulky - ta zustava zdrojem pravdy a
  // sama se prubezne uklada.
  //
  // Lupa se sklada z funkci, ktere si vsechno berou z parametru:
  //   p     = prvky dialogu (najdiPrvkyLupy),
  //   s     = stav lupy {kde, strana, nahled}, ktery sdileji vsechny obsluhy,
  //   radky = radky tabulky uctenek.

  // Znamka souboru (`v`) drzi adresu nahledu jedinecnou. Bez ni by prohlizec
  // po smazani skenu ukazal pod touz adresou obrazek ulozeny v pameti - papir
  // uctenky, ktera uz neexistuje.
  function adresaNahledu(radek, cislo) {
    return "/sken/" + radek.dataset.sken + "/nahled?velky=1&v=" +
      encodeURIComponent(radek.dataset.otisk || "") + "&strana_c=" + cislo;
  }

  // Zmena policka v tabulce musi bublat - posloucha ji formular s prubeznym
  // ukladanim a teprve on ji posle na server.
  function ohlas(policko) {
    policko.dispatchEvent(new Event("input", { bubbles: true }));
    policko.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function najdiPrvkyLupy(dialog) {
    return {
      dialog: dialog,
      obrazek: dialog.querySelector(".lupa-obrazek"),
      nazev: dialog.querySelector(".lupa-nazev"),
      pozice: dialog.querySelector(".lupa-pozice"),
      pole: dialog.querySelector(".lupa-pole"),
      poznamky: dialog.querySelector(".lupa-poznamky"),
      original: dialog.querySelector(".lupa-original"),
      prepinacOk: dialog.querySelector(".lupa-ok"),
      prepinacPriloha: dialog.querySelector(".lupa-priloha"),
      obalStranek: dialog.querySelector(".lupa-stranky"),
      popisStranky: dialog.querySelector(".lupa-strana-popis"),
      obsah: dialog.querySelector(".lupa-obsah"),
      plochaSkenu: dialog.querySelector(".lupa-sken"),
      stav: dialog.querySelector(".lupa-stav"),
      priblizit: dialog.querySelector(".lupa-priblizit"),
      oddalit: dialog.querySelector(".lupa-oddalit"),
      puvodni: dialog.querySelector(".lupa-puvodni"),
    };
  }

  // ---- Priblizeni skenu ----
  // Lupa ukazovala uctenku vzdy "na celou plochu" a zadne vlastni priblizeni
  // nemela - kolecko s Ctrl, stisknuti dvou prstu ani Ctrl +/- proto nezoomovaly
  // sken, ale CELOU STRANKU (prohlizec). Ted si priblizeni resi sama lupa:
  // sken se meni jen pres `transform` uvnitr plochy s `overflow: hidden`, a
  // udalosti, kterymi by jinak zoomoval prohlizec, se tady zastavuji
  // (`preventDefault` u kolecka, `touch-action: none` v CSS).
  var NEJMENSI_ZOOM = 1;
  var NEJVETSI_ZOOM = 8;
  var KROK_ZOOMU = 1.25;
  var ZOOM_DVOJKLIKEM = 2.5;
  // Kdy se naposledy prepla "cela obrazovka": klik, ktery po klepnuti
  // dorazi AZ po zmene rozlozeni, muze dopadnout na okraj dialogu a lupu zavrit.
  var prepnutoVcas = 0;

  function slozPohled(p) {
    var v = { z: 1, x: 0, y: 0 };
    var ukazatele = {};  // id prstu/mysi -> poslední poloha {x, y}
    var predchozi = null;  // stred a vzdalenost dvou prstu v minulem kroku
    // Klepnuti na telefonu: sken ma tam jen tretinu obrazovky, klepnutim
    // se roztahne na celou (a dalsim zase vrati). Klepnuti = jeden prst, skoro
    // se nepohnul a sken neni priblizeny (to je tazeni/zoom, ne klepnuti).
    var klepnuti = null;
    var telefon = window.matchMedia("(max-width: 37.5rem)");

    function stredPlochy() {
      var r = p.plochaSkenu.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    }

    // Obrazek lezi ve stredu plochy; posunout ho lze nejvys tak, aby jeho okraj
    // dosel k okraji plochy. Mensi nez plocha se vzdy drzi uprostred.
    function pouzij() {
      v.z = Math.min(NEJVETSI_ZOOM, Math.max(NEJMENSI_ZOOM, v.z));
      var mezX = Math.max(0, (p.obrazek.offsetWidth * v.z - p.plochaSkenu.clientWidth) / 2);
      var mezY = Math.max(0, (p.obrazek.offsetHeight * v.z - p.plochaSkenu.clientHeight) / 2);
      v.x = Math.min(mezX, Math.max(-mezX, v.x));
      v.y = Math.min(mezY, Math.max(-mezY, v.y));
      var priblizeno = v.z > 1.001;
      p.obrazek.style.transform = priblizeno
        ? "translate(" + v.x + "px, " + v.y + "px) scale(" + v.z + ")"
        : "";
      p.plochaSkenu.classList.toggle("priblizeno", priblizeno);
      p.puvodni.textContent = Math.round(v.z * 100) + " %";
      p.oddalit.disabled = v.z <= NEJMENSI_ZOOM + 0.001;
      p.priblizit.disabled = v.z >= NEJVETSI_ZOOM - 0.001;
    }

    // Zmeni zoom tak, aby bod pod kurzorem (bodX, bodY v souradnicich okna)
    // zustal na miste. Bez bodu se zoomuje kolem stredu plochy.
    function zoomuj(novy, bodX, bodY) {
      var stred = stredPlochy();
      var dx = (bodX === undefined ? stred.x : bodX) - stred.x;
      var dy = (bodY === undefined ? stred.y : bodY) - stred.y;
      novy = Math.min(NEJVETSI_ZOOM, Math.max(NEJMENSI_ZOOM, novy));
      var pomer = novy / v.z;
      v.x = dx - pomer * (dx - v.x);
      v.y = dy - pomer * (dy - v.y);
      v.z = novy;
      pouzij();
    }

    function obnov() {
      v.z = 1;
      v.x = 0;
      v.y = 0;
      pouzij();
    }

    // Kolecko i "stisknuti" dvou prstu na touchpadu (to prohlizec posila jako
    // kolecko s Ctrl). Ctrl+kolecko mimo sken (v pravem panelu) se bere take -
    // jinak by tam prohlizec zoomoval celou stranku pod lupou.
    function kolecko(u) {
      var nadSkenem = p.plochaSkenu.contains(u.target);
      if (!nadSkenem && !u.ctrlKey) return;  // panel se bez Ctrl normalne roluje
      u.preventDefault();
      var posun = u.deltaY * (u.deltaMode === 1 ? 33 : u.deltaMode === 2 ? 400 : 1);
      if (u.ctrlKey) posun = Math.max(-50, Math.min(50, posun));
      var faktor = Math.exp(-posun * (u.ctrlKey ? 0.01 : 0.002));
      if (nadSkenem) zoomuj(v.z * faktor, u.clientX, u.clientY);
      else zoomuj(v.z * faktor);
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
      if (u.button > 0 || u.target.closest("button")) return;
      ukazatele[u.pointerId] = { x: u.clientX, y: u.clientY };
      if (Object.keys(ukazatele).length === 1) klepnuti = { x: u.clientX, y: u.clientY };
      else klepnuti = null;
      predchozi = Object.keys(ukazatele).length === 2 ? stredADalka() : null;
      try { p.plochaSkenu.setPointerCapture(u.pointerId); } catch (chyba) { /* bez zachyceni to jde taky */ }
      p.plochaSkenu.classList.add("tahne-se");
    }

    function tah(u) {
      var ukazatel = ukazatele[u.pointerId];
      if (!ukazatel) return;
      var pocet = Object.keys(ukazatele).length;
      if (pocet === 1) {
        v.x += u.clientX - ukazatel.x;
        v.y += u.clientY - ukazatel.y;
        ukazatel.x = u.clientX;
        ukazatel.y = u.clientY;
        pouzij();
      } else if (pocet === 2) {
        ukazatel.x = u.clientX;
        ukazatel.y = u.clientY;
        var ted = stredADalka();
        if (predchozi) {
          zoomuj(v.z * ted.dalka / predchozi.dalka, ted.x, ted.y);
          v.x += ted.x - predchozi.x;
          v.y += ted.y - predchozi.y;
          pouzij();
        }
        predchozi = ted;
      }
    }

    function pust(u) {
      var bylKlepnuti = u.type === "pointerup" && klepnuti && Object.keys(ukazatele).length === 1 &&
        Math.hypot(u.clientX - klepnuti.x, u.clientY - klepnuti.y) < 10 && v.z <= 1.001;
      delete ukazatele[u.pointerId];
      predchozi = Object.keys(ukazatele).length === 2 ? stredADalka() : null;
      if (!Object.keys(ukazatele).length) {
        p.plochaSkenu.classList.remove("tahne-se");
        klepnuti = null;
      }
      if (bylKlepnuti && telefon.matches && !(u.target.closest && u.target.closest("button"))) {
        p.dialog.classList.toggle("lupa-cela");
        prepnutoVcas = Date.now();
        pouzij();
      }
    }

    function dvojklik(u) {
      if (u.target.closest("button")) return;
      if (v.z > 1.001) obnov();
      else zoomuj(ZOOM_DVOJKLIKEM, u.clientX, u.clientY);
    }

    p.plochaSkenu.addEventListener("pointerdown", stisk);
    p.plochaSkenu.addEventListener("pointermove", tah);
    p.plochaSkenu.addEventListener("pointerup", pust);
    p.plochaSkenu.addEventListener("pointercancel", pust);
    p.plochaSkenu.addEventListener("dblclick", dvojklik);
    // `passive: false` je nutne - jinak by `preventDefault` u kolecka nezabralo
    // a prohlizec by (hlavne u Ctrl+kolecka) zoomoval celou stranku.
    p.dialog.addEventListener("wheel", kolecko, { passive: false });
    window.addEventListener("resize", pouzij);

    p.priblizit.addEventListener("click", function () { zoomuj(v.z * KROK_ZOOMU); vratFokus(p); });
    p.oddalit.addEventListener("click", function () { zoomuj(v.z / KROK_ZOOMU); vratFokus(p); });
    p.puvodni.addEventListener("click", function () { obnov(); vratFokus(p); });
    // Tazeni obrazku mysi je posouvani skenu, ne prenaseni souboru.
    p.obrazek.addEventListener("dragstart", function (u) { u.preventDefault(); });

    obnov();
    return {
      obnov: obnov,
      pouzij: pouzij,
      priblizit: function () { zoomuj(v.z * KROK_ZOOMU); },
      oddalit: function () { zoomuj(v.z / KROK_ZOOMU); },
    };
  }

  // Po kliknuti mysi zustane fokus na tlacitku a Enter by pak jen znovu
  // zmackl jeho - proto se vraci na dialog, kde ho prebira obsluha klaves.
  function vratFokus(p) {
    p.dialog.focus();
  }

  // Co je v lupe opravdu videt: "nacita" = prohlizec ma jeste starou
  // uctenku a panel uz ukazuje udaje nove, "chyba" = nahled se nepovedl,
  // "ok" = obrazek je ten, ktery ma byt. Enter oznaci "Zkontrolovano" jen
  // v poslednim pripade - nikdo nema potvrdit uctenku, kterou nevidel.
  function nastavNahled(p, s, novy) {
    s.nahled = novy;
    p.plochaSkenu.classList.toggle("nacita-se", novy === "nacita");
    p.plochaSkenu.classList.toggle("chyba-nahledu", novy === "chyba");
    p.stav.hidden = novy === "ok";
    p.stav.textContent = novy === "nacita"
      ? "Načítám účtenku…"
      : novy === "chyba"
        ? "Náhled se nepodařilo načíst — otevři původní soubor."
        : "";
  }

  // Velky nahled server teprve vyrabi z PDF, takze prvni zobrazeni trva.
  // Jakmile je soucasna uctenka videt, stahnu si na pozadi uz i tu dalsi -
  // nez na ni clovek prepne, byva hotova. Obrazek drzim v promenne, aby ho
  // prohlizec nezahodil driv, nez se stahne.
  function napojNahled(p, s, radky) {
    var prednacitany = null;
    p.obrazek.onload = function () {
      nastavNahled(p, s, "ok");
      var dalsi = radky[(s.kde + 1) % radky.length];
      prednacitany = new Image();
      prednacitany.src = adresaNahledu(dalsi, 1);
    };
    p.obrazek.onerror = function () {
      nastavNahled(p, s, "chyba");
    };
  }

  function slozPanel(radek, pole, poznamky, popisky) {
    pole.textContent = "";
    poznamky.textContent = "";

    [].forEach.call(radek.children, function (bunka, poradi) {
      var puvodni = bunka.querySelector("input:not([type=checkbox]), select");
      // Schovane policko (vyber dokladu u neprilohy) by v panelu bylo jen
      // prazdnym radkem s popiskem.
      if (!puvodni || puvodni.hidden) return;

      var klon = puvodni.cloneNode(true);
      // Kopie nesmi mit `name` - do formulare nepatri, jen ovlada original.
      klon.removeAttribute("name");
      klon.value = puvodni.value;

      function prepis() {
        puvodni.value = klon.value;
        ohlas(puvodni);
      }
      klon.addEventListener("input", prepis);
      klon.addEventListener("change", prepis);

      var radekPole = document.createElement("label");
      radekPole.className = "lupa-pole-radek";
      var popisek = document.createElement("span");
      popisek.textContent = popisky[poradi] || "";
      radekPole.appendChild(popisek);
      radekPole.appendChild(klon);
      pole.appendChild(radekPole);
    });

    // Co k uctence napsala AI: jistota, poznamka, rozpis po strankach.
    var policko = radek.querySelector("input[name^=nazev_]");
    var bunkaNazvu = policko && policko.closest("td");
    if (!bunkaNazvu) return;
    [].forEach.call(bunkaNazvu.querySelectorAll(":scope > small, :scope > details"),
      function (prvek) {
        poznamky.appendChild(prvek.cloneNode(true));
      });
  }

  // `po` se zavola po prepnuti; `fokus` vraci fokus na dialog.
  function nastavPrepinac(tlacitko, policko, zapnuto, vypnuto, po, fokus) {
    if (!policko) return;
    tlacitko.textContent = policko.checked ? zapnuto : vypnuto;
    tlacitko.setAttribute("aria-pressed", policko.checked ? "true" : "false");
    // Policko zamcene sablonou (uzavrena akce, K-167) se neda prepnout ani odtud.
    tlacitko.disabled = policko.disabled;
    // Prirazeni, ne addEventListener: pri kazdem otevreni lupy by se jinak
    // posluchace vrstvily na sebe a jedno kliknuti by prepinalo vickrat.
    tlacitko.onclick = function () {
      policko.checked = !policko.checked;
      ohlas(policko);
      nastavPrepinac(tlacitko, policko, zapnuto, vypnuto, po, fokus);
      if (po) po();
      fokus();
    };
  }

  function ukazStranku(p, s, radky) {
    var radek = radky[s.kde];
    var stranek = parseInt(radek.dataset.stranky, 10) || 1;
    if (s.strana < 1) s.strana = stranek;
    if (s.strana > stranek) s.strana = 1;
    // Nova uctenka nebo stranka se ukaze cela, ne priblizena po predchozi.
    s.pohled.obnov();
    nastavNahled(p, s, "nacita");
    p.obrazek.src = adresaNahledu(radek, s.strana);
    // Obrazek uz ulozeny v pameti prohlizece je hotovy hned (a udalost `load`
    // prijde az pozdeji) - at lupa nezustane pri "Nacitam" zbytecne.
    if (p.obrazek.complete && p.obrazek.naturalWidth > 0) nastavNahled(p, s, "ok");
    p.obalStranek.hidden = stranek < 2;
    p.popisStranky.textContent = "str. " + s.strana + " / " + stranek;
  }

  function ukaz(p, s, radky, popisky, cislo) {
    // Za posledni uctenkou se vraci prvni a naopak - kontrola se tak da
    // projet dokola bez trefovani se do konce seznamu.
    s.kde = (cislo + radky.length) % radky.length;
    s.strana = 1;
    var radek = radky[s.kde];
    var fokus = function () { vratFokus(p); };
    slozPanel(radek, p.pole, p.poznamky, popisky);
    // Panel odrolovany od predchozi uctenky by u dalsi zacinal nekde
    // uprostred - clovek by nevidel Nazev a myslel by si, ze chybi.
    p.obsah.scrollTop = 0;
    p.nazev.textContent = radek.dataset.soubor || "";
    p.pozice.textContent = "Účtenka " + (s.kde + 1) + " z " + radky.length;
    p.original.href = "/sken/" + radek.dataset.sken + "/soubor";
    nastavPrepinac(p.prepinacPriloha, radek.querySelector("input[name^=priloha_]"),
      "Příloha", "Označit jako přílohu",
      // Zaskrtnuta priloha odkryje v tabulce vyber dokladu, ke kteremu patri.
      // Panel se proto sestavi znovu, aby ten vyber byl i tady v lupe.
      function () { slozPanel(radek, p.pole, p.poznamky, popisky); }, fokus);
    nastavPrepinac(p.prepinacOk, radek.querySelector("input[name^=ok_]"),
      "Zkontrolováno", "Označit jako zkontrolované", null, fokus);
    ukazStranku(p, s, radky);
  }

  // Tlacitka v dialogu: predchozi/dalsi uctenka, stranky PDF, zavrit.
  // `ovladani` = { ukaz(cislo), ukazStranku(), s }.
  function napojTlacitkaLupy(p, ovladani) {
    var s = ovladani.s;
    p.dialog.querySelector(".lupa-drive").addEventListener("click", function () {
      ovladani.ukaz(s.kde - 1);
      vratFokus(p);
    });
    p.dialog.querySelector(".lupa-pozdeji").addEventListener("click", function () {
      ovladani.ukaz(s.kde + 1);
      vratFokus(p);
    });
    p.dialog.querySelector(".lupa-strana-zpet").addEventListener("click", function () {
      s.strana -= 1;
      ovladani.ukazStranku();
      vratFokus(p);
    });
    p.dialog.querySelector(".lupa-strana-vpred").addEventListener("click", function () {
      s.strana += 1;
      ovladani.ukazStranku();
      vratFokus(p);
    });
    [".lupa-zavrit", ".lupa-zavrit-cela"].forEach(function (selektor) {
      p.dialog.querySelector(selektor).addEventListener("click", function () {
        p.dialog.close();
      });
    });
  }

  // Klavesy: sipky prochazeji uctenky, Enter oznaci "Zkontrolovano" a rovnou
  // skoci na dalsi - cela akce se tak da projet bez sahnuti na mys.
  // Kdyz clovek pise do policka, klavesy patri kurzoru, ne lupe.
  function obsluzKlavesyLupy(u, s, radky, ukazCislo) {
    if (u.target && /^(INPUT|SELECT|TEXTAREA)$/.test(u.target.tagName)) return;

    // + / - / 0 pribliziji sken (i s Ctrl, aby prohlizec nezoomoval stranku).
    if (u.key === "+" || u.key === "=") {
      u.preventDefault();
      s.pohled.priblizit();
      return;
    }
    if (u.key === "-" || u.key === "_") {
      u.preventDefault();
      s.pohled.oddalit();
      return;
    }
    if (u.key === "0") {
      u.preventDefault();
      s.pohled.obnov();
      return;
    }

    if (u.key === "ArrowLeft" || u.key === "ArrowRight") {
      u.preventDefault();
      ukazCislo(u.key === "ArrowLeft" ? s.kde - 1 : s.kde + 1);
      return;
    }

    // Enter zmacknuty na tlacitku necham tlacitku - jinak by se klavesnici
    // nedala lupa zavrit ani prepnout priloha.
    if (u.key !== "Enter" || /^(BUTTON|A)$/.test(u.target.tagName)) return;
    u.preventDefault();
    // Dokud neni videt obrazek te uctenky, Enter nic nepotvrdi ani neprepne.
    if (s.nahled !== "ok") return;
    // Zamerne jen zaskrtava, neprepina. Kdyby Enter odskrtaval, stacilo by
    // zmacknout ho dvakrat a uctenka by zustala neoverena - coz uz bys na
    // dalsi uctence nevidel. Odskrtnout jde tlacitkem v paticce.
    var policko = radky[s.kde].querySelector("input[name^=ok_]");
    if (policko && !policko.checked && !policko.disabled) {
      policko.checked = true;
      ohlas(policko);
    }
    ukazCislo(s.kde + 1);
  }

  function nastavLupu() {
    var dialog = document.querySelector("dialog.lupa");
    var tabulka = document.querySelector("table.husta");
    // Bez podpory <dialog> se nic nedeje a odkaz u nahledu otevre puvodni
    // soubor v nove zalozce, jak to delal driv.
    if (!dialog || !tabulka || !dialog.showModal) return;

    var radky = [].slice.call(tabulka.querySelectorAll("tbody tr[data-sken]"));
    if (!radky.length) return;

    // Popisky poli beru z hlavicky tabulky, at se panel nerozejde se sloupci.
    // Zavorka za nazvem ("Nazev (jde do Excelu...)") by v uzkem panelu jen
    // prekazela, tak ji ustrihnu.
    var popisky = [].map.call(tabulka.querySelectorAll("thead th"), function (bunka) {
      return bunka.textContent.trim().replace(/\s*\(.*$/, "");
    });

    var p = najdiPrvkyLupy(dialog);
    // kolikata uctenka a stranka je videt, jak je sken priblizeny
    var s = { kde: 0, strana: 1, nahled: "ok", pohled: slozPohled(p) };
    var ukazCislo = function (cislo) { ukaz(p, s, radky, popisky, cislo); };

    napojNahled(p, s, radky);

    radky.forEach(function (radek, poradi) {
      var odkaz = radek.querySelector("[data-lupa]");
      if (!odkaz) return;
      odkaz.addEventListener("click", function (u) {
        u.preventDefault();
        dialog.classList.remove("lupa-cela");
        ukazCislo(poradi);
        dialog.showModal();
        vratFokus(p);
      });
    });

    napojTlacitkaLupy(p, {
      s: s,
      ukaz: ukazCislo,
      ukazStranku: function () { ukazStranku(p, s, radky); },
    });

    dialog.addEventListener("keydown", function (u) {
      obsluzKlavesyLupy(u, s, radky, ukazCislo);
    });

    // Kliknuti mimo panel (na ztmavene pozadi) lupu zavre.
    dialog.addEventListener("click", function (u) {
      if (u.target === dialog && Date.now() - prepnutoVcas > 500) dialog.close();
    });
  }

  nastavLupu();
})();
