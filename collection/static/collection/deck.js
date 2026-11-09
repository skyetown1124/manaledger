// Deck page: mana curve chart and the "Copy deck list" button.
(function () {
  const data = JSON.parse(document.getElementById("deck-data").textContent);
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  let chart = null;

  function drawCurve() {
    const el = document.getElementById("chart-curve");
    if (!el || typeof Chart === "undefined") return;
    if (chart) chart.destroy();
    const muted = css("--muted"), grid = css("--line"), text = css("--text");
    chart = new Chart(el, {
      type: "bar",
      data: {
        labels: data.curve.labels,
        datasets: [{ data: data.curve.values, backgroundColor: css("--accent"), maxBarThickness: 46 }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? false : { duration: 300 },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { title: (items) => "Mana value " + items[0].label, label: (ctx) => " " + ctx.parsed.y + " cards" } },
        },
        scales: {
          x: { grid: { display: false }, border: { color: grid }, ticks: { color: muted },
               title: { display: true, text: "Mana value", color: text, font: { size: 12 } } },
          y: { beginAtZero: true, grid: { color: grid }, border: { color: grid },
               ticks: { color: muted, precision: 0 }, title: { display: true, text: "Cards", color: text, font: { size: 12 } } },
        },
      },
    });
  }

  drawCurve();
  document.addEventListener("themechange", drawCurve);

  const copyButton = document.getElementById("copy-export");
  const box = document.getElementById("export-text");
  const status = document.getElementById("copy-status");
  if (copyButton && box) {
    copyButton.addEventListener("click", async function () {
      try {
        await navigator.clipboard.writeText(box.value);
        status.textContent = "Copied.";
      } catch (e) {
        box.focus();
        box.select();
        status.textContent = "Press Ctrl+C (or Cmd+C) to copy.";
      }
    });
  }

  // Shopping list copy button.
  document.querySelectorAll("[data-copy]").forEach(function (button) {
    const target = document.getElementById(button.dataset.copy);
    const note = document.getElementById("buy-status");
    button.addEventListener("click", async function () {
      try {
        await navigator.clipboard.writeText(target.value);
        if (note) note.textContent = "Copied.";
      } catch (e) {
        target.focus();
        target.select();
        if (note) note.textContent = "Press Ctrl+C (or Cmd+C) to copy.";
      }
    });
  });
})();

// Library search on the deck page: results update as you type, without reloading the page.
(function () {
  const form = document.getElementById("library-search");
  const box = document.getElementById("library-results");
  if (!form || !box) return;
  const input = form.querySelector('input[name="q"]');
  const status = document.getElementById("library-status");
  let timer = null;
  let controller = null;

  function query() {
    const data = new FormData(form);
    // A ticked "fits" box is sent after its hidden 0; keep the last value only.
    const params = new URLSearchParams();
    for (const [k, v] of data.entries()) params.set(k, v);
    return params;
  }

  async function search() {
    const params = query();
    const pageUrl = location.pathname + "?" + params.toString() + "#library";
    params.set("partial", "library");
    if (controller) controller.abort();
    controller = new AbortController();
    if (status) status.textContent = "Searching…";
    try {
      const resp = await fetch(location.pathname + "?" + params.toString(), {
        headers: { "X-Requested-With": "fetch" }, signal: controller.signal, credentials: "same-origin",
      });
      if (!resp.ok) throw new Error(resp.status);
      box.innerHTML = await resp.text();
      history.replaceState(null, "", pageUrl);
      if (status) status.textContent = "";
    } catch (e) {
      if (e.name === "AbortError") return;
      if (status) status.textContent = "Couldn't search; press Search to try again.";
    }
  }

  input.addEventListener("input", function () {
    clearTimeout(timer);
    timer = setTimeout(search, 250);
  });
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    clearTimeout(timer);
    search();
  });
  form.querySelectorAll('input[type="checkbox"]').forEach(function (cb) {
    cb.removeAttribute("onchange");
    cb.addEventListener("change", search);
  });
})();

// Keep checkboxes (table view): live count of what the deck will be after removing
// the unticked cards. Unticked boxes are remembered in this browser until you remove them.
(function () {
  const form = document.getElementById("keep-form");
  const bar = document.getElementById("keep-bar");
  if (!form || !bar) return;
  const boxes = Array.from(document.querySelectorAll('input[name="keep"][form="keep-form"]'));
  const count = document.getElementById("keep-count");
  const submit = document.getElementById("keep-submit");
  const storeKey = "manaledger.keep." + location.pathname;

  function load() {
    try { return new Set(JSON.parse(localStorage.getItem(storeKey) || "[]")); } catch (e) { return new Set(); }
  }
  function save() {
    const off = boxes.filter((b) => !b.checked).map((b) => b.value);
    try { off.length ? localStorage.setItem(storeKey, JSON.stringify(off)) : localStorage.removeItem(storeKey); } catch (e) { /* not saved */ }
  }
  function update() {
    let total = 0, kept = 0, cutCards = 0, cutCopies = 0;
    boxes.forEach(function (b) {
      const row = b.closest("tr");
      const qty = parseInt(row.dataset.qty, 10) || 0;
      if (row.dataset.board !== "side") total += qty;
      if (b.checked) { if (row.dataset.board !== "side") kept += qty; }
      else { cutCards += 1; cutCopies += qty; }
      row.classList.toggle("cut", !b.checked);
    });
    count.textContent = cutCards
      ? `${total} cards → ${kept} after removing ${cutCopies} (${cutCards} unticked)`
      : `${total} cards · untick Keep on cards to cut`;
    submit.disabled = cutCards === 0;
    submit.textContent = cutCards ? `Remove ${cutCopies} unticked card${cutCopies === 1 ? "" : "s"}` : "Remove unticked cards";
  }

  const remembered = load();
  boxes.forEach(function (b) {
    if (remembered.has(b.value)) b.checked = false;
    b.addEventListener("change", function () { save(); update(); });
  });
  bar.querySelectorAll("[data-keep]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      const on = btn.dataset.keep === "all";
      boxes.forEach((b) => { b.checked = on; });
      save(); update();
    });
  });
  form.addEventListener("submit", function () {
    try { localStorage.removeItem(storeKey); } catch (e) { /* fine */ }
  });
  update();
})();
