// Theme toggle (saved in this browser) and card-name suggestions.
(function () {
  const root = document.documentElement;
  const button = document.getElementById("theme-toggle");

  function label() {
    if (!button) return;
    const light = root.dataset.theme === "light";
    button.querySelector(".nav-label, span").textContent = light ? "Dark mode" : "Light mode";
  }

  if (button) {
    button.addEventListener("click", function () {
      const light = root.dataset.theme !== "light";
      if (light) root.dataset.theme = "light"; else delete root.dataset.theme;
      try { localStorage.setItem("ml-theme", light ? "light" : "dark"); } catch (e) {}
      label();
      document.dispatchEvent(new Event("themechange"));
    });
    label();
  }

  // Side menu: slides in on phones, shrinks to icons on wider screens (remembered).
  const nav = document.getElementById("sidenav");
  const openBtn = document.querySelector(".nav-open");
  const backdrop = document.querySelector(".nav-backdrop");
  function setOpen(open) {
    document.body.classList.toggle("nav-open", open);
    if (backdrop) backdrop.hidden = !open;
    if (openBtn) openBtn.setAttribute("aria-expanded", String(open));
    if (open && nav) { const first = nav.querySelector(".nav-item.active, .nav-item"); if (first) first.focus(); }
    else if (openBtn && document.activeElement && nav && nav.contains(document.activeElement)) openBtn.focus();
  }
  if (nav) {
    if (openBtn) openBtn.addEventListener("click", () => setOpen(true));
    if (backdrop) backdrop.addEventListener("click", () => setOpen(false));
    const closeBtn = nav.querySelector(".nav-close");
    if (closeBtn) closeBtn.addEventListener("click", () => setOpen(false));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && document.body.classList.contains("nav-open")) setOpen(false); });
    const collapse = nav.querySelector(".nav-collapse");
    const collapseLabel = () => {
      const mini = root.dataset.nav === "mini";
      collapse.setAttribute("aria-label", mini ? "Show menu labels" : "Shrink menu to icons");
      collapse.title = collapse.getAttribute("aria-label");
      collapse.setAttribute("aria-pressed", String(mini));
    };
    if (collapse) {
      collapse.addEventListener("click", () => {
        const mini = root.dataset.nav !== "mini";
        if (mini) root.dataset.nav = "mini"; else delete root.dataset.nav;
        try { localStorage.setItem("ml-nav", mini ? "mini" : "full"); } catch (e) {}
        collapseLabel();
      });
      collapseLabel();
    }
  }

  // Suggest card names from Scryfall as you type on the Add Cards page.
  const input = document.querySelector("[data-autocomplete]");
  const list = input && document.getElementById(input.getAttribute("list"));
  if (input && list) {
    let timer = null;
    let lastQuery = "";
    input.addEventListener("input", function () {
      clearTimeout(timer);
      const q = input.value.trim();
      if (q.length < 2 || q === lastQuery) return;
      timer = setTimeout(async function () {
        lastQuery = q;
        try {
          const resp = await fetch(input.dataset.autocomplete + "?q=" + encodeURIComponent(q));
          const data = await resp.json();
          list.replaceChildren(...data.names.map(function (name) {
            const opt = document.createElement("option");
            opt.value = name;
            return opt;
          }));
        } catch (e) { /* suggestions are optional */ }
      }, 250);
    });
  }
})();

// Keep your place: after a button that saves something (Add, Save, +, Remove...), the page
// reloads; put it back where you were scrolled instead of at the top.
(function () {
  const KEY = "manaledger.scroll";
  document.addEventListener("submit", function (ev) {
    const form = ev.target;
    if ((form.method || "").toLowerCase() !== "post" || form.target) return;
    try {
      sessionStorage.setItem(KEY, JSON.stringify({ path: location.pathname, y: window.scrollY, t: Date.now() }));
    } catch (e) { /* storage blocked: the page just starts at the top */ }
  }, true);
  let saved = null;
  try {
    saved = JSON.parse(sessionStorage.getItem(KEY) || "null");
    sessionStorage.removeItem(KEY);
  } catch (e) { saved = null; }
  if (!saved || saved.path !== location.pathname || Date.now() - saved.t > 15000 || location.hash) return;
  if ("scrollRestoration" in history) history.scrollRestoration = "manual";
  const restore = function () { window.scrollTo(0, saved.y); };
  restore();
  window.addEventListener("load", restore);   // again once images have their size
})();
