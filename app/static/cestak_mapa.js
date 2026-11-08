/* Mapa v cestovnim prikazu.

   Mapa jizdu sama nepridava. Vyplnuje pole radku "Pridat jizdu" pod mapou
   (formular #jizda-rucne: Z, Do, Km a skryte souradnice bodu), datum doplni
   clovek a jizdu ulozi tlacitko toho radku - tentyz radek jde pouzit i bez
   mapy. Policka Z a Do jsou zaroven hledani s naseptavacem; klik do mapy
   nastavi bod, ktery je prave zvoleny (tlacitka v panelu u mapy, nebo policko,
   do ktereho se kliklo). Po spocitani trasy se do pole Km doplni kilometry;
   dalsi klik prepise jen to, co mapa pocita (Z, Do, Km) - datum a prostredek
   zustavaji. Km muze clovek rucne prepsat, prepise se az s dalsi trasou.

   Trasu i kilometry pocita server (/mapa/trasa), klic k Mapy.com prohlizec
   nikdy nevidi. Souradnice bodu se ukladaji jen proto, aby se jizda dala
   nakreslit v mape; kdyz clovek text v Z/Do prepise bez vyberu mista, pri
   odeslani se souradnice zahodi (bod uz by k textu nepatril).

   Vsechno z Mapy.com jde pres nase adresy /mapa/... (app/routes/mapa.py).

   Vyrez mapy (stred a zoom) se pamatuje v sessionStorage, at znovunacteni
   stranky po pridani jizdy mapu neodveze jinam. Je to schvalne jen pro tenhle
   prohlizec a tuto zalozku - nic se neposila na server. */

