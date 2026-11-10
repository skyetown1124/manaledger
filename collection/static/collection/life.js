/* Life tracker page: setup screen, the table of player seats, and the menus.
 * The rules live in life-engine.js; this file only draws them and turns taps into actions.
 * The game in progress is kept in this browser (localStorage) so a reload doesn't lose it.
 */
(function () {
  "use strict";
  const L = window.LifeEngine;
  const CFG = window.LIFE_CONFIG || {};
  const DECKS = JSON.parse(document.getElementById("life-decks").textContent || "[]");
  const STORE = "manaledger.life.v1";
  const HOLD_MS = 450, REPEAT_MS = 380;

  // ------------------------------------------------------------ small helpers
  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else if (k === "style" && typeof v === "object") {
        for (const [prop, val] of Object.entries(v)) {
          if (prop.startsWith("--")) n.style.setProperty(prop, val);  // custom properties need setProperty
          else n.style[prop] = val;
        }
      }
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) n.append(kid);
    return n;
  }
  const signed = (n) => (n > 0 ? "+" : n < 0 ? "−" : "") + Math.abs(n);
  const cssUrl = (u) => `url("${String(u).replace(/["\\\n]/g, "")}")`;

  function readStore() {
    try { return JSON.parse(localStorage.getItem(STORE) || "null") || {}; } catch (e) { return {}; }
  }
  function writeStore() {
    try { localStorage.setItem(STORE, JSON.stringify({ state, setup, layout })); } catch (e) { /* private window: game just isn't kept */ }
  }

  // ------------------------------------------------------------ state
  const saved = readStore();
  let state = L.restore(saved.state);
  let past = [];
  let layout = saved.layout === "screen" ? "screen" : "table";
  let setup = saved.setup || null;
  if (!setup || !Array.isArray(setup.seats)) {
    setup = { count: 4, format: "commander", life: 30, cmdDamage: true, seats: [] };
  }
  while (setup.seats.length < 6) setup.seats.push({ name: "", deckId: "", commanders: [] });

  // A deck link (?deck=7) puts that deck in seat 1.
  if (CFG.startDeck) {
    const d = DECKS.find((x) => x.id === CFG.startDeck);
    if (d) setup.seats[0] = { name: setup.seats[0].name, deckId: String(d.id), commanders: d.commanders.slice() };
  }

  function act(action) {
    const next = L.apply(state, action);
    if (next === state) return;
    past.push(state);
    if (past.length > 200) past.shift();
    state = next;
    writeStore();
    update();
  }
  function undo() {
    if (!past.length) return;
    state = past.pop();
    writeStore();
    update();
  }

  // ------------------------------------------------------------ setup screen
  const setupEl = document.getElementById("setup");
  const tableEl = document.getElementById("table");
  const seatsEl = document.getElementById("seats");
  const names = document.getElementById("card-names");

  let acTimer = null;
  function autocomplete(text) {
    clearTimeout(acTimer);
    if (!CFG.autocompleteUrl || text.trim().length < 2) return;
    acTimer = setTimeout(() => {
      fetch(`${CFG.autocompleteUrl}?q=${encodeURIComponent(text)}`)
        .then((r) => r.json())
        .then((d) => names.replaceChildren(...(d.names || []).map((n) => el("option", { value: n }))))
        .catch(() => {});
    }, 220);
  }

  function lookupCommander(name) {
    return fetch(`${CFG.commanderUrl}?name=${encodeURIComponent(name)}`)
      .then((r) => r.json().then((d) => (r.ok ? d : Promise.reject(new Error(d.error || "Not found")))));
  }

  function seatCard(i) {
    const seat = setup.seats[i];
    const deckSel = el("select", { "aria-label": `Player ${i + 1} deck` },
      el("option", { value: "", text: "No deck (type a commander)" }),
      DECKS.map((d) => el("option", { value: d.id, selected: String(d.id) === String(seat.deckId) },
        d.name + (d.commanders.length ? " — " + d.commanders.map((c) => c.name.split(",")[0]).join(" & ") : ""))));
    const status = el("span", { class: "seat-status small muted", role: "status" });
    const preview = el("div", { class: "seat-preview" });
    const inputs = [0, 1].map((k) => el("input", {
      type: "text", list: "card-names", autocomplete: "off", spellcheck: "false",
      placeholder: k === 0 ? "Commander (any card name)" : "Partner / background (optional)",
      "aria-label": k === 0 ? `Player ${i + 1} commander` : `Player ${i + 1} partner`,
      value: (seat.commanders[k] && seat.commanders[k].name) || "",
    }));

    function drawPreview() {
      preview.replaceChildren(...seat.commanders.filter((c) => c && c.art)
        .map((c) => el("div", { class: "seat-art", style: { backgroundImage: cssUrl(c.art) }, title: c.name })));
      preview.classList.toggle("empty", !preview.children.length);
    }

    deckSel.addEventListener("change", () => {
      const d = DECKS.find((x) => String(x.id) === deckSel.value);
      seat.deckId = deckSel.value;
      seat.commanders = d ? d.commanders.slice() : [];
      inputs.forEach((inp, k) => { inp.value = (seat.commanders[k] && seat.commanders[k].name) || ""; });
      if (d && !nameInput.value) nameInput.placeholder = d.name;
      status.textContent = d && !d.isCommander && d.commanders.length ? "No commander in that deck: using its cover card." : "";
      drawPreview();
      writeStore();
    });
    inputs.forEach((inp, k) => {
      inp.addEventListener("input", () => autocomplete(inp.value));
      inp.addEventListener("change", () => {
        const v = inp.value.trim();
        if (!v) {
          seat.commanders = seat.commanders.filter((_, j) => j !== k);
          drawPreview(); writeStore();
          return;
        }
        if (seat.commanders[k] && seat.commanders[k].name === v) return;
        status.textContent = "Finding " + v + "…";
        lookupCommander(v).then((c) => {
          seat.commanders[k] = { name: c.name, art: c.art, image: c.image };
          seat.commanders = seat.commanders.filter(Boolean);
          inp.value = c.name;
          status.textContent = "";
          drawPreview(); writeStore();
        }).catch((err) => {
          seat.commanders[k] = { name: v, art: "", image: "" };
          seat.commanders = seat.commanders.filter(Boolean);
          status.textContent = `No picture found for "${v}" (${err.message}). It'll still be tracked.`;
          drawPreview(); writeStore();
        });
      });
    });
    const nameInput = el("input", {
      type: "text", maxlength: "24", placeholder: `Player ${i + 1}`, value: seat.name || "",
      "aria-label": `Player ${i + 1} name`, oninput: (e) => { seat.name = e.target.value; writeStore(); },
    });
    drawPreview();
    return el("div", { class: "seat-card", style: { "--seat": L.COLORS[i % L.COLORS.length] } },
      preview,
      el("div", { class: "seat-fields" },
        el("label", { class: "seat-label" }, el("span", { class: "dot" }), nameInput),
        deckSel, inputs[0], inputs[1], status));
  }

  function drawSetup() {
    document.querySelectorAll("#count [data-count]").forEach((b) => b.setAttribute("aria-checked", String(Number(b.dataset.count) === setup.count)));
    document.querySelectorAll("#layout [data-layout]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.layout === layout)));
    const fmt = document.getElementById("format");
    fmt.value = setup.format;
    document.getElementById("custom-life-wrap").hidden = setup.format !== "custom";
    document.getElementById("custom-life").value = setup.life;
    document.getElementById("cmd-damage").checked = setup.cmdDamage !== false;
    seatsEl.replaceChildren(...Array.from({ length: setup.count }, (_, i) => seatCard(i)));
    document.getElementById("resume").hidden = !state;
  }

  document.getElementById("count").addEventListener("click", (e) => {
    const b = e.target.closest("[data-count]");
    if (!b) return;
    setup.count = Number(b.dataset.count);
    drawSetup(); writeStore();
  });
  document.getElementById("layout").addEventListener("click", (e) => {
    const b = e.target.closest("[data-layout]");
    if (!b) return;
    layout = b.dataset.layout;
    drawSetup(); writeStore();
  });
  document.getElementById("format").addEventListener("change", (e) => {
    setup.format = e.target.value;
    setup.cmdDamage = L.FORMATS[setup.format].commanderDamage;
    drawSetup(); writeStore();
  });
  document.getElementById("custom-life").addEventListener("change", (e) => { setup.life = Number(e.target.value) || 30; writeStore(); });
  document.getElementById("cmd-damage").addEventListener("change", (e) => { setup.cmdDamage = e.target.checked; writeStore(); });
  document.getElementById("resume-btn").addEventListener("click", showTable);
  document.getElementById("start").addEventListener("click", () => {
    const players = setup.seats.slice(0, setup.count).map((s, i) => {
      const deck = DECKS.find((d) => String(d.id) === String(s.deckId));
      return { name: s.name || (deck ? deck.name : "") || `Player ${i + 1}`, deckId: s.deckId || null, commanders: s.commanders || [] };
    });
    state = L.newGame({ format: setup.format, life: setup.format === "custom" ? setup.life : undefined, commanderDamage: setup.cmdDamage, players });
    past = [];
    writeStore();
    showTable();
  });

  function showSetup() {
    tableEl.hidden = true;
    setupEl.hidden = false;
    document.body.classList.remove("playing");
    drawSetup();
    window.scrollTo(0, 0);
  }

  // ------------------------------------------------------------ the table
  let seatViews = {};
  let centerView = null;

  function seatOrder() {
    // Around a table the order runs clockwise: bottom row left to right, then the top row
    // right to left (it's upside down, so for those players it still reads left to right).
    const ids = state.players.map((p) => p.id);
    const topCount = Math.floor(ids.length / 2);
    const bottom = ids.slice(0, ids.length - topCount);
    const top = ids.slice(ids.length - topCount);
    if (layout === "table") return { top: top.reverse(), bottom };
    return { top: ids.slice(0, ids.length - topCount), bottom: ids.slice(ids.length - topCount) };
  }

  function showTable() {
    if (!state) return showSetup();
    setupEl.hidden = true;
    tableEl.hidden = false;
    document.body.classList.add("playing");
    buildTable();
    update();
    keepAwake();
  }

  function holdButton(btn, step, big) {
    let timer = null, repeat = null, held = false;
    const stop = () => { clearTimeout(timer); clearInterval(repeat); timer = repeat = null; };
    btn.addEventListener("pointerdown", (e) => {
      if (e.button !== undefined && e.button !== 0) return;
      held = false;
      stop();
      timer = setTimeout(() => {
        held = true;
        step(big);
        repeat = setInterval(() => step(big), REPEAT_MS);
      }, HOLD_MS);
    });
    ["pointerup", "pointerleave", "pointercancel"].forEach((ev) => btn.addEventListener(ev, stop));
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      if (held) { held = false; return; }  // a hold already counted
      step(1);
    });
    btn.addEventListener("contextmenu", (e) => e.preventDefault());
  }

  function buildSeat(id, flipped) {
    const p = L.player(state, id);
    const art = el("div", { class: "seat-bg" });
    const life = el("div", { class: "life", "aria-live": "polite" });
    const delta = el("div", { class: "delta", "aria-hidden": "true" });
    const minus = el("button", { type: "button", class: "tap minus", "aria-label": `${p.name}: lose 1 life (hold for 10)` }, el("span", { text: "−" }));
    const plus = el("button", { type: "button", class: "tap plus", "aria-label": `${p.name}: gain 1 life (hold for 10)` }, el("span", { text: "+" }));
    const nameBtn = el("button", { type: "button", class: "seat-name", onclick: () => openSheet(id, "player") });
    const sub = el("div", { class: "seat-sub" });
    const badges = el("div", { class: "badges" });
    const chips = el("div", { class: "chips" });
    const out = el("div", { class: "out", hidden: true });
    const inner = el("div", { class: "seat-inner" + (flipped ? " flipped" : "") },
      art, el("div", { class: "shade" }),
      el("header", { class: "seat-head" }, el("div", { class: "seat-title" }, nameBtn, sub), badges),
      el("div", { class: "life-area" }, minus, life, delta, plus),
      chips, out);
    const seat = el("section", { class: "seat", style: { "--seat": p.color }, "data-player": id }, inner);

    let pending = 0, pendingTimer = null;
    const change = (n) => {
      act({ type: "life", player: id, delta: n });
      pending += n;
      delta.textContent = signed(pending);
      delta.classList.toggle("down", pending < 0);
      delta.classList.add("show");
      clearTimeout(pendingTimer);
      pendingTimer = setTimeout(() => { pending = 0; delta.classList.remove("show"); }, 1800);
    };
    holdButton(minus, (n) => change(-n), 10);
    holdButton(plus, (n) => change(n), 10);
    seatViews[id] = { seat, inner, art, life, nameBtn, sub, badges, chips, out, flipped, artKey: "" };
    return seat;
  }

  function buildTable() {
    seatViews = {};
    const order = seatOrder();
    const row = (ids, flipped) => el("div", { class: "row", style: { "--n": ids.length } }, ids.map((id) => buildSeat(id, flipped)));
    const turnBtn = el("button", { type: "button", class: "turn", onclick: () => act({ type: "nextTurn" }), title: "Pass the turn" });
    const clock = el("span", { class: "clock", title: "Game time" });
    const dn = el("button", { type: "button", class: "daynight", onclick: () => act({ type: "dayNight" }), title: "Day / night" });
    centerView = { turnBtn, clock, dn };
    const center = el("div", { class: "center" },
      el("button", { type: "button", class: "round", "aria-label": "Undo", title: "Undo", onclick: undo, text: "↶" }),
      turnBtn, dn, clock,
      el("button", { type: "button", class: "round menu-btn", "aria-label": "Game menu", title: "Game menu", onclick: openMenu, text: "☰" }));
    tableEl.className = `table layout-${layout} players-${state.players.length}`;
    tableEl.replaceChildren(row(order.top, layout === "table"), center, row(order.bottom, false));
  }

  function setArt(view, p) {
    const key = p.commanders.map((c) => c.art).join("|");
    if (key === view.artKey) return;
    view.artKey = key;
    const arts = p.commanders.filter((c) => c.art);
    view.art.replaceChildren(...arts.map((c) => el("div", { class: "art", style: { backgroundImage: cssUrl(c.art) } })));
    view.art.classList.toggle("partners", arts.length > 1);
    view.art.classList.toggle("none", !arts.length);
  }

  function chip(text, cls, onclick, label) {
    return el("button", { type: "button", class: "chip " + (cls || ""), onclick, "aria-label": label || text, text });
  }

  function maxCommanderDamage(id) {
    let best = 0;
    for (const row of Object.values(state.damage[id] || {})) for (const n of row) best = Math.max(best, n);
    return best;
  }

  function update() {
    if (!state || tableEl.hidden) return;
    const winner = L.alive(state).length === 1 && state.players.length > 1 ? L.alive(state)[0].id : null;
    for (const p of state.players) {
      const v = seatViews[p.id];
      if (!v) continue;
      setArt(v, p);
      const lost = L.lossReasons(state, p.id);
      v.life.textContent = p.life;
      v.life.classList.toggle("low", p.life <= 10);
      v.nameBtn.textContent = p.name;
      v.nameBtn.setAttribute("aria-label", `${p.name}: counters and options`);
      v.sub.textContent = p.commanders.map((c) => c.name).join(" & ");
      v.seat.classList.toggle("active", state.active === p.id && !winner);
      v.seat.classList.toggle("is-out", lost.length > 0);

      const b = [];
      if (state.active === p.id && !winner) b.push(el("span", { class: "badge turn-badge", text: "Turn" }));
      if (state.monarch === p.id) b.push(el("span", { class: "badge", title: "The monarch", text: "👑" }));
      if (state.initiative === p.id) b.push(el("span", { class: "badge", title: "The initiative", text: "🏰" }));
      if (state.blessing.includes(p.id)) b.push(el("span", { class: "badge", title: "City's blessing", text: "🏙" }));
      v.badges.replaceChildren(...b);

      const c = [];
      c.push(chip(`☠ ${p.poison}`, p.poison ? (p.poison >= 7 ? "danger" : "on") : "dim", () => openSheet(p.id, "player"), `Poison: ${p.poison}`));
      if (state.commanderDamage && state.players.some((o) => o.id !== p.id && o.commanders.length)) {
        const m = maxCommanderDamage(p.id);
        c.push(chip(`⚔ ${m}`, m ? (m >= 15 ? "danger" : "on") : "dim", () => openSheet(p.id, "damage"), `Commander damage taken: most from one commander ${m}`));
      }
      for (const k of ["energy", "experience", "rad"]) {
        if (p[k]) c.push(chip({ energy: "⚡", experience: "★", rad: "☢" }[k] + " " + p[k], "on", () => openSheet(p.id, "player"), `${k}: ${p[k]}`));
      }
      p.commanders.forEach((cm, i) => {
        if (cm.casts) c.push(chip(`Tax +${L.commanderTax(cm)}`, "on", () => openSheet(p.id, "player"), `${cm.name}: commander tax ${L.commanderTax(cm)}`));
      });
      c.push(chip("•••", "dim more", () => openSheet(p.id, "player"), `${p.name}: counters and options`));
      v.chips.replaceChildren(...c);

      if (winner === p.id) {
        v.out.hidden = false;
        v.out.className = "out win";
        v.out.replaceChildren(el("strong", { text: "Winner!" }), el("button", { type: "button", class: "btn", text: "Rematch", onclick: rematch }));
      } else if (lost.length) {
        v.out.hidden = false;
        v.out.className = "out";
        v.out.replaceChildren(el("strong", { text: "Out" }), el("span", { text: lost.join(" · ") }),
          el("button", { type: "button", class: "btn", text: "Undo", onclick: undo }));
      } else {
        v.out.hidden = true;
      }
    }
    const active = L.player(state, state.active);
    centerView.turnBtn.replaceChildren(el("span", { class: "turn-n", text: `Turn ${state.turn}` }), el("span", { class: "turn-who", text: active ? active.name : "" }), el("span", { text: " ▸" }));
    centerView.turnBtn.style.setProperty("--seat", active ? active.color : "#888");
    centerView.dn.textContent = state.dayNight === "day" ? "☀" : state.dayNight === "night" ? "☾" : "☀/☾";
    centerView.dn.classList.toggle("dim", !state.dayNight);
    tickClock();
    if (sheet.open) sheet.refresh();
  }

  function tickClock() {
    if (!centerView || !state) return;
    const mins = Math.floor((Date.now() - state.startedAt) / 60000);
    centerView.clock.textContent = mins >= 60 ? `${Math.floor(mins / 60)}h ${String(mins % 60).padStart(2, "0")}m` : `${mins}m`;
  }
  setInterval(tickClock, 30000);

  // ------------------------------------------------------------ per-player sheet
  // Opens over the whole screen, turned the same way as the seat it belongs to.
  const sheet = { open: false, refresh: () => {} };
  const sheetEl = el("div", { class: "overlay", hidden: true, role: "dialog", "aria-modal": "true" });
  document.body.append(sheetEl);
  sheetEl.addEventListener("click", (e) => { if (e.target === sheetEl) closeSheet(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && sheet.open) closeSheet(); });

  function stepper(label, value, onMinus, onPlus, extra) {
    return el("div", { class: "stepper" },
      el("div", { class: "stepper-label" }, label, extra || null),
      el("button", { type: "button", class: "step", "aria-label": `${typeof label === "string" ? label : "value"} minus 1`, onclick: onMinus, text: "−" }),
      el("output", { class: "step-val", text: String(value) }),
      el("button", { type: "button", class: "step", "aria-label": `${typeof label === "string" ? label : "value"} plus 1`, onclick: onPlus, text: "+" }));
  }

  function toggle(label, on, onclick) {
    return el("button", { type: "button", class: "toggle" + (on ? " on" : ""), "aria-pressed": String(on), onclick, text: label });
  }

  function playerSheet(id) {
    const p = L.player(state, id);
    const body = [];
    const names = { poison: "☠ Poison", energy: "⚡ Energy", experience: "★ Experience", rad: "☢ Rad" };
    for (const k of L.COUNTERS) {
      body.push(stepper(names[k], p[k], () => act({ type: "counter", player: id, kind: k, delta: -1 }), () => act({ type: "counter", player: id, kind: k, delta: 1 }),
        k === "poison" ? el("small", { class: "muted", text: ` · ${L.LETHAL_POISON} is lethal` }) : null));
    }
    if (p.commanders.length) {
      body.push(el("h3", { text: "Commander tax" }));
      p.commanders.forEach((c, i) => body.push(stepper(c.name || "Commander", c.casts,
        () => act({ type: "cast", player: id, index: i, delta: -1 }), () => act({ type: "cast", player: id, index: i, delta: 1 }),
        el("small", { class: "muted", text: ` · cast ${c.casts}×, costs {${L.commanderTax(c)}} more` }))));
    }
    body.push(el("h3", { text: "Game" }));
    body.push(el("div", { class: "toggles" },
      toggle("👑 Monarch", state.monarch === id, () => act({ type: "monarch", player: id })),
      toggle("🏰 Initiative", state.initiative === id, () => act({ type: "initiative", player: id })),
      toggle("🏙 City's blessing", state.blessing.includes(id), () => act({ type: "blessing", player: id })),
      toggle("Their turn", state.active === id, () => act({ type: "setActive", player: id }))));
    if (state.commanderDamage && state.players.some((o) => o.id !== id && o.commanders.length)) {
      body.push(el("button", { type: "button", class: "btn wide", text: "⚔ Commander damage taken…", onclick: () => openSheet(id, "damage") }));
    }
    const lifeIn = el("input", { type: "number", value: p.life, min: "-999", max: "9999", "aria-label": "Life total" });
    const nameIn = el("input", { type: "text", value: p.name, maxlength: "24", "aria-label": "Player name" });
    body.push(el("h3", { text: "Fix-ups" }));
    body.push(el("div", { class: "fix" },
      el("label", {}, "Life ", lifeIn, el("button", { type: "button", class: "btn", text: "Set", onclick: () => act({ type: "setLife", player: id, value: lifeIn.value }) })),
      el("label", {}, "Name ", nameIn, el("button", { type: "button", class: "btn", text: "Rename", onclick: () => act({ type: "rename", player: id, name: nameIn.value }) }))));
    body.push(el("button", { type: "button", class: "btn wide " + (p.conceded ? "" : "danger"), text: p.conceded ? "Back in the game" : "Concede", onclick: () => act({ type: "concede", player: id }) }));
    return { title: p.name, body };
  }

  function damageSheet(id) {
    const p = L.player(state, id);
    const lifeToo = sheet.lifeToo !== false;
    const body = [el("p", { class: "muted small", text: `Combat damage dealt to ${p.name} by each commander. ${L.LETHAL_COMMANDER} from any one commander is lethal.` })];
    for (const o of state.players) {
      if (o.id === id) continue;
      o.commanders.forEach((c, i) => {
        const n = state.damage[id][o.id][i];
        body.push(el("div", { class: "dmg-row" + (n >= 15 ? " danger" : "") },
          el("div", { class: "dmg-art", style: c.art ? { backgroundImage: cssUrl(c.art) } : { background: o.color } }),
          stepper(el("span", {}, el("strong", { text: c.name || o.name }), el("small", { class: "muted", text: ` · ${o.name}` })), `${n}`,
            () => act({ type: "commanderDamage", target: id, source: o.id, index: i, delta: -1, lifeToo }),
            () => act({ type: "commanderDamage", target: id, source: o.id, index: i, delta: 1, lifeToo }))));
      });
    }
    body.push(el("label", { class: "inline" },
      el("input", { type: "checkbox", checked: lifeToo, onchange: (e) => { sheet.lifeToo = e.target.checked; sheet.refresh(); } }),
      " Also change their life total"));
    return { title: `${p.name}: commander damage taken`, body };
  }

  function openSheet(id, kind) {
    const v = seatViews[id];
    sheet.open = true;
    sheet.refresh = () => {
      const { title, body } = kind === "damage" ? damageSheet(id) : playerSheet(id);
      const p = L.player(state, id);
      const box = el("div", { class: "sheet-box", style: { "--seat": p.color } },
        el("header", { class: "sheet-head" }, el("h2", { text: title }), el("button", { type: "button", class: "x", "aria-label": "Close", text: "✕", onclick: closeSheet })),
        el("div", { class: "sheet-body" }, body));
      const scroll = sheetEl.firstChild && sheetEl.firstChild.querySelector(".sheet-body");
      const top = scroll ? scroll.scrollTop : 0;
      sheetEl.replaceChildren(box);
      box.querySelector(".sheet-body").scrollTop = top;
    };
    sheetEl.classList.toggle("flipped", !!(v && v.flipped));
    sheetEl.hidden = false;
    sheet.refresh();
    const first = sheetEl.querySelector(".x");
    if (first) first.focus();
  }
  function closeSheet() {
    sheet.open = false;
    sheetEl.hidden = true;
    sheetEl.replaceChildren();
  }

  // ------------------------------------------------------------ game menu
  const menu = document.getElementById("menu");
  const menuExtra = document.getElementById("menu-extra");
  function openMenu() {
    menuExtra.replaceChildren();
    if (typeof menu.showModal === "function") menu.showModal();
    else menu.setAttribute("open", "");
  }
  function rematch() {
    state = L.rematch(state);
    past = [];
    writeStore();
    if (menu.open) menu.close();
    showTable();
  }
  menu.addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const a = b.dataset.act;
    if (a === "undo") return undo();
    if (a === "nextTurn") return act({ type: "nextTurn" });
    if (a === "dayNight") return act({ type: "dayNight" });
    if (a === "rematch") return rematch();
    if (a === "setup") { menu.close(); return showSetup(); }
    if (a === "fullscreen") {
      const d = document.documentElement;
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      else if (d.requestFullscreen) d.requestFullscreen().catch(() => {});
      return;
    }
    if (a === "dice") {
      const out = el("output", { class: "roll", text: "–" });
      const roll = (label, fn) => el("button", { type: "button", text: label, onclick: () => {
        const r = fn();
        out.textContent = r;
        out.classList.remove("pop"); void out.offsetWidth; out.classList.add("pop");
        act({ type: "note", text: `${label}: ${r}` });
      } });
      menuExtra.replaceChildren(el("div", { class: "dice" },
        roll("d6", () => L.rollDie(6)), roll("d20", () => L.rollDie(20)), roll("d4", () => L.rollDie(4)), roll("d12", () => L.rollDie(12)),
        roll("Coin", () => (L.rollDie(2) === 1 ? "Heads" : "Tails")), roll("Planar die", () => ["Planeswalk", "Chaos", "–", "–", "–", "–"][L.rollDie(6) - 1])), out);
      return;
    }
    if (a === "first") {
      const live = L.alive(state);
      const pick = live[L.rollDie(live.length) - 1];
      act({ type: "setActive", player: pick.id });
      menuExtra.replaceChildren(el("p", { class: "roll pop", text: `${pick.name} goes first` }));
      const v = seatViews[pick.id];
      if (v) { v.seat.classList.remove("flash"); void v.seat.offsetWidth; v.seat.classList.add("flash"); }
      return;
    }
    if (a === "log") {
      menuExtra.replaceChildren(el("ol", { class: "log", reversed: true },
        state.log.slice().reverse().map((l) => el("li", {},
          el("time", { text: new Date(l.t).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) }), " ", l.text))));
    }
  });

  // ------------------------------------------------------------ keep the screen on
  let wakeLock = null;
  function keepAwake() {
    if (!("wakeLock" in navigator) || document.visibilityState !== "visible" || tableEl.hidden) return;
    navigator.wakeLock.request("screen").then((l) => {
      wakeLock = l;
      l.addEventListener("release", () => { wakeLock = null; });
    }).catch(() => {});
  }
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && !wakeLock) keepAwake(); });

  // ------------------------------------------------------------ go
  if (state && !CFG.startDeck) showTable();
  else showSetup();
  window.__life = { get state() { return state; } };  // for checks in the browser console
})();
