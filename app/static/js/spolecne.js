/* Spolecne chovani pro kazdou stranku (K-075: puvodni app.js rozdeleny po obrazovkach):
   - trida `js` na <html>, aby CSS vedelo, ze JavaScript bezi,
   - bublina se stavem ukladani (window.appka.ukazStav) a ulozeni na pozadi
     (window.appka.ulozNaPozadi) - pouzivaji je dalsi soubory,
   - hlasky vpravo dole (krizek, zelene samy zmizi),
   - dotaz pred smazanim, tisk dokladu,
   - po odeslani formulare zustat na stejnem miste stranky.

   Nacita se v base.html jako prvni - ostatni soubory (js/*.js) na `window.appka`
   spolehaji. Vsechno je jen nadstavba: bez JavaScriptu appka funguje dal. */

(function () {
  "use strict";

  // Bez tohohle by CSS nepoznalo, ze JavaScript bezi, a schovalo by policka
  // v tabulce i prohlizeci, ktery je tuztickou nedokaze odkryt.
  document.documentElement.classList.add("js");

  // ---- 0. Chyby v JavaScriptu ----
  // Neosetrena chyba se posle na server (POST /profil/chyba-js) a zapise se do jeho
  // logu - nikam jinam. Posila se jen zprava, soubor, radek a cesta stranky
  // (zkracene), nikdy obsah formularu. Nejvys 3 zpravy za nacteni stranky.

  var HLASENI_CHYB_MAX = 3;
  var odeslanoChyb = 0;

  function nahlasChybu(zprava, soubor, radek) {
    if (odeslanoChyb >= HLASENI_CHYB_MAX) return;
    odeslanoChyb += 1;
    try {
      var telo = new URLSearchParams();
      telo.set("zprava", String(zprava || "").slice(0, 300));
      telo.set("soubor", String(soubor || "").slice(0, 300));
      telo.set("radek", String(radek || "").slice(0, 10));
      telo.set("strana", location.pathname);
      fetch("/profil/chyba-js", { method: "POST", body: telo, keepalive: true }).catch(function () {});
    } catch (chyba) {
      // Hlaseni chyby nesmi samo zpusobit dalsi chybu.
    }
    ukazStav("Na stránce se něco pokazilo — obnov ji.", "chyba");
  }

  window.addEventListener("error", function (udalost) {
    nahlasChybu(udalost.message, udalost.filename, udalost.lineno);
  });
  window.addEventListener("unhandledrejection", function (udalost) {
    var duvod = udalost.reason;
    nahlasChybu(duvod && duvod.message ? duvod.message : duvod, "", "");
  });

  // Jedna cast inicializace nesmi vyradit ty za ni: vyhodi-li neco vyjimku,
  // nahlasi se a zbytek se spusti dal.
  function bezpecne(cast) {
    try {
      cast();
    } catch (chyba) {
      nahlasChybu(chyba && chyba.message, "spolecne.js", "");
    }
  }

  // Dotaz pred smazanim se registruje JAKO PRVNI: kdyby kterakoli dalsi cast
  // spadla, krizek "smazat uctenku i se souborem" by jinak maza bez ptani.
  // (Funkce `nastavPotvrzeni` je nize; deklarace funkci se zvedaji nahoru.)
  document.querySelectorAll("[data-potvrdit]").forEach(nastavPotvrzeni);

  // ---- Bublina se stavem ukladani (vpravo dole) ----

  var bublina = null;
  var casovacBubliny = null;

  // Sloupec vpravo dole, kde jsou i hlasky ze serveru (base.html) - bublina
  // do nej patri taky, jinak by se s hlaskami prekryvala.
  function sloupecHlasek() {
    var sloupec = document.querySelector(".hlasky");
    if (!sloupec) {
      sloupec = document.createElement("div");
      sloupec.className = "hlasky";
      sloupec.setAttribute("aria-live", "polite");
      document.body.appendChild(sloupec);
    }
    return sloupec;
  }

  // Bublina ukazuje dalsi stav az do dalsiho volani. "Uloženo" po chvili zmizi,
  // "Ukládám…" zustava, dokud neskonci, a chyba zustava, dokud se neco neulozi
  //: clovek se pri vyplnovani tabulky dival do ni, ne do rohu, a po 6 s
  // by odesel v domneni, ze je ulozeno. `znovu` (neni povinne) prida tlacitko
  // "Zkusit znovu"; chybu jde taky zavrit krizkem.
  function ukazStav(text, druh, znovu) {
    if (!bublina) {
      bublina = document.createElement("div");
      bublina.className = "stav-ukladani";
      sloupecHlasek().appendChild(bublina);
    }
    bublina.className = "stav-ukladani je-videt" + (druh ? " " + druh : "");
    bublina.textContent = "";
    var veta = document.createElement("span");
    veta.className = "stav-text";
    veta.textContent = text;
    bublina.appendChild(veta);
    clearTimeout(casovacBubliny);

    if (druh === "chyba") {
      if (znovu) {
        var tlacitko = document.createElement("button");
        tlacitko.type = "button";
        tlacitko.className = "duch drobne stav-akce";
        tlacitko.textContent = "Zkusit znovu";
        tlacitko.addEventListener("click", znovu);
        bublina.appendChild(tlacitko);
      }
      var krizek = document.createElement("button");
      krizek.type = "button";
      krizek.className = "duch drobne stav-akce";
      krizek.setAttribute("aria-label", "Zavřít hlášku");
      krizek.textContent = "×";
      krizek.addEventListener("click", function () {
        bublina.className = "stav-ukladani";
      });
      bublina.appendChild(krizek);
    } else if (druh) {
      casovacBubliny = setTimeout(function () {
        bublina.className = "stav-ukladani";
      }, 1800);
    }
  }

  // Bublina s textem, ktery po `ms` sam zmizi - ale jen kdyz ji mezitim nezabral
  // jiny stav (chyba ulozeni nesmi zmizet kvuli stare zprave o stahovani).
  function ukazDocasne(text, ms) {
    ukazStav(text, "");
    var moje = bublina;
    setTimeout(function () {
      var veta = moje.querySelector(".stav-text");
      if (veta && veta.textContent === text && moje.className.indexOf("chyba") < 0) {
        moje.className = "stav-ukladani";
      }
    }, ms);
  }

  // Schova bublinu s "Cekam..." (po navratu tlacitkem Zpet, K-139).
  function schovejStav() {
    if (bublina && bublina.className.indexOf("chyba") < 0) bublina.className = "stav-ukladani";
  }

  // ---- Ulozeni na pozadi (jedno misto pro prubezne ukladani, rozdavani i razeni) ----
  // Musi sedet s HLAVICKA_PRUBEZNE v app/routes/spolecne.py.
  var HLAVICKA_PRUBEZNE = "X-Prubezne-Ulozeni";
  // Jak dlouho se ceka na odpoved serveru. Kdyz server nestiha nebo spojeni visi
  // (mobil na taboře), bublina by jinak "Ukládám…" ukazovala donekonečna.
  var LIMIT_ULOZENI_MS = 20000;

  function limitCasu(ms) {
    return typeof AbortSignal !== "undefined" && AbortSignal.timeout
      ? AbortSignal.timeout(ms)
      : undefined;
  }

  // Veta ze serveru, kdyz ji poslal on (stavy, u kterych vime, ze je to nase
  // veta, ne surova chyba serveru): text u 401/403/409/422, `detail` z JSONu.
  function zpravaZOdpovedi(odpoved) {
    var typ = odpoved.headers.get("content-type") || "";
    var stav = odpoved.status;
    if (typ.indexOf("text/plain") === 0 && [401, 403, 409, 422].indexOf(stav) >= 0) {
      return odpoved.text().catch(function () { return ""; });
    }
    if (typ.indexOf("application/json") === 0 && [403, 404, 409, 422].indexOf(stav) >= 0) {
      return odpoved.json().then(function (data) {
        return typeof data.detail === "string" ? data.detail : "";
      }).catch(function () { return ""; });
    }
    return Promise.resolve("");
  }

  // Odesle formular na server bez prekresleni stranky a ukaze vysledek v bublině.
  // Vrati Promise<boolean>: true jen pri odpovedi 204. Presmerovani a 200 jsou
  // chyba - `fetch` presmerovani na prihlaseni sam nasleduje a vrati stranku se
  // stavem 200, takze "ok" by tu lhalo.
  // `texty`: { bezi, hotovo, chyba, znovu } - vse nepovinne; `znovu` je funkce
  // pro tlacitko "Zkusit znovu".
  function ulozNaPozadi(adresa, telo, texty) {
    texty = texty || {};
    var hlavicky = {};
    hlavicky[HLAVICKA_PRUBEZNE] = "1";
    var obecna = texty.chyba || "Nepodařilo se uložit.";
    ukazStav(texty.bezi || "Ukládám…", "");

    return fetch(adresa, {
      method: "POST",
      body: telo,
      headers: hlavicky,
      signal: limitCasu(LIMIT_ULOZENI_MS),
    }).then(function (odpoved) {
      if (odpoved.status === 204 && !odpoved.redirected) {
        ukazStav(texty.hotovo || "Uloženo", "hotovo");
        return true;
      }
      return zpravaZOdpovedi(odpoved).then(function (zprava) {
        // 422 s textem: zbytek je ulozeny, ale nektere cislo server nepreclet
        // a nechal puvodni hodnotu - "Zkusit znovu" by nic nezmenilo.
        var znovu = odpoved.status === 422 ? undefined : texty.znovu;
        ukazStav(zprava || obecna, "chyba", znovu);
        return false;
      });
    }, function (chyba) {
      var vyprselo = chyba && chyba.name === "TimeoutError";
      ukazStav(
        vyprselo ? "Server teď neodpovídá — neuloženo, zkus to za chvíli." : obecna,
        "chyba",
        texty.znovu
      );
      return false;
    });
  }

  // Tlacitka, ktera davaji smysl jen bez JavaScriptu (napr. "Uložit" u formulare,
  // ktery se uklada sam), se schovaji. Bez `selektor` ty uvnitr `koren`.
  function schovejBezJs(koren, selektor) {
    koren.querySelectorAll(selektor || "[data-bez-js]").forEach(function (prvek) {
      prvek.hidden = true;
    });
  }

  // ---- 6. Dotaz pred smazanim ----
  // Krizek v pokladni knize nemaze jen radek v tabulce, ale samotnou uctenku
  // vcetne souboru. Bez dotazu by to bylo jedno kliknuti od nevratne ztraty.
  // Bez JavaScriptu se formular odesle rovnou - proto je text dotazu v sablone
  // psany tak, aby stejnou vec rikal i popisek tlacitka.

  function nastavPotvrzeni(formular) {
    formular.addEventListener("submit", function (udalost) {
      if (!window.confirm(formular.dataset.potvrdit)) udalost.preventDefault();
    });
  }

  // ---- 7. Tisk dokladu ----
  // Tiskopisy PPD/VPD se tisknou primo z prohlizece, zadny generator PDF.
  // Tlacitko dela presne totez co Ctrl+P - jen at ho clovek nemusi hledat.

  function nastavTisk(tlacitko) {
    tlacitko.addEventListener("click", function () { window.print(); });
  }

  // ---- 10. Hlasky vpravo dole ----
  // Zelena hlaska ("Jizda pridana") po chvili zmizi sama - jen potvrzuje, co
  // uzivatel prave udelal. Chyba a varovani zustanou, dokud je nezavre:
  // kdyby zmizely samy, snadno by je prehledl a nevedel by, ze se neco nepovedlo.

  var HLASKA_ZMIZI_MS = 5000;

  function zavriHlasku(hlaska) {
    hlaska.classList.add("odchazi");
    // 220 ms = prechod `.hlasky .oznameni.odchazi` v app.css (.2s) a 20 ms rezerva;
    // kdo zmeni jedno, musi zmenit i druhe, jinak hlaska zmizi drive, nez dobehne
    // animace, nebo na obrazovce zustane prazdne misto.
    setTimeout(function () { hlaska.remove(); }, 220);
  }

  bezpecne(function () {
    document.querySelectorAll(".hlasky .oznameni").forEach(function (hlaska) {
      var krizek = document.createElement("button");
      krizek.type = "button";
      krizek.className = "hlaska-zavrit";
      krizek.setAttribute("aria-label", "Zavřít hlášku");
      krizek.textContent = "×";
      krizek.addEventListener("click", function () { zavriHlasku(hlaska); });
      hlaska.appendChild(krizek);

      if (hlaska.classList.contains("chyba") || hlaska.classList.contains("pozor")) return;

      // Kdyz na ni uzivatel zrovna najede mysi (cte ji), odpocet se zastavi.
      var casovac = setTimeout(function () { zavriHlasku(hlaska); }, HLASKA_ZMIZI_MS);
      hlaska.addEventListener("mouseenter", function () { clearTimeout(casovac); });
      hlaska.addEventListener("mouseleave", function () {
        casovac = setTimeout(function () { zavriHlasku(hlaska); }, HLASKA_ZMIZI_MS / 2);
      });
    });
  });

  // ---- 11. Po odeslani formulare zustat na stejnem miste ----
  // Server po kazdem ulozeni presmeruje (zpet() v app/sablony.py) a prohlizec
  // by novou stranku ukazal od zacatku - po pridani jizdy dole v cestaku by
  // clovek skoncil nahore. Pred odeslanim si proto zapamatujeme, kde byl,
  // a kdyz se vrati na tutez adresu, vratime ho tam. Kdyz ho server posle
  // jinam, zacina se nahore jako vzdycky. Jen sessionStorage teto zalozky.

  var KLIC_POZICE = "pozice-po-odeslani";
  var POZICE_PLATI_MS = 15000;

  document.addEventListener("submit", function (udalost) {
    var formular = udalost.target;
    if (udalost.defaultPrevented || (formular.method || "").toLowerCase() !== "post") return;
    try {
      sessionStorage.setItem(KLIC_POZICE, JSON.stringify({
        adresa: location.pathname + location.search,
        y: window.scrollY,
        kdy: Date.now(),
      }));
    } catch (chyba) {
      // Soukromy rezim - stranka proste zacne nahore.
    }
    bezpecne(function () { zamkniOdeslani(udalost); });
  });

  // ---- 12. Po odeslani tlacitko zesedne, dokud se stranka nenacte ----
  // Na pomalem serveru clovek klikne znovu, protoze se "nic nedeje" - a pridal by
  // druhy prijem, druhou jizdu nebo druhe (placene) cteni uctenky. Proto se po
  // odeslani odesilaci tlacitka formulare vypnou a nakresli se na nich tocici
  // kolecko (Pico: aria-busy). Formular s atributem `data-cekani="text"` k tomu
  // ukaze v bublině, na co se ceka. Neplati pro:
  //   - `data-prubezne` (odesila se na pozadi a tlacitko Ulozit je schovane),
  //   - `data-vlastni-zamek` (formular si hlida sam, napr. panel mapy u cestaku),
  //   - zrusene odeslani (dotaz pred smazanim, K-154) - to uz sem nedojde.
  // Server dvoji odeslani nehlida zamerne: tlacitko staci (rozhodnuti R18).

  function submitTlacitka(formular) {
    return [].filter.call(formular.elements, function (prvek) {
      return (prvek.tagName === "BUTTON" || prvek.tagName === "INPUT") && prvek.type === "submit";
    });
  }

  function zamkniOdeslani(udalost) {
    var formular = udalost.target;
    if (formular.hasAttribute("data-prubezne") || formular.hasAttribute("data-vlastni-zamek")) return;

    var tlacitka = submitTlacitka(formular);
    var hlavni = udalost.submitter || tlacitka[0];
    if (formular.dataset.cekani) ukazStav(formular.dataset.cekani, "");

    // Az po skonceni udalosti: vypnute tlacitko by prohlizec z odeslani vynechal
    // (jmeno a hodnota tlacitka patri do odeslanych dat).
    setTimeout(function () {
      tlacitka.forEach(function (tlacitko) {
        tlacitko.disabled = true;
        tlacitko.dataset.zamceno = "1";
      });
      if (hlavni) hlavni.setAttribute("aria-busy", "true");
    }, 0);
  }

  // Zpet/Vpred z mezipameti prohlizece vrati stranku i s vypnutymi tlacitky.
  window.addEventListener("pageshow", function (udalost) {
    if (!udalost.persisted) return;
    document.querySelectorAll("[data-zamceno]").forEach(function (tlacitko) {
      tlacitko.disabled = false;
      tlacitko.removeAttribute("aria-busy");
      delete tlacitko.dataset.zamceno;
    });
    schovejStav();
  });

  // Odkaz na soubor, ktery server nejdriv cely slozi (ZIP, zaloha): do zacatku
  // stahovani se jinak po kliknuti nedeje nic.
  bezpecne(function () {
    document.querySelectorAll("a[data-cekani]").forEach(function (odkaz) {
      odkaz.addEventListener("click", function () {
        ukazDocasne(odkaz.dataset.cekani, 10000);
      });
    });
  });

  bezpecne(function vratNaPozici() {
    var pozice = null;
    try {
      pozice = JSON.parse(sessionStorage.getItem(KLIC_POZICE) || "null");
      sessionStorage.removeItem(KLIC_POZICE);
    } catch (chyba) {
      return;
    }
    if (!pozice || Date.now() - pozice.kdy > POZICE_PLATI_MS) return;
    if (pozice.adresa !== location.pathname + location.search) return;

    window.scrollTo(0, pozice.y);
    // Obrazky se jeste mohou dotahovat a stranku natahnout - po nacteni
    // jeste jednou, pokud mezitim uzivatel sam nescrolloval.
    var posunulSe = false;
    window.addEventListener("wheel", function () { posunulSe = true; }, { once: true });
    window.addEventListener("touchmove", function () { posunulSe = true; }, { once: true });
    window.addEventListener("load", function () {
      if (!posunulSe) window.scrollTo(0, pozice.y);
    });
  });

  // ---- 13. Formular s chybou u pole ----
  // Server po spatne vyplnenem formulari vykresli stranku znovu (stav 422) a pole
  // s chybou ma aria-invalid="true". Prvni z nich dostane fokus: stranka se k nemu
  // posune (formular "Nastaveni akce" je daleko dole) a ctecka obrazovky precte
  // jeho popisek i text chyby (aria-describedby).
  bezpecne(function () {
    var spatne = document.querySelector('[aria-invalid="true"]');
    if (spatne) spatne.focus();
  });

  // Pole s data-nejmene-podle="jine_pole" (konec akce) nepusti datum drive, nez je
  // hodnota toho druheho pole (zacatek akce). Jen pohodli - server hlida totez.
  bezpecne(function () {
    document.querySelectorAll("[data-nejmene-podle]").forEach(function (pole) {
      var prvni = pole.form && pole.form.elements[pole.dataset.nejmenePodle];
      if (!prvni) return;
      function nastav() {
        pole.min = prvni.value || "";
      }
      prvni.addEventListener("change", nastav);
      nastav();
    });
  });

  // Tisk se ctenim neztrati, proto bezi i pro revizora (potvrzeni je nahore).
  bezpecne(function () {
    document.querySelectorAll("[data-tisk]").forEach(nastavTisk);
  });

  // Nabidka v hlavicce: na pocitaci je <details> vzdy otevreny (odkazy jsou
  // v radku, tlacitko "Nabidka" je schovane), na telefonu zavreny. Hranice je stejna
  // jako v app.css (37,5 rem). Escape nabidku na telefonu zavre a vrati fokus na tlacitko.
  bezpecne(function () {
    var nabidka = document.querySelector("details.nabidka");
    if (!nabidka) return;
    var telefon = window.matchMedia("(max-width: 37.5rem)");
    function nastav() {
      nabidka.open = !telefon.matches;
    }
    nastav();
    telefon.addEventListener("change", nastav);
    nabidka.addEventListener("keydown", function (udalost) {
      if (udalost.key !== "Escape" || !telefon.matches || !nabidka.open) return;
      nabidka.open = false;
      var tlacitko = nabidka.querySelector("summary");
      if (tlacitko) tlacitko.focus();
    });
  });

  // Stranka s chybou u pole je odpoved na POST, takze v adresnim radku je adresa POST
  // (/lide/pridat): F5 by nabidl odeslat formular znovu a otevreni adresy by skoncilo
  // chybou 405. Server proto v <main> posle adresu stranky, odkud formular prisel
  // (data-adresa-po-chybe); nahradime jí aktualni zaznam v historii. Napsane hodnoty
  // a chyby zustanou na obrazovce, obnoveni stranky nacte cistou stranku.
  bezpecne(function () {
    var hlavni = document.querySelector("main[data-adresa-po-chybe]");
    if (!hlavni || !window.history || !window.history.replaceState) return;
    var adresa = hlavni.getAttribute("data-adresa-po-chybe") || "";
    if (adresa.charAt(0) !== "/" || adresa.charAt(1) === "/") return;
    window.history.replaceState(window.history.state, "", adresa);
  });

  // Revizor stejne nic neulozi (server to odmitne, viz app.css ".jen-cteni") -
  // tak se ani nesnazi: soubory, ktere by volaly server (ukladani, nahrani, razeni),
  // se v rezimu jen pro cteni nerozjedou. Lupa, galerie, tisk a potvrzeni zustavaji.
  // Zjisti se tady jednou, aby to vsechny soubory videly stejne.
  window.appka = {
    ukazStav: ukazStav,
    ulozNaPozadi: ulozNaPozadi,
    schovejBezJs: schovejBezJs,
    jenCteni: document.body.classList.contains("jen-cteni"),
  };
})();
