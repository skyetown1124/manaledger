// Theme toggle (saved in this browser) and card-name suggestions.
(function () {
  const root = document.documentElement;
  const button = document.getElementById("theme-toggle");

  function label() {
    if (!button) return;
    const light = root.dataset.theme === "light";
    button.querySelector("span").textContent = light ? "Dark mode" : "Light mode";
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

  // Suggest card names from Scryfall as you type on the Add Cards page.
  const input = document.querySelector("[data-autocomplete]");
  const list = document.getElementById("name-suggestions");
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