(function () {
  "use strict";

  var plocha = document.getElementById("mapa-cesty");
  if (!plocha || typeof L === "undefined") return;

  var BARVA_TRASY = "#2A4983";     // --kormorani-blue
  var BARVA_ODKUD = "#2A4983";
  var BARVA_KAM = "#FAB202";       // --kormorani-yellow
  var ZPOZDENI_HLEDANI_MS = 350;   // kratsi = vic dotazu (kreditu) pri psani

  var cestakId = plocha.dataset.cestak;
  var klicVyrezu = "mapa-cestak-" + cestakId;

  var vychoziStred = [49.1951, 16.6068]; // Brno - domovsky pristav
  var vychoziZoom = 12;
  // Musi sedet s MIN_DELKA_DOTAZU v app/routes/mapa.py (kratsi dotaz server zahodi).
  var minDelkaDotazu = 2;

  var mapa = L.map(plocha, { attributionControl: false });

  // keepBuffer: kolik radku dlazdic navic kolem vyrezu drzet nactenych -
  // pri posouvani mapy pak nevznikaji prazdna mista.
  L.tileLayer("/mapa/dlazdice/{z}/{x}/{y}", { maxZoom: 18, keepBuffer: 4 }).addTo(mapa);

  // ---- Dosavadni jizdy: tenke carkovane cary, at je videt, kudy se uz jelo ----
  // Mapa si trasu po silnici neuklada, jen oba body - proto rovna cara.

  var jizdy = [];
  try {
    jizdy = JSON.parse(plocha.dataset.jizdy || "[]");
  } catch (chyba) {
    jizdy = [];
  }
  jizdy.forEach(function (j) {
    L.polyline([[j[0], j[1]], [j[2], j[3]]], {
      color: BARVA_TRASY, weight: 2, opacity: .55, dashArray: "6 6", interactive: false,
    }).addTo(mapa);
    [[j[0], j[1]], [j[2], j[3]]].forEach(function (bod) {
      L.circleMarker(bod, {
        radius: 4, color: "#fff", weight: 1.5, fillColor: BARVA_TRASY, fillOpacity: .8,
        interactive: false,
      }).addTo(mapa);
    });
  });

  // ---- Vychozi vyrez ----

  var panel = document.getElementById("mapa-panel");
  var radek = document.getElementById("jizda-rucne");
  var ulozenyVyrez = nacti(klicVyrezu);
  var startLat = radek ? parseFloat(radek.elements.z_lat.value) : NaN;
  var startLon = radek ? parseFloat(radek.elements.z_lon.value) : NaN;
  var posledni = jizdy.length ? jizdy[jizdy.length - 1] : null;

  if (ulozenyVyrez) {
    mapa.setView([ulozenyVyrez.lat, ulozenyVyrez.lon], ulozenyVyrez.zoom);
  } else if (!isNaN(startLat) && !isNaN(startLon)) {
    mapa.setView([startLat, startLon], vychoziZoom);
  } else if (posledni) {
    mapa.setView([posledni[2], posledni[3]], vychoziZoom);
  } else {
    mapa.setView(vychoziStred, vychoziZoom);
  }

  mapa.on("moveend", function () {
    var stred = mapa.getCenter();
    uloz(klicVyrezu, { lat: stred.lat, lon: stred.lng, zoom: mapa.getZoom() });
  });

  // Revizor panel ani radek nema - mapa je jen na divani.
  if (!panel || !radek) return;

  // ---- Body Z / Do ----

  var pole = radek.elements;
  var tlacitkoZrusit = document.getElementById("mapa-zrusit");
  var textTrasy = document.getElementById("mapa-trasa");
  var poleKm = pole.km;
  var poleProstredek = pole.prostredek;
  var tlacitkaBodu = {
    z: panel.querySelector('button[data-bod="z"]'),
    do: panel.querySelector('button[data-bod="do"]'),
  };
  var bloky = {
    z: radek.querySelector('.pole-mista[data-bod="z"]'),
    do: radek.querySelector('.pole-mista[data-bod="do"]'),
  };
  var nazvy = { z: pole.odkud, do: pole.kam };

  var body = { z: null, do: null };
  var znacky = { z: null, do: null };
  var caraTrasy = null;
  var aktivni = "z";
  var poradiDotazu = 0; // odpovedi na starsi dotaz o trasu se zahodi
  // null = zadna trasa, "pocitam", "chyba", nebo cislo = km
  var stavTrasy = null;
  var kmZMapy = ""; // co jsme do pole Km naposledy zapsali (Smazat body smaze jen tohle)

  // Nazev, ktery k bodu patri (prisel z naseptavace, z mapy, nebo ho clovek
  // potvrdil volbou "Jen prejmenovat"). Kdyz se text v policku od nej lisi,
  // nekdo tam napsal jine misto a nevybral ho - souradnice bodu k nemu uz
  // nepatri a pri odeslani se zahodi (nic se nezamyka, radek jde ulozit i tak).
  var potvrzene = { z: "", do: "" };

  function nastavAktivni(ktery) {
    aktivni = ktery;
    ["z", "do"].forEach(function (k) {
      bloky[k].classList.toggle("aktivni", k === ktery);
      tlacitkaBodu[k].setAttribute("aria-pressed", k === ktery ? "true" : "false");
    });
  }

  // Km se doplnuji jen u prostredku placenych podle kilometru (auto, motocykl);
  // u vlaku a autobusu je ve stejnem poli cena jizdenky.
  function prostredekNaKm() {
    var vybrana = poleProstredek.options[poleProstredek.selectedIndex];
    return !!vybrana && vybrana.hasAttribute("data-na-km");
  }

  function nakresliZnacku(ktery) {
    if (znacky[ktery]) {
      mapa.removeLayer(znacky[ktery]);
      znacky[ktery] = null;
    }
    var bod = body[ktery];
    if (!bod) return;
    znacky[ktery] = L.circleMarker([bod.lat, bod.lon], {
      radius: 9, color: "#fff", weight: 3,
      fillColor: ktery === "z" ? BARVA_ODKUD : BARVA_KAM, fillOpacity: 1,
      interactive: false,
    }).addTo(mapa);
  }

  function zapisSouradnice(ktery) {
    var bod = body[ktery];
    pole[ktery + "_lat"].value = bod ? bod.lat : "";
    pole[ktery + "_lon"].value = bod ? bod.lon : "";
  }

  function smazTrasu() {
    poradiDotazu += 1;
    stavTrasy = null;
    if (caraTrasy) {
      mapa.removeLayer(caraTrasy);
      caraTrasy = null;
    }
  }

  function ukazText(text, druh) {
    textTrasy.textContent = text;
    textTrasy.className = "mapa-trasa" + (druh ? " " + druh : "");
  }

  function obnovStav() {
    if (!body.z) {
      ukazText("Vyhledej nebo klikni do mapy, odkud jsi jel.");
    } else if (!body.do) {
      ukazText("Vyhledej nebo klikni do mapy, kam jsi jel.");
    } else if (stavTrasy === "pocitam") {
      ukazText("Počítám trasu…");
    } else if (stavTrasy === "chyba") {
      ukazText("Trasu se nepodařilo spočítat — zkus jiný bod, nebo kilometry doplň ručně.", "chyba");
    } else if (typeof stavTrasy === "number") {
      // Server posila km na jedno desetinne misto (12.4); cesky se pise carka.
      var km = String(stavTrasy).replace(".", ",");
      ukazText(
        prostredekNaKm()
          ? "Trasa: " + km + " km — doplněno do řádku pod mapou."
          : "Trasa: " + km + " km. U tohoto prostředku se píše cena jízdenky, kilometry se nedoplňují.",
        "hotovo"
      );
    }
  }

  // Nastavi bod Z, nebo Do. Nazev z naseptavace prijde rovnou, po kliku
  // do mapy se dohleda.
  function nastavBod(ktery, lat, lon, nazev) {
    body[ktery] = { lat: lat, lon: lon };
    zapisSouradnice(ktery);
    nakresliZnacku(ktery);

    var policko = nazvy[ktery];
    var puvodniPlaceholder = policko.dataset.placeholder || policko.placeholder;
    policko.dataset.placeholder = puvodniPlaceholder;
    if (nazev) {
      policko.value = nazev;
      potvrzene[ktery] = nazev.trim();
    } else {
      policko.value = "";
      potvrzene[ktery] = "";
      policko.placeholder = "Hledám název…";
      var pro = body[ktery];
      ziskej("/mapa/misto?lat=" + lat + "&lon=" + lon)
        .then(function (data) {
          // Mezitim mohl prijit jiny klik - ten ma prednost. A kdo uz do
          // policka zacal psat, tomu se text neprepisuje.
          if (body[ktery] === pro && !policko.value) {
            policko.value = data.nazev;
            potvrzene[ktery] = data.nazev.trim();
          }
        })
        .catch(function () { /* nazev si clovek doplni sam */ })
        .then(function () { policko.placeholder = puvodniPlaceholder; });
    }

    // Po Z se rovnou pokracuje na Do. Do zustava aktivni i po nastaveni -
    // dalsi klik cil jen opravi.
    if (ktery === "z" && !body.do) nastavAktivni("do");

    spocitejTrasu();
  }

  function spocitejTrasu() {
    smazTrasu();
    if (!body.z || !body.do) {
      obnovStav();
      return;
    }
    var moje = poradiDotazu;
    stavTrasy = "pocitam";
    obnovStav();
    ziskej(
      "/mapa/trasa?z_lat=" + body.z.lat + "&z_lon=" + body.z.lon +
      "&do_lat=" + body.do.lat + "&do_lon=" + body.do.lon
    )
      .then(function (data) {
        if (moje !== poradiDotazu) return;
        caraTrasy = L.polyline(data.body, {
          color: BARVA_TRASY, weight: 5, opacity: .85, interactive: false,
        }).addTo(mapa);
        var hranice = caraTrasy.getBounds();
        if (hranice.isValid() && !mapa.getBounds().contains(hranice)) {
          mapa.fitBounds(hranice, { padding: [40, 40] });
        }
        stavTrasy = data.km;
        if (prostredekNaKm()) {
          kmZMapy = String(data.km).replace(".", ",");
          poleKm.value = kmZMapy;
        }
        obnovStav();
      })
      .catch(function () {
        if (moje !== poradiDotazu) return;
        stavTrasy = "chyba";
        obnovStav();
      });
  }

  mapa.on("click", function (udalost) {
    nastavBod(aktivni, udalost.latlng.lat, udalost.latlng.lng, null);
  });

  // Po zmene prostredku se jen upravi text pod mapou; hodnota v poli Km
  // zustava, jak ji clovek nechal (u vlaku v ni je cena, ne kilometry).
  poleProstredek.addEventListener("change", obnovStav);

  // Volba bodu: tlacitka v panelu, nebo kliknuti do policka Z / Do v radku.
  ["z", "do"].forEach(function (ktery) {
    tlacitkaBodu[ktery].addEventListener("click", function () { nastavAktivni(ktery); });
    bloky[ktery].addEventListener("focusin", function () { nastavAktivni(ktery); });
  });

  // Smaze body z mapy (a trasu). Z policek radku zmizi jen to, co tam dala mapa -
  // nazev a kilometry, ktere clovek mezitim prepsal, zustavaji.
  tlacitkoZrusit.addEventListener("click", function () {
    ["z", "do"].forEach(function (ktery) {
      if (nazvy[ktery].value.trim() === potvrzene[ktery]) nazvy[ktery].value = "";
      body[ktery] = null;
      potvrzene[ktery] = "";
      zapisSouradnice(ktery);
      nakresliZnacku(ktery);
    });
    if (kmZMapy && poleKm.value === kmZMapy) poleKm.value = "";
    kmZMapy = "";
    smazTrasu();
    nastavAktivni("z");
    obnovStav();
  });

  // Souradnice patri jen k textu, ktery k nim opravdu patri (viz `potvrzene`).
  // Jizda se kresli v mape jen s obema body, takze nesedi-li jeden, zahodi se oba.
  radek.addEventListener("submit", function () {
    var nesedi = ["z", "do"].some(function (ktery) {
      return body[ktery] && nazvy[ktery].value.trim() !== potvrzene[ktery];
    });
    if (!nesedi && body.z && body.do) return;
    ["z", "do"].forEach(function (ktery) {
      pole[ktery + "_lat"].value = "";
      pole[ktery + "_lon"].value = "";
    });
  });

  // Pocatecni stav: Z z konce posledni jizdy (vyplnila ho sablona).
  if (!isNaN(startLat) && !isNaN(startLon)) {
    body.z = { lat: startLat, lon: startLon };
    potvrzene.z = nazvy.z.value.trim();
    nakresliZnacku("z");
    nastavAktivni("do");
  } else {
    nastavAktivni("z");
  }
  obnovStav();

  // ---- Hledani primo v polickach Z a Do ----
  // Kazde policko ma vlastni naseptavac. Vyber z nej nastavi prave ten bod
  // (ne "aktivni" - je to jedno a totez, fokus je v tom policku). Posledni
  // polozka "Jen prejmenovat" necha bod, kde je, a napsany text vezme jako
  // novy nazev mista ("Klubovna" misto adresy) - ulozi se, jak prisel.

  function napojHledani(ktery) {
    var policko = nazvy[ktery];
    vytvorNaseptavac(policko, document.getElementById("mapa-naseptavac-" + ktery), {
      pozmeneno: obnovStav,
      hledej: function (dotaz) {
        var stred = mapa.getCenter();
        return ziskej(
          "/mapa/hledat?q=" + encodeURIComponent(dotaz) +
          "&lat=" + stred.lat + "&lon=" + stred.lng
        );
      },
      // Prejmenovat jde jen bod, ktery uz existuje.
      muzePrejmenovat: function () { return !!body[ktery]; },
      vyber: function (v) {
        mapa.setView([v.lat, v.lon], Math.max(mapa.getZoom(), 15));
        nastavBod(ktery, v.lat, v.lon, v.nazev);
        // Po vyberu Odkud se rovnou pise Kam, dokud jeste chybi.
        if (ktery === "z" && !body.do) nazvy.do.focus();
      },
      prejmenovat: function () {
        policko.value = policko.value.trim();
        potvrzene[ktery] = policko.value;
        obnovStav();
      },
      // Escape = rozmyslel jsem si to: v policku je zase nazev, ktery k bodu patri.
      vratNazev: function () {
        if (body[ktery]) {
          policko.value = potvrzene[ktery];
          obnovStav();
        }
      },
    });
  }

  function zavriSeznam(policko, seznam) {
    seznam.hidden = true;
    seznam.innerHTML = "";
    policko.setAttribute("aria-expanded", "false");
  }

  function oznacPolozku(seznam, index) {
    seznam.querySelectorAll('[role="option"]').forEach(function (li, i) {
      li.classList.toggle("vybrany", i === index);
      li.setAttribute("aria-selected", i === index ? "true" : "false");
    });
  }

  function pridejPolozku(seznam, index, nadpis, podnadpis, trida, naVyber) {
    var li = document.createElement("li");
    li.setAttribute("role", "option");
    if (trida) li.className = trida;
    var silne = document.createElement("strong");
    silne.textContent = nadpis;
    li.appendChild(silne);
    if (podnadpis) {
      var male = document.createElement("small");
      male.textContent = podnadpis;
      li.appendChild(male);
    }
    // mousedown, ne click - click by prisel az po ztrate fokusu pole
    // a seznam by uz byl zavreny.
    li.addEventListener("mousedown", function (u) {
      u.preventDefault();
      naVyber(index);
    });
    seznam.appendChild(li);
  }

  // Vypise vysledky do seznamu a vrati, co jde vybrat; {prejmenovat: true}
  // je vzdycky posledni. `selhalo`: server nebo Mapy.com neodpovedely - to neni
  // totez co "nic jsem nenasel", clovek by zkousel jine nazvy misto toho,
  // aby chvili pockal.
  function vykresliVysledky(seznam, data, dotaz, selhalo, muzePrejmenovat, naVyber) {
    var vysledky = data.slice();
    seznam.innerHTML = "";
    if (!data.length) {
      var prazdno = document.createElement("li");
      prazdno.className = "prazdne";
      prazdno.textContent = selhalo
        ? "Hledání teď nefunguje — zkus to za chvíli, nebo místo napiš ručně."
        : "Nic takového jsem nenašel.";
      seznam.appendChild(prazdno);
    }
    data.forEach(function (v, index) {
      pridejPolozku(seznam, index, v.nazev, v.popis, "", naVyber);
    });
    if (muzePrejmenovat) {
      vysledky.push({ prejmenovat: true });
      pridejPolozku(
        seznam, vysledky.length - 1, "Jen přejmenovat na „" + dotaz + "“",
        "Bod v mapě zůstane, kde je.", "prejmenovat", naVyber
      );
    }
    return vysledky;
  }

  // Obecny naseptavac pod textovym polem: psani spusti (se zpozdenim) hledani,
  // vysledky se vypisou do `seznam`, sipky a Enter vybiraji, Escape a ztrata
  // fokusu seznam zavrou. Co se pri tom ma stat, rika `volby`:
  //   pozmeneno()        - po kazdem zmenenem textu v policku,
  //   hledej(dotaz)      - vrati Promise s polem vysledku {nazev, popis, ...},
  //   muzePrejmenovat()  - ma se pod vysledky nabidnout "Jen prejmenovat"?,
  //   vyber(v)           - vybran vysledek hledani,
  //   prejmenovat()      - vybrano "Jen prejmenovat",
  //   vratNazev()        - Escape.
  function vytvorNaseptavac(policko, seznam, volby) {
    var vysledky = [];   // co jde vybrat; {prejmenovat: true} je vzdycky posledni
    var vybrany = -1;
    var casovac = null;
    var poradi = 0; // odpovedi na starsi dotaz se zahodi

    function zavri() {
      zavriSeznam(policko, seznam);
      vysledky = [];
      vybrany = -1;
    }

    function oznac(index) {
      vybrany = index;
      oznacPolozku(seznam, index);
    }

    // Starsi odpovedi i rozjety casovac se zahodi a seznam zmizi.
    function zrus() {
      poradi += 1;
      clearTimeout(casovac);
      zavri();
    }

    function vyber(index) {
      var v = vysledky[index];
      if (!v) return;
      zrus();
      if (v.prejmenovat) {
        volby.prejmenovat();
        return;
      }
      volby.vyber(v);
    }

    function ukaz(data, dotaz, selhalo) {
      vysledky = vykresliVysledky(seznam, data, dotaz, selhalo, volby.muzePrejmenovat(), vyber);
      seznam.hidden = false;
      policko.setAttribute("aria-expanded", "true");
      vybrany = -1;
    }

    policko.addEventListener("input", function () {
      volby.pozmeneno();
      clearTimeout(casovac);
      var dotaz = policko.value.trim();
      if (dotaz.length < minDelkaDotazu) {
        zrus();
        return;
      }
      casovac = setTimeout(function () {
        var moje = ++poradi;
        volby.hledej(dotaz)
          .then(function (data) {
            if (moje === poradi) ukaz(data, dotaz);
          })
          .catch(function () {
            if (moje === poradi) ukaz([], dotaz, true);
          });
      }, ZPOZDENI_HLEDANI_MS);
    });

    policko.addEventListener("keydown", function (u) {
      if (u.key === "Enter") {
        // Enter v policku Z / Do neodesila radek (mohl by odejit nedopsany,
        // jeste bez dat) - jizda se pridava tlacitkem. Je-li otevreny
        // naseptavac, vybere vysledek.
        u.preventDefault();
        if (vysledky.length) vyber(vybrany >= 0 ? vybrany : 0);
      } else if (u.key === "ArrowDown" && vysledky.length) {
        u.preventDefault();
        oznac((vybrany + 1) % vysledky.length);
      } else if (u.key === "ArrowUp" && vysledky.length) {
        u.preventDefault();
        oznac(vybrany <= 0 ? vysledky.length - 1 : vybrany - 1);
      } else if (u.key === "Escape") {
        zrus();
        volby.vratNazev();
      }
    });

    policko.addEventListener("blur", function () {
      // Az po pripadnem mousedown na polozce. Rozepsany dotaz, na ktery
      // odpoved jeste nedorazila, se zahodi - seznam by jinak vyskocil
      // pod polickem, ve kterem uz nikdo neni.
      setTimeout(zrus, 150);
    });
  }

  napojHledani("z");
  napojHledani("do");

  // ---- Pomocnici ----

  // Casovy limit, aby "Hledám…" a "Počítám trasu…" nevisely, kdyz server nestiha
  // nebo spojeni visi. Mapy.com ma na serveru limit 10 s, tohle je pojistka.
  var LIMIT_MAPY_MS = 15000;

  function ziskej(adresa) {
    return fetch(adresa, {
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout ? AbortSignal.timeout(LIMIT_MAPY_MS) : undefined,
    }).then(function (odpoved) {
      if (!odpoved.ok) throw new Error(odpoved.status);
      return odpoved.json();
    });
  }

  function uloz(klic, hodnota) {
    try {
      sessionStorage.setItem(klic, JSON.stringify(hodnota));
    } catch (chyba) {
      // Privatni okno nebo zakazane ulozeni - mapa si proste nic nepamatuje.
    }
  }

  function nacti(klic) {
    try {
      var hodnota = sessionStorage.getItem(klic);
      return hodnota ? JSON.parse(hodnota) : null;
    } catch (chyba) {
      return null;
    }
  }
})();
