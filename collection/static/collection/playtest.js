// Playtest page: draws the table and wires buttons to PlaytestEngine (playtest-engine.js).
(function () {
  "use strict";
  const E = window.PlaytestEngine;
  const DATA = JSON.parse(document.getElementById("playtest-data").textContent);
  const $ = (id) => document.getElementById(id);
  const canHover = window.matchMedia("(hover: hover)").matches;

  let state = null;
  let onThePlay = true;
  const selected = new Set();   // cards picked to go on the bottom after a mulligan

  // ------------------------------------------------------------ small helpers

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child.nodeType ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function mana(cost) {
    const wrap = el("span", { class: "mana" });
    (cost || "").split(" // ").forEach((half, i) => {
      if (i) wrap.append(el("span", { class: "ms-split", text: "//" }));
      for (const m of half.matchAll(/\{([^}]+)\}/g)) {
        const sym = m[1].toUpperCase();
        const colors = [..."WUBRG"].filter((c) => sym.includes(c));
        let cls = "ms ms-generic";
        if (colors.length === 2 && sym.includes("/")) cls = `ms ms-${colors[0]} ms-hybrid-${colors[1]}`;
        else if (colors.length) cls = `ms ms-${colors[0]}`;
        wrap.append(el("span", { class: cls, title: m[1], text: sym.replace("/", "").slice(0, 2) }));
      }
    });
    return wrap;
  }

  function stats(card) {
    if (card.power || card.toughness) return `${card.power}/${card.toughness}`;
    if (card.loyalty) return `Loyalty ${card.loyalty}`;
    return "";
  }

  function textFace(card) {
    return el("span", { class: "pt-face" },
      el("span", { class: "pt-face-top" }, el("span", { class: "pt-face-name", text: card.name }), card.mana_cost ? mana(card.mana_cost) : null),
      el("span", { class: "pt-face-type", text: card.token ? "Token" : (card.type_line || "") }),
      card.oracle_text ? el("span", { class: "pt-face-text", text: card.oracle_text }) : null,
      stats(card) ? el("span", { class: "pt-face-pt", text: stats(card) }) : null,
    );
  }

  function cardNode(card, zone) {
    const classes = ["pt-card"];
    if (card.tapped) classes.push("tapped");
    if (selected.has(card.uid)) classes.push("selected");
    if (card.token) classes.push("token");
    const node = el("button", {
      type: "button", class: classes.join(" "), "data-uid": card.uid, "data-zone": zone,
      "aria-label": `${card.name}${card.tapped ? " (tapped)" : ""}`,
      "aria-pressed": zone === "hand" && state.phase === "bottom" ? String(selected.has(card.uid)) : null,
    });
    if (card.image_url) {
      const img = el("img", { src: card.image_url, alt: "", loading: "lazy", draggable: "false" });
      img.addEventListener("error", () => img.replaceWith(textFace(card)), { once: true });
      node.append(img);
    } else {
      node.append(textFace(card));
    }
    if (zone === "command") {
      const tax = E.commanderTax(state, card.uid);
      node.append(el("span", { class: "pt-badge", text: tax ? `Tax +${tax}` : "Cast" }));
    }
    return node;
  }

  function fill(container, cards, zone) {
    container.replaceChildren(...cards.map((c) => cardNode(c, zone)));
  }

  // ------------------------------------------------------------ rendering

  function render() {
    $("pt-turn").textContent = state.phase === "playing" ? state.turn : "–";
    $("pt-life").textContent = state.life;
    $("pt-library-count").textContent = state.library.length;
    $("pt-mana").textContent = E.untappedLands(state);
    $("pt-lands-played").textContent = state.landsPlayed;
    $("pt-hand-count").textContent = `(${state.hand.length})`;
    for (const pile of ["library", "graveyard", "exile"]) $("pt-pile-" + pile).textContent = state[pile].length;

    const lands = state.battlefield.filter((c) => E.isLand(c));
    const others = state.battlefield.filter((c) => !E.isLand(c));
    fill($("pt-permanents"), others, "battlefield");
    fill($("pt-lands"), lands, "battlefield");
    fill($("pt-hand"), state.hand, "hand");
    fill($("pt-command"), state.command, "command");
    $("pt-command-panel").hidden = !DATA.commanders.length && !state.command.length;

    renderBanner();
    const playing = state.phase === "playing";
    document.querySelectorAll(".pt-actions [data-act]").forEach((b) => {
      if (b.dataset.act !== "restart") b.disabled = !playing;
    });
    $("pt-log").replaceChildren(...state.log.slice(0, 40).map((entry) =>
      el("li", {}, entry.turn ? el("span", { class: "muted", text: `T${entry.turn} ` }) : null, entry.text)));
  }

  function renderBanner() {
    const banner = $("pt-banner");
    if (state.phase === "mulligan") {
      const next = Math.max(0, state.handSize - Math.max(0, state.mulligans + 1 - (state.freeMulligan ? 1 : 0)));
      const lands = state.hand.filter((c) => E.isLand(c)).length;
      banner.replaceChildren(
        el("div", { class: "pt-banner-text" },
          el("strong", { text: state.mulligans ? `Mulligan ${state.mulligans}` : "Opening hand" }),
          ` · ${lands} land${lands === 1 ? "" : "s"} in hand`,
          state.toBottom ? ` · keeping means putting ${state.toBottom} on the bottom` : ""),
        el("div", { class: "pt-banner-actions" },
          state.mulligans === 0 ? el("label", { class: "check", for: "pt-play-draw" },
            el("input", { type: "checkbox", id: "pt-play-draw", checked: onThePlay ? true : null,
              onchange: (e) => { onThePlay = e.target.checked; state.onThePlay = onThePlay; } }),
            " On the play (no draw on turn 1)") : null,
          el("button", { type: "button", class: "btn primary", text: "Keep", onclick: () => { E.keep(state); render(); } }),
          el("button", { type: "button", class: "btn",
            text: state.freeMulligan && state.mulligans === 0 ? "Mulligan (free)" : `Mulligan to ${next}`,
            onclick: () => { E.mulligan(state); render(); } }),
        ),
      );
      banner.hidden = false;
    } else if (state.phase === "bottom") {
      const ready = selected.size === state.toBottom;
      banner.replaceChildren(
        el("div", { class: "pt-banner-text" },
          el("strong", { text: `Choose ${state.toBottom} card${state.toBottom === 1 ? "" : "s"} for the bottom` }),
          ` · ${selected.size} picked. Click cards in your hand to pick them.`),
        el("div", { class: "pt-banner-actions" },
          el("button", { type: "button", class: "btn primary", disabled: ready ? null : true,
            text: "Put on bottom and start",
            onclick: () => { E.bottom(state, [...selected]); selected.clear(); render(); } })),
      );
      banner.hidden = false;
    } else {
      banner.hidden = true;
      banner.replaceChildren();
    }
  }

  // ------------------------------------------------------------ card menu

  const menu = $("pt-menu");

  function closeMenu() {
    menu.hidden = true;
    menu.replaceChildren();
  }

  function actionsFor(card, zone) {
    const go = (to, pos) => () => E.move(state, card.uid, to, pos);
    const list = [];
    if (zone === "hand") {
      const verb = E.isLand(card) ? "Play land" : E.isPermanentSpell(card) ? "Cast" : "Cast (then to graveyard)";
      list.push([verb, () => E.play(state, card.uid)]);
      list.push(["Discard", go("graveyard")], ["Exile", go("exile")]);
      list.push(["Put on top of library", go("library", "top")], ["Put on bottom of library", go("library", "bottom")]);
    } else if (zone === "battlefield") {
      list.push([card.tapped ? "Untap" : "Tap", () => E.toggleTap(state, card.uid)]);
      list.push(["Return to hand", go("hand")], ["To graveyard", go("graveyard")], ["Exile", go("exile")]);
      list.push(["Put on top of library", go("library", "top")]);
    } else if (zone === "command") {
      const tax = E.commanderTax(state, card.uid);
      list.push([tax ? `Cast (tax +${tax} mana)` : "Cast commander", go("battlefield")]);
    } else {
      if (zone !== "hand") list.push(["To hand", go("hand")]);
      list.push(["To battlefield", go("battlefield")]);
      if (zone !== "graveyard") list.push(["To graveyard", go("graveyard")]);
      if (zone !== "exile") list.push(["Exile", go("exile")]);
      if (zone !== "library") list.push(["Put on top of library", go("library", "top")], ["Put on bottom of library", go("library", "bottom")]);
    }
    if (card.commander && zone !== "command") list.push(["To command zone", go("command")]);
    return list;
  }

  function openMenu(card, zone, anchor) {
    const items = actionsFor(card, zone).map(([label, fn]) =>
      el("button", { type: "button", role: "menuitem", class: "pt-menu-item", text: label,
        onclick: () => { fn(); closeMenu(); render(); refreshDialog(); } }));
    menu.replaceChildren(
      el("div", { class: "pt-menu-head" },
        el("div", { class: "card-name" }, card.name, card.mana_cost ? mana(card.mana_cost) : null),
        el("div", { class: "small muted", text: card.token ? "Token" : card.type_line || "" }),
        card.oracle_text ? el("div", { class: "pt-menu-text", text: card.oracle_text }) : null,
        stats(card) ? el("div", { class: "small", text: stats(card) }) : null),
      ...items,
    );
    menu.hidden = false;
    const r = anchor.getBoundingClientRect();
    const width = menu.offsetWidth;
    const height = menu.offsetHeight;
    const left = Math.min(Math.max(8, r.left + window.scrollX), window.scrollX + document.documentElement.clientWidth - width - 8);
    // Below the card if it fits on screen, otherwise above it.
    const below = r.bottom + 6 + height <= window.innerHeight || r.top - 6 - height < 0;
    menu.style.left = left + "px";
    menu.style.top = (below ? r.bottom + 6 : r.top - 6 - height) + window.scrollY + "px";
    const first = menu.querySelector(".pt-menu-item");
    if (first) first.focus({ preventScroll: true });  // scrolling here would move the card out from under a double-click
  }

  function cardFrom(target) {
    const node = target.closest(".pt-card, [data-card-uid]");
    if (!node) return null;
    const uid = Number(node.dataset.uid || node.dataset.cardUid);
    const where = E.find(state, uid);
    return where ? { node, card: where.card, zone: where.zone } : null;
  }

  document.addEventListener("click", (event) => {
    if (menu.contains(event.target)) return;
    const hit = event.target.closest(".pt-card") ? cardFrom(event.target) : null;
    if (!hit) { closeMenu(); return; }
    if (state.phase === "bottom") {
      if (hit.zone === "hand") {
        if (selected.has(hit.card.uid)) selected.delete(hit.card.uid);
        else if (selected.size < state.toBottom) selected.add(hit.card.uid);
        render();
      }
      return;
    }
    if (state.phase !== "playing") return;
    openMenu(hit.card, hit.zone, hit.node);
  });

  document.addEventListener("dblclick", (event) => {
    const hit = event.target.closest(".pt-card") ? cardFrom(event.target) : null;
    if (!hit || state.phase !== "playing") return;
    if (hit.zone === "hand") E.play(state, hit.card.uid);
    else if (hit.zone === "battlefield") E.toggleTap(state, hit.card.uid);
    else return;
    closeMenu();
    render();
  });

  // ------------------------------------------------------------ hover preview

  const preview = $("pt-preview");
  if (canHover) {
    document.addEventListener("mouseover", (event) => {
      const hit = event.target.closest(".pt-card") ? cardFrom(event.target) : null;
      if (!hit) { preview.hidden = true; return; }
      preview.replaceChildren(hit.card.image_url ? el("img", { src: hit.card.image_url, alt: hit.card.name }) : textFace(hit.card));
      preview.hidden = false;
    });
  }

  // ------------------------------------------------------------ dialogs

  const dialog = $("pt-dialog");
  let dialogView = null;        // function that redraws the open dialog
  let searchedLibrary = false;  // a card was taken while searching the library

  function openDialog(view) {
    dialogView = view;
    view();
    if (!dialog.open) dialog.showModal();
  }

  function refreshDialog() {
    if (dialog.open && dialogView) dialogView();
  }

  dialog.addEventListener("close", () => {
    if (searchedLibrary) {
      E.shuffleLibrary(state);
      searchedLibrary = false;
      render();
    }
    dialogView = null;
  });

  function dialogShell(title, ...body) {
    dialog.replaceChildren(el("div", { class: "pt-dialog-inner" },
      el("div", { class: "pt-dialog-head" },
        el("h3", { text: title }),
        el("button", { type: "button", class: "btn small", text: "Close", onclick: () => dialog.close() })),
      ...body));
  }

  function pileView(zone) {
    return function view() {
      const cards = zone === "library"
        ? [...state.library].sort((a, b) => a.name.localeCompare(b.name))
        : [...state[zone]].reverse();
      const title = zone === "library" ? `Search your library (${cards.length})` : `${zone[0].toUpperCase() + zone.slice(1)} (${cards.length})`;
      const note = zone === "library" ? el("p", { class: "note", text: "Taking a card shuffles your library when you close this." }) : null;
      const rows = cards.map((card) => {
        const moves = actionsFor(card, zone).map(([label, fn]) =>
          el("button", { type: "button", class: "btn small", text: label,
            onclick: () => { fn(); if (zone === "library") searchedLibrary = true; render(); view(); } }));
        return el("li", { class: "pt-list-row" },
          el("div", {}, el("div", { class: "card-name" }, card.name, card.mana_cost ? mana(card.mana_cost) : null),
            el("div", { class: "small muted", text: card.type_line || "" })),
          el("div", { class: "pt-list-actions" }, ...moves));
      });
      dialogShell(title, note, rows.length ? el("ul", { class: "pt-list" }, ...rows) : el("p", { class: "muted", text: "Empty." }));
    };
  }

  function lookView() {
    let count = Math.min(3, state.library.length);
    let order = [];      // [{uid, to}]
    function load() {
      order = state.library.slice(0, count).map((c) => ({ uid: c.uid, to: "top" }));
    }
    load();
    function view() {
      const byUid = Object.fromEntries(state.library.map((c) => [c.uid, c]));
      const picker = el("div", { class: "pt-look-count" },
        el("label", { for: "pt-look-n", text: "How many" }),
        el("input", { type: "number", id: "pt-look-n", min: "1", max: String(Math.min(20, state.library.length)), value: String(count),
          class: "qty", onchange: (e) => { count = Math.max(1, Math.min(state.library.length, Number(e.target.value) || 1)); load(); view(); } }));
      const rows = order.map((choice, i) => {
        const card = byUid[choice.uid];
        const select = el("select", { class: "compact", "aria-label": `Where ${card.name} goes`,
          onchange: (e) => { choice.to = e.target.value; } },
          ...[["top", "Keep on top"], ["bottom", "Bottom"], ["graveyard", "Graveyard"], ["hand", "Hand"]].map(([v, l]) =>
            el("option", { value: v, selected: choice.to === v ? true : null, text: l })));
        return el("li", { class: "pt-list-row" },
          el("div", {}, el("span", { class: "muted", text: `${i + 1}. ` }), el("span", { class: "card-name" }, card.name, card.mana_cost ? mana(card.mana_cost) : null),
            el("div", { class: "small muted", text: card.type_line || "" })),
          el("div", { class: "pt-list-actions" },
            i > 0 ? el("button", { type: "button", class: "btn small", text: "↑", "aria-label": `Move ${card.name} up`,
              onclick: () => { [order[i - 1], order[i]] = [order[i], order[i - 1]]; view(); } }) : null,
            select));
      });
      dialogShell("Look at the top of your library", picker,
        el("p", { class: "note", text: "Cards kept on top stay in the order shown (1 is the top card)." }),
        el("ol", { class: "pt-list" }, ...rows),
        el("div", { class: "row-actions" },
          el("button", { type: "button", class: "btn primary", text: "Done",
            onclick: () => { E.resolveTop(state, order); dialog.close(); render(); } })));
    }
    return view;
  }

  // ------------------------------------------------------------ buttons and keys

  const actions = {
    draw: () => E.draw(state, 1),
    next: () => E.nextTurn(state),
    untap: () => E.untapAll(state),
    mill: () => E.mill(state, 1),
    shuffle: () => E.shuffleLibrary(state),
    look: () => { if (state.library.length) openDialog(lookView()); },
    restart: () => { closeMenu(); start(); },
  };

  document.querySelectorAll("[data-act]").forEach((button) => {
    button.addEventListener("click", () => {
      const act = button.dataset.act;
      if (act === "life") state.life += Number(button.dataset.n);
      else if (actions[act]) actions[act]();
      render();
    });
  });

  document.querySelectorAll("[data-pile]").forEach((button) => {
    button.addEventListener("click", () => { if (state.phase === "playing") openDialog(pileView(button.dataset.pile)); });
  });

  $("pt-token-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (state.phase !== "playing") return;
    const count = Math.max(1, Math.min(20, Number($("pt-token-count").value) || 1));
    E.createToken(state, $("pt-token-name").value, count);
    render();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { closeMenu(); return; }
    if (event.target.closest("input, select, textarea, dialog") || event.ctrlKey || event.metaKey || event.altKey) return;
    if (state.phase !== "playing") return;
    const key = event.key.toLowerCase();
    const map = { d: "draw", n: "next", u: "untap", l: "look" };
    if (map[key]) {
      event.preventDefault();
      actions[map[key]]();
      closeMenu();
      render();
    }
  });

  // ------------------------------------------------------------ odds

  function pct(p) {
    return p >= 0.995 ? ">99%" : p < 0.005 && p > 0 ? "<1%" : Math.round(p * 100) + "%";
  }

  function renderOdds() {
    const N = DATA.cards.length;
    const K = DATA.cards.filter((c) => E.isLand(c)).length;
    const n = Math.min(7, N);
    const play = $("pt-odds-play").checked;

    const rows = [];
    let keepable = 0;
    for (let k = 0; k <= n; k++) {
      const p = E.hypergeom(N, K, n, k);
      if (k >= 2 && k <= 4) keepable += p;
      rows.push(el("tr", {},
        el("td", { text: `${k} land${k === 1 ? "" : "s"}` }),
        el("td", { class: "num", text: pct(p) }),
        el("td", { class: "bar-col" }, el("span", { class: "bar-track" }, el("span", { class: "bar type-land", style: `width:${Math.round(p * 100)}%` })))));
    }
    $("pt-odds-lands").replaceChildren(el("tbody", {}, ...rows));
    $("pt-odds-summary").textContent =
      `${K} lands in ${N} cards. Average ${(n * K / N).toFixed(1)} lands per opening hand; ` +
      `${pct(keepable)} of hands have 2 to 4 lands.`;

    const drops = [];
    for (let t = 1; t <= 6; t++) {
      const seen = Math.min(N, E.cardsSeen(t, play, 7));
      drops.push(el("tr", {}, el("td", { text: `${t} land${t === 1 ? "" : "s"} by turn ${t}` }),
        el("td", { class: "num", text: pct(E.atLeast(N, K, seen, t)) })));
    }
    $("pt-odds-drops").replaceChildren(el("tbody", {}, ...drops));

    const select = $("pt-odds-card");
    const name = select.value;
    const copies = DATA.cards.filter((c) => c.name === name).length;
    const cardRows = [el("tr", {}, el("td", { text: "In your opening hand" }), el("td", { class: "num", text: pct(E.atLeast(N, copies, n, 1)) }))];
    for (let t = 2; t <= 6; t++) {
      cardRows.push(el("tr", {}, el("td", { text: `By turn ${t}` }),
        el("td", { class: "num", text: pct(E.atLeast(N, copies, Math.min(N, E.cardsSeen(t, play, 7)), 1)) })));
    }
    $("pt-odds-card-table").replaceChildren(el("tbody", {}, ...cardRows));
  }

  function setupOdds() {
    const counts = {};
    for (const c of DATA.cards) counts[c.name] = (counts[c.name] || 0) + 1;
    const names = Object.keys(counts).sort((a, b) => a.localeCompare(b));
    const nonland = names.filter((n) => !E.isLand(DATA.cards.find((c) => c.name === n)));
    $("pt-odds-card").replaceChildren(...names.map((n) =>
      el("option", { value: n, text: `${n}${counts[n] > 1 ? ` (${counts[n]} copies)` : ""}` })));
    if (nonland.length) $("pt-odds-card").value = nonland[0];
    $("pt-odds-card").addEventListener("change", renderOdds);
    document.querySelectorAll('input[name="pt-odds-side"]').forEach((r) => r.addEventListener("change", renderOdds));
    renderOdds();
  }

  function start() {
    state = E.newGame(DATA, { onThePlay });
    selected.clear();
    render();
  }

  setupOdds();
  start();
})();
