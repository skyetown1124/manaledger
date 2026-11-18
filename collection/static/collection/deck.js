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
    // Advanced search can send several values for one field (two card types), so keep them all.
    const params = new URLSearchParams();
    for (const [k, v] of data.entries()) {
      if (k.startsWith("adv_")) { if (v !== "") params.append(k, v); } else params.set(k, v);
    }
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

  // "All cards" asks Scryfall, so wait a little longer for typing to pause.
  const allCards = () => (form.querySelector('input[name="src"]:checked') || {}).value === "all";
  input.addEventListener("input", function () {
    clearTimeout(timer);
    timer = setTimeout(search, allCards() ? 600 : 250);
  });
  // Advanced search fields update the results as you pick them, too.
  document.querySelectorAll('[form="library-search"][name^="adv_"]').forEach(function (el) {
    el.addEventListener(el.type === "text" || el.type === "number" ? "input" : "change", function () {
      clearTimeout(timer);
      timer = setTimeout(search, allCards() ? 700 : 300);
    });
  });
  form.querySelectorAll('input[name="src"]').forEach(function (radio) {
    radio.addEventListener("change", function () {
      const label = form.querySelector('label[for="lib-q"]');
      if (label) label.textContent = allCards() ? "Search all Magic cards" : "Search your library";
      clearTimeout(timer);
      search();
      input.focus();
    });
  });
  // "Not here? Search all cards" link in the results: switch to All cards without reloading.
  box.addEventListener("click", function (ev) {
    const link = ev.target.closest("[data-src-all]");
    if (!link) return;
    ev.preventDefault();
    const radio = form.querySelector('input[name="src"][value="all"]');
    if (radio) { radio.checked = true; radio.dispatchEvent(new Event("change")); }
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

// Select boxes: tick cards (or a section's "Select all"), then move, tag, proxy or remove them
// together with the bar at the bottom.
(function () {
  const form = document.getElementById("bulk-form");
  const bar = document.getElementById("bulk-bar");
  if (!form || !bar) return;
  const boxes = Array.from(document.querySelectorAll('input[name="sel"][form="bulk-form"]'));
  const count = document.getElementById("bulk-count");
  const actions = document.getElementById("bulk-actions");
  const clear = bar.querySelector("[data-bulk-clear]");

  function update() {
    let cards = 0, copies = 0;
    boxes.forEach(function (b) {
      const row = b.closest("[data-qty]");
      if (b.checked) { cards += 1; copies += parseInt(row.dataset.qty, 10) || 0; }
      row.classList.toggle("picked", b.checked);
    });
    count.textContent = cards
      ? `${cards} card${cards === 1 ? "" : "s"} selected (${copies} cop${copies === 1 ? "y" : "ies"})`
      : "Tick Select on cards to change several at once";
    actions.querySelectorAll("button, select, input").forEach((el) => { el.disabled = cards === 0; });
    clear.hidden = cards === 0;
    bar.classList.toggle("active", cards > 0);
  }
  boxes.forEach((b) => b.addEventListener("change", update));
  clear.addEventListener("click", function () { boxes.forEach((b) => { b.checked = false; }); update(); });

  // "Select all" in a section header: ticks every card in that section (or unticks if all are ticked).
  document.querySelectorAll("[data-select-group]").forEach(function (btn) {
    const section = btn.closest(".stk-group, .txt-group") ||
      (btn.closest(".group-title") && btn.closest(".group-title").nextElementSibling);
    if (!section) return;
    btn.addEventListener("click", function () {
      const inGroup = boxes.filter((b) => section.contains(b));
      const on = !inGroup.every((b) => b.checked);
      inGroup.forEach((b) => { b.checked = on; });
      update();
    });
  });

  form.addEventListener("submit", function (ev) {
    const action = ev.submitter && ev.submitter.value;
    const picked = boxes.filter((b) => b.checked);
    if (!picked.length) { ev.preventDefault(); return; }
    if (action === "tag" || action === "untag") {
      const tag = document.getElementById("bulk-tag");
      if (!tag.value.trim()) { ev.preventDefault(); tag.focus(); return; }
    }
  });
  update();
})();

// Export: switch formats, copy, or download as a .txt file.
(function () {
  const pick = document.getElementById("export-format");
  const box = document.getElementById("export-text");
  const data = document.getElementById("deck-exports");
  if (!pick || !box || !data) return;
  const texts = JSON.parse(data.textContent);
  const notes = {
    plain: "One line per card. Works almost anywhere, including store buy-list boxes.",
    arena: "Paste into MTG Arena: Decks → Import. Arena only knows cards that are on Arena.",
    mtgo: "Save as a .txt file and import it in MTGO. A Commander deck's commander goes in the sideboard.",
    moxfield: "Paste into Moxfield's import box. Tags come along as #tags.",
    archidekt: "Paste into Archidekt's import. Tags become categories; Maybeboard cards stay out of the deck.",
  };
  const note = document.getElementById("export-note");
  pick.addEventListener("change", function () {
    box.value = texts[pick.value] || "";
    if (note) note.textContent = notes[pick.value] || "";
  });
  const dl = document.getElementById("download-export");
  if (dl) dl.addEventListener("click", function () {
    const name = (dl.dataset.name || "deck").replace(/[\\/:*?"<>|]+/g, "").trim() || "deck";
    const blob = new Blob([box.value.replace(/\n/g, "\r\n")], { type: "text/plain" });
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `${name} (${pick.value}).txt` });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });
})();
