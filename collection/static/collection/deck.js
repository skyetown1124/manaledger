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
