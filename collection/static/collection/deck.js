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
})();
