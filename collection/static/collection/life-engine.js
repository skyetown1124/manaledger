/* Life tracker game state: plain functions, no DOM.
 * Runs in the browser (window.LifeEngine) and in Node tests.
 *
 * Every change goes through apply(state, action), which returns a NEW state, so
 * undo is just keeping the previous states. Covers:
 *   life, poison, commander damage (per opposing commander, partners included),
 *   energy / experience / rad counters, commander tax, monarch, the initiative,
 *   the city's blessing, day/night, turns and the active player, and who has lost.
 */
(function (root) {
  "use strict";

  const FORMATS = {
    commander: { label: "Commander", life: 40, commanderDamage: true },
    brawl: { label: "Brawl", life: 25, commanderDamage: false },
    oathbreaker: { label: "Oathbreaker", life: 20, commanderDamage: false },
    standard: { label: "20 life (60-card)", life: 20, commanderDamage: false },
    custom: { label: "Custom", life: 30, commanderDamage: true },
  };
  const LETHAL_POISON = 10;
  const LETHAL_COMMANDER = 21;
  const COUNTERS = ["poison", "energy", "experience", "rad"];
  const COLORS = ["#c0392b", "#2f7ed8", "#27ae60", "#8e44ad", "#d68910", "#16a085"];

  function clampInt(v, lo, hi, fallback) {
    const n = Math.round(Number(v));
    if (!Number.isFinite(n)) return fallback;
    return Math.max(lo, Math.min(hi, n));
  }

  /** A fresh game. opts: {format, life, players: [{name, commanders: [{name, art}], deckId}]} */
  function newGame(opts) {
    opts = opts || {};
    const format = FORMATS[opts.format] ? opts.format : "commander";
    const life = clampInt(opts.life, 1, 999, FORMATS[format].life);
    const seats = (opts.players && opts.players.length ? opts.players : [{}, {}, {}, {}]).slice(0, 6);
    const players = seats.map((p, i) => ({
      id: "p" + (i + 1),
      name: (p.name || "").trim().slice(0, 24) || "Player " + (i + 1),
      color: p.color || COLORS[i % COLORS.length],
      deckId: p.deckId || null,
      commanders: (p.commanders || []).slice(0, 2).map((c) => ({
        name: String(c.name || "").slice(0, 120), art: c.art || "", image: c.image || "", casts: 0,
      })),
      life,
      poison: 0, energy: 0, experience: 0, rad: 0,
      conceded: false,
    }));
    // damage[targetId][sourceId] = [from commander 1, from commander 2]
    const damage = {};
    for (const t of players) {
      damage[t.id] = {};
      for (const s of players) if (s.id !== t.id) damage[t.id][s.id] = s.commanders.map(() => 0);
    }
    return {
      version: 1, format, startLife: life,
      commanderDamage: opts.commanderDamage === undefined ? FORMATS[format].commanderDamage : !!opts.commanderDamage,
      players, damage,
      monarch: null, initiative: null, blessing: [], dayNight: null,
      turn: 1, active: players[0] ? players[0].id : null,
      log: [{ t: Date.now(), text: `New ${FORMATS[format].label} game: ${players.length} players at ${life} life` }],
      startedAt: Date.now(),
    };
  }

  function clone(state) {
    return JSON.parse(JSON.stringify(state));
  }

  function player(state, id) {
    return state.players.find((p) => p.id === id);
  }

  function totalCommanderDamage(state, targetId, sourceId) {
    const row = (state.damage[targetId] || {})[sourceId] || [];
    return row.reduce((a, b) => a + b, 0);
  }

  /** Why a player has lost, as a list of reasons (empty = still in). */
  function lossReasons(state, id) {
    const p = player(state, id);
    if (!p) return [];
    const why = [];
    if (p.conceded) why.push("Conceded");
    if (p.life <= 0) why.push("0 life");
    if (p.poison >= LETHAL_POISON) why.push(`${LETHAL_POISON} poison`);
    if (state.commanderDamage) {
      for (const s of state.players) {
        const row = (state.damage[id] || {})[s.id] || [];
        row.forEach((n, i) => {
          if (n >= LETHAL_COMMANDER) {
            const c = s.commanders[i];
            why.push(`${LETHAL_COMMANDER} commander damage from ${c && c.name ? c.name : s.name}`);
          }
        });
      }
    }
    return why;
  }

  function alive(state) {
    return state.players.filter((p) => lossReasons(state, p.id).length === 0);
  }

  function commanderTax(c) {
    return 2 * (c ? c.casts : 0);
  }

  function signed(n) {
    return (n > 0 ? "+" : "−") + Math.abs(n);
  }

  function addLog(state, text, merge) {
    const now = Date.now();
    const last = state.log[state.log.length - 1];
    // Quick taps on the same thing become one line ("Ana −5", not five "−1"s).
    if (merge && last && last.key === merge.key && now - last.t < 4000) {
      last.amount += merge.amount;
      last.t = now;
      last.text = merge.render(last.amount);
      if (last.amount === 0) state.log.pop();
      return;
    }
    const entry = { t: now, text: merge ? merge.render(merge.amount) : text };
    if (merge) { entry.key = merge.key; entry.amount = merge.amount; }
    state.log.push(entry);
    if (state.log.length > 300) state.log.splice(0, state.log.length - 300);
  }

  function nextAlive(state, fromId) {
    const n = state.players.length;
    const start = state.players.findIndex((p) => p.id === fromId);
    for (let k = 1; k <= n; k++) {
      const p = state.players[(start + k) % n];
      if (lossReasons(state, p.id).length === 0) return p.id;
    }
    return fromId;
  }

  const ACTIONS = {
    life(s, a) {
      const p = player(s, a.player);
      const before = p.life;
      p.life = clampInt(p.life + a.delta, -999, 9999, p.life);
      const d = p.life - before;
      if (d) addLog(s, null, { key: "life:" + p.id, amount: d, render: (n) => `${p.name} ${signed(n)} life (${p.life})` });
    },
    setLife(s, a) {
      const p = player(s, a.player);
      p.life = clampInt(a.value, -999, 9999, p.life);
      addLog(s, `${p.name}'s life set to ${p.life}`);
    },
    counter(s, a) {
      if (!COUNTERS.includes(a.kind)) return;
      const p = player(s, a.player);
      const before = p[a.kind];
      p[a.kind] = clampInt(before + a.delta, 0, 999, before);
      const d = p[a.kind] - before;
      if (d) addLog(s, null, { key: a.kind + ":" + p.id, amount: d, render: (n) => `${p.name} ${signed(n)} ${a.kind} (${p[a.kind]})` });
    },
    commanderDamage(s, a) {
      // Commander damage is combat damage: it also comes off life.
      const target = player(s, a.target), source = player(s, a.source);
      const row = s.damage[a.target][a.source];
      const i = a.index || 0;
      if (!row || i >= row.length) return;
      const before = row[i];
      row[i] = clampInt(before + a.delta, 0, 999, before);
      const d = row[i] - before;
      if (!d) return;
      if (a.lifeToo !== false) target.life = clampInt(target.life - d, -999, 9999, target.life);
      const cname = (source.commanders[i] && source.commanders[i].name) || source.name;
      addLog(s, null, {
        key: `cmd:${a.target}:${a.source}:${i}`, amount: d,
        render: (n) => `${cname} ${n > 0 ? "hits" : "un-hits"} ${target.name} for ${Math.abs(n)} (${row[i]} total)`,
      });
    },
    cast(s, a) {
      const p = player(s, a.player);
      const c = p.commanders[a.index || 0];
      if (!c) return;
      c.casts = clampInt(c.casts + (a.delta || 1), 0, 99, c.casts);
      addLog(s, `${p.name}: ${c.name || "commander"} cast ${c.casts} time${c.casts === 1 ? "" : "s"} (tax now +${commanderTax(c)})`);
    },
    monarch(s, a) {
      s.monarch = s.monarch === a.player ? null : a.player;
      addLog(s, s.monarch ? `${player(s, s.monarch).name} is the monarch` : "No monarch");
    },
    initiative(s, a) {
      s.initiative = s.initiative === a.player ? null : a.player;
      addLog(s, s.initiative ? `${player(s, s.initiative).name} has the initiative` : "No one has the initiative");
    },
    blessing(s, a) {
      const has = s.blessing.includes(a.player);
      s.blessing = has ? s.blessing.filter((id) => id !== a.player) : s.blessing.concat(a.player);
      addLog(s, `${player(s, a.player).name} ${has ? "loses" : "gets"} the city's blessing`);
    },
    dayNight(s) {
      s.dayNight = s.dayNight === null ? "day" : s.dayNight === "day" ? "night" : "day";
      addLog(s, `It becomes ${s.dayNight}`);
    },
    clearDayNight(s) {
      s.dayNight = null;
    },
    concede(s, a) {
      const p = player(s, a.player);
      p.conceded = !p.conceded;
      addLog(s, `${p.name} ${p.conceded ? "concedes" : "is back in"}`);
      if (p.conceded && s.active === p.id) s.active = nextAlive(s, p.id);
    },
    nextTurn(s) {
      const next = nextAlive(s, s.active);
      const wrapped = s.players.findIndex((p) => p.id === next) <= s.players.findIndex((p) => p.id === s.active);
      s.active = next;
      if (wrapped) s.turn += 1;
      addLog(s, `Turn ${s.turn}: ${player(s, s.active).name}`);
    },
    setActive(s, a) {
      s.active = a.player;
      addLog(s, `${player(s, a.player).name} goes first`);
    },
    rename(s, a) {
      const p = player(s, a.player);
      p.name = String(a.name || "").trim().slice(0, 24) || p.name;
    },
    note(s, a) {
      addLog(s, String(a.text || "").slice(0, 200));
    },
  };

  /** Returns the new state after one action; the old state is untouched. */
  function apply(state, action) {
    const fn = ACTIONS[action && action.type];
    if (!fn) return state;
    const s = clone(state);
    fn(s, action);
    // Note a player going out, once.
    for (const p of s.players) {
      const was = lossReasons(state, p.id).length > 0;
      const now = lossReasons(s, p.id);
      if (!was && now.length) {
        addLog(s, `${p.name} is out: ${now.join(", ")}`);
        if (s.active === p.id) s.active = nextAlive(s, p.id);
      }
    }
    const left = alive(s);
    if (left.length === 1 && alive(state).length > 1) addLog(s, `${left[0].name} wins!`);
    return s;
  }

  /** Same players and commanders, fresh life and counters. */
  function rematch(state) {
    return newGame({
      format: state.format, life: state.startLife, commanderDamage: state.commanderDamage,
      players: state.players.map((p) => ({ name: p.name, color: p.color, deckId: p.deckId, commanders: p.commanders })),
    });
  }

  /** Accepts a saved game only if it has the shape we expect. */
  function restore(saved) {
    if (!saved || saved.version !== 1 || !Array.isArray(saved.players) || !saved.players.length || !saved.damage) return null;
    return saved;
  }

  function rollDie(sides, rand) {
    rand = rand || Math.random;
    return 1 + Math.floor(rand() * sides);
  }

  const api = {
    FORMATS, COUNTERS, COLORS, LETHAL_POISON, LETHAL_COMMANDER,
    newGame, apply, rematch, restore, lossReasons, alive, totalCommanderDamage, commanderTax, rollDie, player,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.LifeEngine = api;
})(typeof window !== "undefined" ? window : this);
