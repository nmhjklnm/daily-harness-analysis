/* daily-harness-analysis — progressive enhancement only. Page works without JS. */
(function () {
  "use strict";

  /* ---- Theme toggle (state already applied by inline head script) -------- */
  var root = document.documentElement;
  var btn = document.querySelector(".theme-toggle");
  if (btn) {
    var sync = function () {
      var t = root.getAttribute("data-theme") || "dark";
      btn.setAttribute("aria-pressed", t === "light" ? "true" : "false");
      btn.setAttribute(
        "aria-label",
        t === "light" ? "Switch to dark theme" : "Switch to light theme"
      );
    };
    sync();
    btn.addEventListener("click", function () {
      var next =
        (root.getAttribute("data-theme") || "dark") === "dark"
          ? "light"
          : "dark";
      root.setAttribute("data-theme", next);
      try {
        localStorage.setItem("nmd-theme", next);
      } catch (e) {}
      sync();
    });
  }

  /* ---- Issue-feed view toggle: list (default) / grid --------------------- */
  /* The chosen view is a class on <html> (view-list / view-grid), already set
     before paint by the inline head script from localStorage('nmd-view'). Here
     we sync the toolbar buttons' aria-pressed to it and wire click -> set class
     + persist. The in-issue category filter + search bind to the same .item
     nodes, so both views filter/search identically. */
  var viewBtns = [].slice.call(document.querySelectorAll(".viewbar__btn"));
  if (viewBtns.length) {
    var readView = function () {
      return root.classList.contains("view-grid") ? "grid" : "list";
    };
    var syncView = function () {
      var v = readView();
      viewBtns.forEach(function (b) {
        var on = b.getAttribute("data-view") === v;
        b.setAttribute("aria-pressed", on ? "true" : "false");
      });
    };
    var setView = function (v) {
      var grid = v === "grid";
      root.classList.toggle("view-grid", grid);
      root.classList.toggle("view-list", !grid);
      try {
        localStorage.setItem("nmd-view", grid ? "grid" : "list");
      } catch (e) {}
      syncView();
    };
    syncView();
    viewBtns.forEach(function (b) {
      b.addEventListener("click", function () {
        setView(b.getAttribute("data-view") || "list");
      });
    });
  }

  var reduce =
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---- Scroll reveal (gentle, once) -------------------------------------- */
  var reveals = document.querySelectorAll(".reveal");
  if (reveals.length) {
    if (reduce || !("IntersectionObserver" in window)) {
      reveals.forEach(function (el) {
        el.classList.add("is-in");
      });
    } else {
      var io = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (en) {
            if (en.isIntersecting) {
              en.target.classList.add("is-in");
              io.unobserve(en.target);
            }
          });
        },
        { threshold: 0.12, rootMargin: "0px 0px -8% 0px" }
      );
      reveals.forEach(function (el) {
        io.observe(el);
      });
    }
  }

  /* ---- Two-level TOC: scrollspy + progressive disclosure ----------------- */
  var toc = document.querySelector(".toc");
  if (toc) {
    var items = [].slice.call(toc.querySelectorAll(".toc__item"));
    var topLinks = [].slice.call(toc.querySelectorAll(".toc__link"));
    var subLinks = [].slice.call(toc.querySelectorAll(".toc__sublink"));
    var mbtn = toc.querySelector(".toc__mobile");

    var subByItem = {}; // body item id -> sublink
    subLinks.forEach(function (a) {
      var href = a.getAttribute("href");
      if (href && href.charAt(0) === "#") subByItem[href.slice(1)] = a;
    });

    var secIdOf = function (li) {
      var l = li.querySelector(".toc__link");
      var h = l && l.getAttribute("href");
      return h && h.charAt(0) === "#" ? h.slice(1) : null;
    };

    // Expand exactly one section (the active one); collapse the rest.
    var expandSection = function (secId) {
      items.forEach(function (li) {
        var on = secIdOf(li) === secId;
        if (li.classList.contains("has-children")) {
          li.classList.toggle("is-expanded", on);
        }
        var l = li.querySelector(".toc__link");
        if (l) l.classList.toggle("is-active", on);
      });
    };

    var setOpen = function (open) {
      toc.classList.toggle("is-open", open);
      if (mbtn) mbtn.setAttribute("aria-expanded", open ? "true" : "false");
    };

    // Section-level scrollspy -> drives which section is expanded/active.
    var secEls = document.querySelectorAll(".section[id], .lead[id]");
    if (secEls.length && "IntersectionObserver" in window) {
      var secSpy = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (en) {
            if (en.isIntersecting) expandSection(en.target.id);
          });
        },
        { rootMargin: "-25% 0px -65% 0px", threshold: 0 }
      );
      secEls.forEach(function (s) {
        secSpy.observe(s);
      });
    } else {
      var firstChild = toc.querySelector(".toc__item.has-children");
      if (firstChild) expandSection(secIdOf(firstChild));
    }

    // Item-level scrollspy -> highlights the active second-level link.
    var itemEls = document.querySelectorAll(".item[id]");
    if (itemEls.length && "IntersectionObserver" in window) {
      var itemSpy = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (en) {
            if (en.isIntersecting) {
              subLinks.forEach(function (a) {
                a.classList.remove("is-active");
              });
              var a = subByItem[en.target.id];
              if (a) a.classList.add("is-active");
            }
          });
        },
        { rootMargin: "-30% 0px -60% 0px", threshold: 0 }
      );
      itemEls.forEach(function (it) {
        itemSpy.observe(it);
      });
    }

    // Clicking a top-level link expands its section at once; any link closes
    // the mobile drawer.
    topLinks.forEach(function (a) {
      a.addEventListener("click", function () {
        var h = a.getAttribute("href");
        if (h && h.charAt(0) === "#") expandSection(h.slice(1));
        setOpen(false);
      });
    });
    subLinks.forEach(function (a) {
      a.addEventListener("click", function () {
        setOpen(false);
      });
    });

    if (mbtn) {
      mbtn.addEventListener("click", function () {
        setOpen(!toc.classList.contains("is-open"));
      });
    }
  }

  /* ---- Collapsible search/filter toggle (DESIGN B) ----------------------- */
  /* The search box + category pills are wrapped in a panel collapsed by
     default; a compact toggle reveals it. Toggle + inner form are both gated
     [hidden] for non-JS users; activate them here. The filter logic below
     binds to the same elements regardless of panel visibility. */
  var filterBar = document.querySelector(".filterbar");
  var filterToggle = filterBar && filterBar.querySelector(".filterbar__toggle");
  if (filterBar && filterToggle) {
    filterToggle.removeAttribute("hidden");
    var setFilterOpen = function (open) {
      filterBar.classList.toggle("is-open", open);
      filterToggle.setAttribute("aria-expanded", open ? "true" : "false");
    };
    filterToggle.addEventListener("click", function () {
      var open = !filterBar.classList.contains("is-open");
      setFilterOpen(open);
      if (open) {
        var inp = filterBar.querySelector(".issue-filter__input");
        if (inp) inp.focus();
      }
    });
  }

  /* ---- Issue filter: client-side search + category toggles --------------- */
  var filterForm = document.querySelector(".issue-filter");
  if (filterForm) {
    filterForm.removeAttribute("hidden"); // activate (gated for non-JS users)
    var fInput = filterForm.querySelector(".issue-filter__input");
    var fStatus = filterForm.querySelector(".issue-filter__status");
    var fCatBtns = [].slice.call(
      filterForm.querySelectorAll(".issue-filter__cat")
    );
    var fCtrlBtns = [].slice.call(
      filterForm.querySelectorAll(".issue-filter__ctrl")
    );

    // Filterable items: precompute lowercased text + owning section id.
    var fItems = [].slice.call(document.querySelectorAll(".item")).map(function (
      el
    ) {
      var sec = el.closest(".section");
      return {
        el: el,
        text: (el.textContent || "").toLowerCase(),
        sec: sec ? sec.id : null,
      };
    });
    // Only the toggle-backed sections are hide-eligible (highlights/refs never).
    var fSections = fCatBtns.map(function (b) {
      return document.getElementById(b.getAttribute("data-cat"));
    }).filter(Boolean);

    var fQuery = "";
    var fActive = {}; // section id -> on/off
    fCatBtns.forEach(function (b) {
      fActive[b.getAttribute("data-cat")] = true;
    });
    var fTotal = fItems.length;

    var apply = function () {
      var visible = 0;
      fItems.forEach(function (it) {
        var catOn = it.sec ? fActive[it.sec] !== false : true;
        var hit = catOn && (fQuery === "" || it.text.indexOf(fQuery) !== -1);
        it.el.classList.toggle("is-hidden", !hit);
        if (hit) visible++;
      });
      fSections.forEach(function (sec) {
        var any = sec.querySelector(".item:not(.is-hidden)");
        sec.classList.toggle("is-hidden", !any);
      });
      if (fStatus) {
        if (visible === 0) {
          fStatus.textContent =
            "无匹配项 · No matching items — 点 全部 恢复 / tap All to reset";
        } else {
          fStatus.textContent =
            visible + " / " + fTotal + " 项 · " + visible + " / " + fTotal + " items";
        }
      }
      filterForm.classList.toggle("is-empty", visible === 0);
    };

    // Bulk toggle every category on/off (used by the All / Clear controls).
    var setAllCats = function (on) {
      fCatBtns.forEach(function (b) {
        var cat = b.getAttribute("data-cat");
        fActive[cat] = on;
        b.classList.toggle("is-on", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
      });
    };

    var raf = 0;
    if (fInput) {
      fInput.addEventListener("input", function () {
        if (raf) window.cancelAnimationFrame(raf);
        raf = window.requestAnimationFrame(function () {
          fQuery = fInput.value.trim().toLowerCase();
          apply();
        });
      });
      fInput.addEventListener("keydown", function (e) {
        if (e.key === "Escape" || e.keyCode === 27) {
          fInput.value = "";
          fQuery = "";
          apply();
        }
      });
    }

    // "/" accelerator: jump to search (skip when already typing in a field).
    document.addEventListener("keydown", function (e) {
      if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
      var t = e.target;
      var tag = t && t.tagName ? t.tagName.toLowerCase() : "";
      if (
        tag === "input" || tag === "textarea" || tag === "select" ||
        (t && t.isContentEditable)
      ) return;
      if (!fInput) return;
      e.preventDefault();
      fInput.focus();
      fInput.select();
    });
    fCatBtns.forEach(function (b) {
      b.addEventListener("click", function () {
        var cat = b.getAttribute("data-cat");
        var next = !(fActive[cat] !== false);
        fActive[cat] = next;
        b.classList.toggle("is-on", next);
        b.setAttribute("aria-pressed", next ? "true" : "false");
        apply();
      });
    });
    // Roving arrow-key focus across the category pills (toggling stays native).
    fCatBtns.forEach(function (b, i) {
      b.addEventListener("keydown", function (e) {
        var k = e.key, next = -1;
        if (k === "ArrowRight" || k === "ArrowDown") next = (i + 1) % fCatBtns.length;
        else if (k === "ArrowLeft" || k === "ArrowUp") next = (i - 1 + fCatBtns.length) % fCatBtns.length;
        else if (k === "Home") next = 0;
        else if (k === "End") next = fCatBtns.length - 1;
        else return;
        e.preventDefault();
        fCatBtns[next].focus();
      });
    });
    fCtrlBtns.forEach(function (b) {
      b.addEventListener("click", function () {
        setAllCats(b.getAttribute("data-act") === "all");
        apply();
      });
    });

    apply();
  }

  /* ---- Per-item copy-link: deep-link share (progressive enhancement) ------ */
  var copyBtns = [].slice.call(document.querySelectorAll(".item__copy"));
  if (copyBtns.length) {
    var hasClipboard =
      navigator.clipboard && typeof navigator.clipboard.writeText === "function";

    // execCommand fallback for browsers without async clipboard (or non-secure
    // contexts). Returns true on success.
    var legacyCopy = function (str) {
      var ta = document.createElement("textarea");
      ta.value = str;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.top = "-9999px";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      var ok = false;
      try {
        ta.select();
        ok = document.execCommand("copy");
      } catch (e) {
        ok = false;
      }
      document.body.removeChild(ta);
      return ok;
    };

    var flashCopied = function (btn) {
      var txt = btn.querySelector(".item__copy-txt");
      var orig = txt ? txt.textContent : "";
      if (txt) txt.textContent = "已复制";
      btn.classList.add("is-copied");
      window.setTimeout(function () {
        if (txt) txt.textContent = orig;
        btn.classList.remove("is-copied");
      }, 1500);
    };

    copyBtns.forEach(function (btn) {
      btn.removeAttribute("hidden"); // activate (gated for non-JS users)
      btn.addEventListener("click", function () {
        var anchor = btn.getAttribute("data-anchor");
        if (!anchor) return;
        var url = location.origin + location.pathname + "#" + anchor;
        // Reflect the deep link in the URL bar without yanking scroll position.
        try {
          history.replaceState(null, "", "#" + anchor);
        } catch (e) {}
        if (hasClipboard) {
          navigator.clipboard.writeText(url).then(
            function () {
              flashCopied(btn);
            },
            function () {
              if (legacyCopy(url)) flashCopied(btn);
            }
          );
        } else {
          if (legacyCopy(url)) flashCopied(btn);
        }
      });
    });
  }

  /* ---- Reading progress -------------------------------------------------- */
  var bar = document.getElementById("readbar");
  if (bar) {
    var ticking = false;
    var update = function () {
      var h = document.documentElement;
      var max = h.scrollHeight - h.clientHeight;
      var y = h.scrollTop || window.pageYOffset || 0;
      var pct = max > 0 ? y / max : 0;
      bar.style.width = Math.max(0, Math.min(1, pct)) * 100 + "%";
      ticking = false;
    };
    window.addEventListener(
      "scroll",
      function () {
        if (!ticking) {
          ticking = true;
          window.requestAnimationFrame(update);
        }
      },
      { passive: true }
    );
    window.addEventListener("resize", update, { passive: true });
    update();
  }

  /* ---- Site-wide search (masthead #site-search + any rail [data-site-search]) */
  /* Shared across landing + issue pages. There may now be MORE THAN ONE search
     input on a page (the masthead magnifier AND a search box inside the right
     page rail). They all share ONE lazily-fetched assets/search-index.json (a
     flat array of {title,desc,section,date,url}); each input owns its own
     results dropdown so opening one doesn't disturb the other. Live-filter as
     the user types (debounced); each row links to <date>.html#item-N. Keyboard:
     ArrowUp/Down move the active row, Enter opens it, Esc clears/closes. Closes
     on outside click. Pure substring/token matching — no libs.
     Fully independent of the in-issue category filter above. */
  (function siteSearch() {
    var inputs = [].slice.call(
      document.querySelectorAll("#site-search, [data-site-search]")
    );
    if (!inputs.length) return;

    var MAX_RESULTS = 12;

    // ---- shared index (loaded once, reused by every search input) ----------
    var idx = null;
    var loading = false;
    var loaded = false;
    var pending = []; // callbacks waiting on the in-flight load

    var loadIndex = function (then) {
      if (loaded) {
        if (then) then();
        return;
      }
      if (then) pending.push(then);
      if (loading) return;
      loading = true;
      var done = function (data) {
        loaded = true;
        loading = false;
        if (data && data.length) {
          for (var i = 0; i < data.length; i++) {
            var it = data[i];
            it._hay = (
              (it.title || "") + " " + (it.desc || "") + " " + (it.section || "")
            ).toLowerCase();
          }
          idx = data;
        } else {
          idx = [];
        }
        var cbs = pending.slice();
        pending = [];
        cbs.forEach(function (cb) {
          try { cb(); } catch (e) {}
        });
      };
      try {
        if (window.fetch) {
          window
            .fetch("assets/search-index.json", { credentials: "same-origin" })
            .then(function (r) { return r.ok ? r.json() : []; })
            .then(function (d) { done(d || []); })
            ["catch"](function () { done([]); });
        } else {
          var xhr = new XMLHttpRequest();
          xhr.open("GET", "assets/search-index.json", true);
          xhr.onreadystatechange = function () {
            if (xhr.readyState === 4) {
              var d = [];
              try { d = JSON.parse(xhr.responseText) || []; } catch (e) { d = []; }
              done(d);
            }
          };
          xhr.send();
        }
      } catch (e) {
        done([]);
      }
    };

    var esc = function (s) {
      return String(s == null ? "" : s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    };

    var matchItem = function (it, tokens) {
      var hay = it._hay;
      for (var i = 0; i < tokens.length; i++) {
        if (hay.indexOf(tokens[i]) === -1) return false;
      }
      return true;
    };

    // ---- per-input widget (one dropdown each, sharing the index) -----------
    var wireOne = function (siteInput, n) {
      var searchWrap =
        siteInput.closest(".masthead__search") ||
        siteInput.closest(".rail-search") ||
        siteInput.parentNode;
      var siteToggle = siteInput.id === "site-search"
        ? document.getElementById("site-search-toggle")
        : null;
      var panelId = "site-search-panel-" + n;

      var panel = document.createElement("div");
      panel.className = "site-search__panel";
      panel.id = panelId;
      panel.setAttribute("role", "listbox");
      panel.setAttribute("aria-label", "搜索结果 · Search results");
      panel.hidden = true;
      searchWrap.appendChild(panel);
      siteInput.setAttribute("role", "combobox");
      siteInput.setAttribute("aria-autocomplete", "list");
      siteInput.setAttribute("aria-expanded", "false");
      siteInput.setAttribute("aria-controls", panelId);

      var results = [];
      var rows = [];
      var active = -1;

      var setExpanded = function (open) {
        panel.hidden = !open;
        siteInput.setAttribute("aria-expanded", open ? "true" : "false");
        if (searchWrap.classList) {
          searchWrap.classList.toggle("is-results-open", open);
        }
        if (!open) {
          active = -1;
          siteInput.removeAttribute("aria-activedescendant");
        }
      };
      var closePanel = function () { setExpanded(false); };

      var render = function () {
        rows = [];
        active = -1;
        siteInput.removeAttribute("aria-activedescendant");
        if (!results.length) {
          panel.innerHTML =
            '<p class="site-search__empty">无匹配项 · No matches</p>';
          return;
        }
        var html = "";
        for (var i = 0; i < results.length; i++) {
          var r = results[i];
          html +=
            '<a class="site-search__row" id="' + panelId + '-row-' + i + '" ' +
            'role="option" aria-selected="false" href="' + esc(r.url) + '">' +
            '<span class="site-search__row-title">' + esc(r.title) + "</span>" +
            '<span class="site-search__row-meta">' +
            '<span class="site-search__row-sec">' + esc(r.section) + "</span>" +
            '<span class="site-search__row-date">' + esc(r.date) + "</span>" +
            "</span>" +
            (r.desc
              ? '<span class="site-search__row-desc">' + esc(r.desc) + "</span>"
              : "") +
            "</a>";
        }
        panel.innerHTML = html;
        rows = [].slice.call(panel.querySelectorAll(".site-search__row"));
      };

      var doSearch = function () {
        var q = siteInput.value.trim().toLowerCase();
        if (!q) {
          results = [];
          closePanel();
          return;
        }
        if (!idx) {
          results = [];
          panel.innerHTML =
            '<p class="site-search__empty">载入中 · Loading…</p>';
          setExpanded(true);
          return;
        }
        var tokens = q.split(/\s+/).filter(Boolean);
        var out = [];
        for (var i = 0; i < idx.length && out.length < MAX_RESULTS; i++) {
          if (matchItem(idx[i], tokens)) out.push(idx[i]);
        }
        results = out;
        render();
        setExpanded(true);
      };

      var raf = 0;
      var debounced = function () {
        if (raf) window.clearTimeout(raf);
        raf = window.setTimeout(doSearch, 130);
      };

      var setActive = function (k) {
        if (!rows.length) return;
        if (k < 0) k = rows.length - 1;
        if (k >= rows.length) k = 0;
        rows.forEach(function (el, i) {
          var on = i === k;
          el.classList.toggle("is-active", on);
          el.setAttribute("aria-selected", on ? "true" : "false");
        });
        active = k;
        var el = rows[active];
        if (el) {
          siteInput.setAttribute("aria-activedescendant", el.id);
          if (el.scrollIntoView) el.scrollIntoView({ block: "nearest" });
        }
      };

      siteInput.addEventListener("focus", function () {
        loadIndex(function () {
          if (siteInput.value.trim()) doSearch();
        });
      });
      siteInput.addEventListener("input", function () {
        loadIndex();
        debounced();
      });
      siteInput.addEventListener("keydown", function (e) {
        var key = e.key;
        if (key === "Escape" || e.keyCode === 27) {
          if (panel.hidden && !siteInput.value) {
            siteInput.blur();
          } else {
            siteInput.value = "";
            results = [];
            closePanel();
          }
          return;
        }
        if (panel.hidden) return;
        if (key === "ArrowDown" || e.keyCode === 40) {
          e.preventDefault();
          setActive(active + 1);
        } else if (key === "ArrowUp" || e.keyCode === 38) {
          e.preventDefault();
          setActive(active - 1);
        } else if (key === "Enter" || e.keyCode === 13) {
          if (active >= 0 && rows[active]) {
            e.preventDefault();
            window.location.href = rows[active].getAttribute("href");
          }
        }
      });

      if (siteToggle) {
        siteToggle.addEventListener("click", function () {
          var open = siteToggle.getAttribute("aria-expanded") !== "true";
          siteToggle.setAttribute("aria-expanded", open ? "true" : "false");
          if (open) {
            loadIndex();
            siteInput.focus();
          } else {
            closePanel();
          }
        });
      }

      document.addEventListener("click", function (e) {
        if (!searchWrap.contains(e.target)) closePanel();
      });
      siteInput.addEventListener("mousedown", function () {
        if (siteInput.value.trim() && results.length) setExpanded(true);
      });
    };

    inputs.forEach(wireOne);
  })();
})();
