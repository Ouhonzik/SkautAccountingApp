/* Stranka vozidel: galerie technickych prukazu pres celou obrazovku a poradi
   vozidel pretahovanim za uchyt. Nacita se jen ve vozidla.html. */

(function () {
  "use strict";

  // Ulozeni na pozadi a bublina jsou ve spolecne.js; tady se jen pouzivaji.
  var ulozNaPozadi = window.appka.ulozNaPozadi;
  var jenCteni = window.appka.jenCteni;

  // ---- 5c. Galerie technickych prukazu ----
  // Jednodussi sestra lupy: jen obrazek pres celou obrazovku a sipky mezi
  // stranami jednoho auta. Kliknuti na obrazek ho priblizi - na malem
  // technickem prukazu je drobne pismo, ktere se do okna vejit nemusi.

  function nastavGalerii() {
    var dialog = document.querySelector("dialog.galerie");
    if (!dialog || !dialog.showModal) return;

    var obrazek = dialog.querySelector(".galerie-obrazek");
    var plocha = dialog.querySelector(".galerie-plocha");
    var nazev = dialog.querySelector(".galerie-nazev");
    var pozice = dialog.querySelector(".galerie-pozice");
    var original = dialog.querySelector(".galerie-original");
    var sipky = dialog.querySelectorAll(".lupa-sipka");

    var fotky = [];  // odkazy jednoho auta
    var kde = 0;

    function ukaz(index) {
      kde = (index + fotky.length) % fotky.length;
      var odkaz = fotky[kde];
      plocha.classList.remove("priblizeno");
      obrazek.style.width = "";
      obrazek.src = odkaz.dataset.velky;
      original.href = odkaz.href;
      nazev.textContent = odkaz.dataset.nazev;
      pozice.textContent = fotky.length > 1 ? "strana " + (kde + 1) + " z " + fotky.length : "";
      sipky.forEach(function (sipka) { sipka.hidden = fotky.length < 2; });
    }

    document.querySelectorAll("a[data-galerie]").forEach(function (odkaz) {
      odkaz.addEventListener("click", function (u) {
        u.preventDefault();
        fotky = [].slice.call(
          document.querySelectorAll('a[data-galerie="' + odkaz.dataset.galerie + '"]'));
        ukaz(fotky.indexOf(odkaz));
        dialog.showModal();
        dialog.focus();
      });
    });

    // Priblizeni: obrazek se ukaze ve skutecne velikosti a plocha se da
    // posouvat. Kam clovek klikl, tam se priblizi.
    obrazek.addEventListener("click", function (u) {
      var ramec = obrazek.getBoundingClientRect();
      var x = (u.clientX - ramec.left) / ramec.width;
      var y = (u.clientY - ramec.top) / ramec.height;
      var priblizeno = plocha.classList.toggle("priblizeno");
      if (!priblizeno) {
        obrazek.style.width = "";
        return;
      }
      // Aspon dvakrat vetsi nez v okne, jinak by na velkem monitoru
      // "priblizeni" skoro nebylo znat.
      obrazek.style.width = Math.max(obrazek.naturalWidth, ramec.width * 2) + "px";
      var plny = fotky[kde].dataset.plny;
      if (plny && obrazek.getAttribute("src") !== plny) obrazek.src = plny;
      plocha.scrollLeft = x * plocha.scrollWidth - plocha.clientWidth / 2;
      plocha.scrollTop = y * plocha.scrollHeight - plocha.clientHeight / 2;
    });

    dialog.querySelector(".galerie-drive").addEventListener("click", function () { ukaz(kde - 1); });
    dialog.querySelector(".galerie-dalsi").addEventListener("click", function () { ukaz(kde + 1); });
    dialog.querySelector(".galerie-zavrit").addEventListener("click", function () { dialog.close(); });
    dialog.addEventListener("keydown", function (u) {
      if (u.key === "ArrowLeft" || u.key === "ArrowRight") {
        u.preventDefault();
        ukaz(u.key === "ArrowLeft" ? kde - 1 : kde + 1);
      }
    });
    // Kliknuti na tmave pozadi mimo obrazek galerii zavre.
    dialog.addEventListener("click", function (u) {
      if (u.target === dialog || u.target === plocha) dialog.close();
    });
  }

  // ---- 9. Poradi vozidel pretahovanim za uchyt ----
  // Tazeni je udelane pres pointer udalosti, ne pres HTML drag & drop - to na
  // dotykovem displeji nefunguje a neumi ukazat blok "v ruce" pod prstem.
  // Chyceny blok se vytrhne z toku (position: fixed) a na jeho miste zustane
  // carkovane volne misto, ktere se posouva mezi ostatnimi. Po pusteni se
  // poradi hned ulozi. Sipky nahoru/dolu na uchytu delaji totez z klavesnice.

  var OKRAJ_POSUNU = 70; // jak blizko okraje okna se zacne stranka posouvat

  function bloky(seznam) {
    return [].slice.call(seznam.querySelectorAll(".vozidlo-blok"));
  }

  function poradiId(seznam) {
    return bloky(seznam).map(function (blok) { return blok.dataset.vozidlo; }).join(",");
  }

  function precisluj(seznam) {
    bloky(seznam).forEach(function (blok, i) {
      var cislo = blok.querySelector(".vozidlo-cislo");
      if (cislo) cislo.textContent = i + 1;
    });
  }

  function ulozPoradi(seznam) {
    precisluj(seznam);
    var telo = new FormData();
    telo.append("poradi", poradiId(seznam));
    ulozNaPozadi(seznam.dataset.razeni, telo, {
      bezi: "Ukládám pořadí…",
      hotovo: "Pořadí uloženo",
      chyba: "Pořadí se nepodařilo uložit.",
      znovu: function () { ulozPoradi(seznam); },
    });
  }

  // Sipky nahoru/dolu na uchytu posunou blok o misto a hned ulozi poradi.
  function napojKlavesyUchytu(seznam, uchyt, blok) {
    uchyt.addEventListener("keydown", function (u) {
      if (u.key !== "ArrowUp" && u.key !== "ArrowDown") return;
      u.preventDefault();
      var soused = u.key === "ArrowUp" ? blok.previousElementSibling : blok.nextElementSibling;
      if (!soused || !soused.classList.contains("vozidlo-blok")) return;
      seznam.insertBefore(blok, u.key === "ArrowUp" ? soused : soused.nextElementSibling);
      uchyt.focus();
      ulozPoradi(seznam);
    });
  }

  // Chyceny blok se vytrhne z toku a na jeho miste zustane carkovane volne
  // misto. Vraci ho.
  function vytrhniBlok(seznam, blok, ramec) {
    var misto = document.createElement("div");
    misto.className = "vozidlo-misto";
    misto.style.height = ramec.height + "px";
    seznam.insertBefore(misto, blok);

    blok.classList.add("vozidlo-v-ruce");
    blok.style.left = ramec.left + "px";
    blok.style.top = ramec.top + "px";
    blok.style.width = ramec.width + "px";
    seznam.classList.add("razeni-bezi");
    document.body.classList.add("razeni-bezi");
    return misto;
  }

  // Volne misto jde pred prvni blok, jehoz stred je pod ukazatelem.
  // `t` je stav jednoho tazeni (viz zacniTazeni).
  function prerovnej(t) {
    t.blok.style.top = t.posledniY - t.odTopu + "px";
    var cil = null;
    bloky(t.seznam).some(function (jiny) {
      if (jiny === t.blok) return false;
      var r = jiny.getBoundingClientRect();
      if (t.posledniY < r.top + r.height / 2) { cil = jiny; return true; }
      return false;
    });
    if (cil) {
      if (t.misto.nextElementSibling !== cil) t.seznam.insertBefore(t.misto, cil);
    } else {
      t.seznam.appendChild(t.misto);
    }
  }

  // U okraje okna se stranka posouva sama, aby slo auto dostat i na
  // misto, ktere prave neni videt.
  function posouvej(t) {
    var krok = 0;
    if (t.posledniY < OKRAJ_POSUNU) krok = -12;
    else if (t.posledniY > window.innerHeight - OKRAJ_POSUNU) krok = 12;
    if (krok) {
      window.scrollBy(0, krok);
      prerovnej(t);
    }
    t.casovac = window.requestAnimationFrame(function () { posouvej(t); });
  }

  // Blok se vrati do toku na misto, kde zustalo volne misto. Poradi se ulozi
  // jen kdyz se opravdu zmenilo.
  function ukonciTazeni(t, uloz) {
    t.seznam.insertBefore(t.blok, t.misto);
    t.misto.remove();
    t.blok.classList.remove("vozidlo-v-ruce");
    t.blok.style.left = t.blok.style.top = t.blok.style.width = "";
    t.seznam.classList.remove("razeni-bezi");
    document.body.classList.remove("razeni-bezi");
    if (poradiId(t.seznam) !== t.puvodni) uloz();
  }

  function zacniTazeni(seznam, blok, u, uloz) {
    var ramec = blok.getBoundingClientRect();
    var t = {
      seznam: seznam,
      blok: blok,
      puvodni: poradiId(seznam),
      odTopu: u.clientY - ramec.top,
      posledniY: u.clientY,
      casovac: null,
      misto: null,
    };
    t.misto = vytrhniBlok(seznam, blok, ramec);
    t.casovac = window.requestAnimationFrame(function () { posouvej(t); });

    function pohyb(e) {
      t.posledniY = e.clientY;
      prerovnej(t);
    }

    function konec() {
      window.cancelAnimationFrame(t.casovac);
      document.removeEventListener("pointermove", pohyb);
      document.removeEventListener("pointerup", konec);
      document.removeEventListener("pointercancel", konec);
      ukonciTazeni(t, uloz);
    }

    // Posloucha cely dokument, ne uchyt - mys z male ikonky pri tazeni
    // hned sjede a udalosti by uchyt minuly.
    document.addEventListener("pointermove", pohyb);
    document.addEventListener("pointerup", konec);
    document.addEventListener("pointercancel", konec);
  }

  function nastavRazeniVozidel(seznam) {
    var uloz = function () { ulozPoradi(seznam); };

    seznam.querySelectorAll(".vozidlo-uchyt").forEach(function (uchyt) {
      var blok = uchyt.closest(".vozidlo-blok");

      // Bez JavaScriptu uchyt odesila formular "o misto nahoru". Tady ne.
      uchyt.addEventListener("click", function (u) { u.preventDefault(); });

      napojKlavesyUchytu(seznam, uchyt, blok);

      uchyt.addEventListener("pointerdown", function (u) {
        if (u.button !== 0) return;
        u.preventDefault();
        zacniTazeni(seznam, blok, u, uloz);
      });
    });
  }

  if (!jenCteni) {
    document.querySelectorAll("[data-razeni]").forEach(nastavRazeniVozidel);
  }
  nastavGalerii();
})();
