// Dashboard charts (Chart.js + the datalabels plugin).
// The numbers come from the <script id="chart-data"> block that Django writes.
(function () {
  const data = JSON.parse(document.getElementById("chart-data").textContent);
  const PALETTE = ["#2f7ed8", "#f26b38", "#1fb57b", "#e5484d", "#f0a500", "#e66fa0", "#1b8a3b", "#8b5cf6", "#14b8c4", "#a3a3a3"];
  const charts = {};
  let topN = 10;
  let typeMeasure = "value";

  const money = (v) => "$" + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const moneyAxis = (v) => (Math.abs(v) >= 1000 ? "$" + (v / 1000).toLocaleString(undefined, { maximumFractionDigits: 1 }) + "k" : "$" + v);
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  function theme() {
    return { text: css("--text"), muted: css("--muted"), grid: css("--line"), panel: css("--panel") };
  }

  function baseOptions(t, extra) {
    return Object.assign({
      responsive: true,
      maintainAspectRatio: false,
      animation: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? false : { duration: 400 },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (ctx) => " " + money(ctx.parsed.y ?? ctx.parsed.x ?? ctx.parsed) } },
        datalabels: { color: t.muted, font: { size: 10 } },
      },
    }, extra);
  }

  function axis(t, title, isMoney) {
    return {
      grid: { color: t.grid },
      border: { color: t.grid },
      ticks: Object.assign({ color: t.muted, font: { size: 11 } }, isMoney ? { callback: moneyAxis } : {}),
      title: title ? { display: true, text: title, color: t.text, font: { size: 12 } } : undefined,
    };
  }

  function make(id, config) {
    if (charts[id]) charts[id].destroy();
    const el = document.getElementById(id);
    if (el) charts[id] = new Chart(el, config);
  }

  function overTime(t) {
    const d = data.over_time;
    const labels = d.dates.map((s) => new Date(s + "T12:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" }));
    make("chart-over-time", {
      type: "bar",
      data: {
        labels,
        datasets: [
          { type: "bar", label: "Value", data: d.values, backgroundColor: d.values.map((_, i) => PALETTE[i % 3]), maxBarThickness: 70,
            datalabels: { anchor: "end", align: "end", formatter: (v) => money(v) } },
          { type: "line", label: "Trend", data: d.trend, borderColor: t.text, borderDash: [6, 5], borderWidth: 1.5, pointRadius: 0,
            datalabels: { display: false }, hidden: d.values.length < 2 },
        ],
      },
      options: baseOptions(t, {
        layout: { padding: { top: 18 } },
        plugins: {
          legend: { display: d.values.length >= 2, position: "top", align: "end", labels: { color: t.muted, boxHeight: 1, font: { size: 11 }, filter: (i) => i.text === "Trend" } },
          tooltip: { callbacks: { label: (ctx) => " " + ctx.dataset.label + ": " + money(ctx.parsed.y) } },
          datalabels: { color: t.muted, font: { size: 10 } },
        },
        scales: { x: axis(t, "Day"), y: Object.assign(axis(t, "Value ($)", true), { beginAtZero: true }) },
      }),
    });
  }

  function verticalBars(id, rows, t, xTitle) {
    make(id, {
      type: "bar",
      data: {
        labels: rows.map((r) => r.label),
        datasets: [{ data: rows.map((r) => r.value), backgroundColor: rows.map((_, i) => PALETTE[i % PALETTE.length]) }],
      },
      options: baseOptions(t, {
        layout: { padding: { top: 18 } },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (ctx) => " " + money(ctx.parsed.y) + " (" + rows[ctx.dataIndex].pct + "%)" } },
          datalabels: { anchor: "end", align: "end", color: t.muted, font: { size: 10 }, formatter: (v) => money(v) },
        },
        scales: {
          x: Object.assign(axis(t, xTitle), { ticks: { color: t.muted, font: { size: 10 }, maxRotation: 40, autoSkip: false,
            callback: function (v) { const s = this.getLabelForValue(v); return s.length > 18 ? s.slice(0, 17) + "…" : s; } } }),
          y: Object.assign(axis(t, "Value ($)", true), { beginAtZero: true }),
        },
      }),
    });
  }

  function horizontalBars(id, rows, t, opts) {
    const measure = (opts && opts.measure) || "value";
    const values = rows.map((r) => (measure === "copies" ? r.copies : r.value));
    const total = values.reduce((a, b) => a + b, 0) || 1;
    make(id, {
      type: "bar",
      data: {
        labels: rows.map((r) => r.label),
        datasets: [{ data: values, backgroundColor: rows.map((r, i) => r.swatch || PALETTE[i % PALETTE.length]), maxBarThickness: 34 }],
      },
      options: baseOptions(t, {
        indexAxis: "y",
        layout: { padding: { right: 110 } },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (ctx) => " " + (measure === "copies" ? ctx.parsed.x + " copies" : money(ctx.parsed.x)) } },
          datalabels: {
            anchor: "end", align: "end", clamp: true, color: t.muted, font: { size: 10 },
            formatter: (v) => (measure === "copies" ? v : money(v)) + " (" + (100 * v / total).toFixed(1) + "%)",
          },
        },
        scales: {
          x: Object.assign(axis(t, measure === "copies" ? "Copies" : "Value ($)", measure !== "copies"), { beginAtZero: true }),
          y: { grid: { display: false }, border: { color: t.grid }, ticks: { color: t.text, font: { size: 11 },
            callback: function (v) { const s = this.getLabelForValue(v); return s.length > 28 ? s.slice(0, 27) + "…" : s; } } },
        },
      }),
    });
  }

  // Draw the total in the middle of the donut.
  const centerText = {
    id: "centerText",
    afterDraw(chart, _args, opts) {
      if (!opts || !opts.lines) return;
      const { ctx, chartArea } = chart;
      const meta = chart.getDatasetMeta(0);
      if (!meta.data.length) return;
      const { x, y } = meta.data[0];
      ctx.save();
      ctx.textAlign = "center";
      ctx.fillStyle = opts.color;
      ctx.font = "600 15px " + getComputedStyle(document.body).fontFamily;
      ctx.fillText(opts.lines[0], x, y + 2);
      ctx.font = "12px " + getComputedStyle(document.body).fontFamily;
      ctx.fillText(opts.lines[1], x, y + 19);
      ctx.restore();
    },
  };

  function donut(t) {
    const rows = data.by_color;
    const total = rows.reduce((a, r) => a + r.value, 0);
    make("chart-by-color", {
      type: "doughnut",
      plugins: [centerText],
      data: {
        labels: rows.map((r) => r.label),
        datasets: [{ data: rows.map((r) => r.value), backgroundColor: rows.map((r) => r.swatch), borderColor: t.panel, borderWidth: 2 }],
      },
      options: baseOptions(t, {
        cutout: "42%",
        plugins: {
          legend: { display: true, position: "right", labels: { color: t.text, boxWidth: 10, boxHeight: 10, font: { size: 11 } } },
          tooltip: { callbacks: { label: (ctx) => " " + money(ctx.parsed) + " (" + rows[ctx.dataIndex].pct + "%)" } },
          datalabels: {
            color: "#10140c", font: { size: 10, weight: "600" },
            formatter: (v) => (v / total >= 0.06 ? (100 * v / total).toFixed(1) + "%" : ""),
          },
          centerText: { color: t.text, lines: [money(total), "total"] },
        },
      }),
    });
  }

  function topCards(t) {
    const rows = data.top_cards.slice(0, topN);
    document.getElementById("top-cards-wrap").style.height = Math.max(200, rows.length * 34 + 60) + "px";
    horizontalBars("chart-top-cards", rows, t);
  }

  function byType(t) {
    document.getElementById("type-title").textContent =
      typeMeasure === "copies" ? "Where my copies sit, by card type" : "Where my value sits, by card type";
    horizontalBars("chart-by-type", data.by_type, t, { measure: typeMeasure });
  }

  function renderAll() {
    const t = theme();
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    overTime(t);
    verticalBars("chart-by-set", data.by_set, t, "Set");
    donut(t);
    horizontalBars("chart-by-rarity", data.by_rarity, t);
    topCards(t);
    byType(t);
  }

  Chart.register(ChartDataLabels);
  renderAll();

  document.querySelectorAll('input[name="topn"]').forEach((r) =>
    r.addEventListener("change", () => { topN = Number(r.value); topCards(theme()); }));
  document.querySelectorAll('input[name="typemeasure"]').forEach((r) =>
    r.addEventListener("change", () => { typeMeasure = r.value; byType(theme()); }));
  document.addEventListener("themechange", renderAll);
})();
