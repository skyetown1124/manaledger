/* Playtest ("goldfish") rules engine: plain functions on a plain state object.
 * No DOM here, so it runs in the browser (window.PlaytestEngine) and in Node tests.
 *
 * Zones: library (index 0 = top), hand, battlefield, graveyard, exile, command.
 * Phases: "mulligan" (deciding on a hand), "bottom" (choosing cards to put on the
 * bottom after a London mulligan), "playing".
 */
(function (root) {
  "use strict";

  const ZONES = ["library", "hand", "battlefield", "graveyard", "exile", "command"];

  function defaultRng() {
    if (typeof crypto !== "undefined" && crypto.getRandomValues) {
      const buf = new Uint32Array(1);
      crypto.getRandomValues(buf);
      return buf[0] / 4294967296;
    }
    return Math.random();
  }

  /** Fisher–Yates shuffle, in place. */
  function shuffle(list, rng) {
    rng = rng || defaultRng;
    for (let i = list.length - 1; i > 0; i--) {
      const j = Math.floor(rng() * (i + 1));
      [list[i], list[j]] = [list[j], list[i]];
    }
    return list;
  }

  function isLand(card) {
    return /\bLand\b/.test((card.type_line || "").split("//")[0].split("—")[0]);
  }

  function isPermanentSpell(card) {
    const front = (card.type_line || "").split("//")[0];
    return !/\b(Instant|Sorcery)\b/.test(front);
  }

  /**
   * deck: { cards: [card...] (one entry per copy), commanders: [card...],
   *         life: 40|20, freeMulligan: bool }
   * opts: { onThePlay: bool, handSize: 7 }
   */
  function newGame(deck, opts, rng) {
    opts = opts || {};
    let uid = 0;
    const make = (card) => Object.assign({}, card, { uid: ++uid, tapped: false });
    const state = {
      library: shuffle(deck.cards.map(make), rng),
      hand: [], battlefield: [], graveyard: [], exile: [],
      command: (deck.commanders || []).map(make),
      life: deck.life || 20,
      turn: 0,
      phase: "mulligan",
      mulligans: 0,
      toBottom: 0,
      freeMulligan: !!deck.freeMulligan,
      onThePlay: opts.onThePlay !== false,
      handSize: opts.handSize || 7,
      landsPlayed: 0,
      commanderCasts: {},   // uid -> times cast from the command zone (commander tax)
      log: [],
    };
    draw(state, state.handSize, true);
    note(state, `Opening hand: drew ${state.hand.length}.`);
    return state;
  }

  function note(state, text) {
    state.log.unshift({ turn: state.turn, text });
    if (state.log.length > 200) state.log.length = 200;
  }

  function draw(state, n, quiet) {
    n = n === undefined ? 1 : n;
    let drawn = 0;
    for (let i = 0; i < n && state.library.length; i++) {
      state.hand.push(state.library.shift());
      drawn++;
    }
    if (!quiet && drawn) note(state, drawn === 1 ? `Drew ${state.hand[state.hand.length - 1].name}.` : `Drew ${drawn} cards.`);
    if (drawn < n) note(state, "Your library is empty.");
    return drawn;
  }

  /** London mulligan: shuffle the hand back, draw a fresh 7, then bottom one card per mulligan. */
  function mulligan(state, rng) {
    if (state.phase !== "mulligan") return state;
    state.library.push(...state.hand.splice(0));
    shuffle(state.library, rng);
    state.mulligans += 1;
    draw(state, state.handSize, true);
    const free = state.freeMulligan ? 1 : 0;
    state.toBottom = Math.max(0, state.mulligans - free);
    note(state, `Mulligan ${state.mulligans}: drew ${state.hand.length}` +
      (state.toBottom ? `, ${state.toBottom} to put on the bottom.` : " (free mulligan)."));
    return state;
  }

  function keep(state) {
    if (state.phase !== "mulligan") return state;
    if (state.toBottom > 0 && state.toBottom < state.hand.length) {
      state.phase = "bottom";
    } else {
      if (state.toBottom >= state.hand.length) {   // mulliganed to zero
        state.library.push(...state.hand.splice(0));
      }
      start(state);
    }
    return state;
  }

  /** Finish a London mulligan: these cards (in this order) go to the bottom of the library. */
  function bottom(state, uids) {
    if (state.phase !== "bottom") return state;
    if (uids.length !== state.toBottom) throw new Error(`Choose exactly ${state.toBottom} card(s).`);
    for (const id of uids) {
      const i = state.hand.findIndex((c) => c.uid === id);
      if (i === -1) throw new Error("That card isn't in your hand.");
      state.library.push(state.hand.splice(i, 1)[0]);
    }
    note(state, `Put ${uids.length} card(s) on the bottom. Keeping ${state.hand.length}.`);
    start(state);
    return state;
  }

  function start(state) {
    state.phase = "playing";
    state.turn = 1;
    state.landsPlayed = 0;
    note(state, `Turn 1 (${state.onThePlay ? "on the play: no draw" : "on the draw"}).`);
    if (!state.onThePlay) draw(state, 1);
  }

  function nextTurn(state) {
    if (state.phase !== "playing") return state;
    state.turn += 1;
    state.landsPlayed = 0;
    for (const c of state.battlefield) c.tapped = false;
    note(state, `Turn ${state.turn}: untapped, drawing.`);
    draw(state, 1);
    return state;
  }

  function find(state, uid) {
    for (const zone of ZONES) {
      const i = state[zone].findIndex((c) => c.uid === uid);
      if (i !== -1) return { zone, index: i, card: state[zone][i] };
    }
    return null;
  }

  /**
   * Move a card to a zone. position: "top" | "bottom" (library only; default top).
   * Leaving the battlefield untaps the card. Casting a commander from the command
   * zone counts toward commander tax.
   */
  function move(state, uid, toZone, position) {
    if (!ZONES.includes(toZone)) throw new Error("Unknown zone " + toZone);
    const where = find(state, uid);
    if (!where) return state;
    const [card] = state[where.zone].splice(where.index, 1);
    if (card.token && toZone !== "battlefield") {
      note(state, `${card.name} token left the battlefield and is gone.`);
      return state;
    }
    if (toZone !== "battlefield") card.tapped = false;
    if (where.zone === "command" && (toZone === "battlefield" || toZone === "graveyard")) {
      state.commanderCasts[uid] = (state.commanderCasts[uid] || 0) + 1;
    }
    if (toZone === "library" && position === "bottom") state.library.push(card);
    else if (toZone === "library") state.library.unshift(card);
    else state[toZone].push(card);
    if (where.zone === "hand" && toZone === "battlefield" && isLand(card)) state.landsPlayed += 1;
    note(state, `${card.name}: ${where.zone} → ${toZone}${toZone === "library" ? " (" + (position || "top") + ")" : ""}.`);
    return state;
  }

  /** Play a card from hand: lands and permanents go to the battlefield, instants and sorceries resolve to the graveyard. */
  function play(state, uid) {
    const where = find(state, uid);
    if (!where) return state;
    const target = isPermanentSpell(where.card) ? "battlefield" : "graveyard";
    return move(state, uid, target);
  }

  let tokenUid = 1000000;
  function createToken(state, name, count) {
    const label = (name || "").trim() || "Token";
    for (let i = 0; i < (count || 1); i++) {
      state.battlefield.push({ uid: ++tokenUid, name: label, type_line: "Token", token: true, tapped: false });
    }
    note(state, `Created ${count || 1} ${label} token${(count || 1) === 1 ? "" : "s"}.`);
    return state;
  }

  function commanderTax(state, uid) {
    return 2 * (state.commanderCasts[uid] || 0);
  }

  function toggleTap(state, uid) {
    const where = find(state, uid);
    if (where && where.zone === "battlefield") where.card.tapped = !where.card.tapped;
    return state;
  }

  function untapAll(state) {
    for (const c of state.battlefield) c.tapped = false;
    return state;
  }

  function mill(state, n) {
    const milled = state.library.splice(0, n === undefined ? 1 : n);
    state.graveyard.push(...milled);
    if (milled.length) note(state, `Milled ${milled.map((c) => c.name).join(", ")}.`);
    return milled;
  }

  function shuffleLibrary(state, rng) {
    shuffle(state.library, rng);
    note(state, "Shuffled your library.");
    return state;
  }

  /** Scry-style resolution of the top cards: choices = [{uid, to: "top"|"bottom"|"graveyard"|"hand"}] in final order. */
  function resolveTop(state, choices) {
    const ids = new Set(choices.map((c) => c.uid));
    const top = state.library.filter((c) => ids.has(c.uid));
    state.library = state.library.filter((c) => !ids.has(c.uid));
    const byId = Object.fromEntries(top.map((c) => [c.uid, c]));
    const keepTop = [];
    for (const choice of choices) {
      const card = byId[choice.uid];
      if (!card) continue;
      if (choice.to === "top") keepTop.push(card);
      else if (choice.to === "bottom") state.library.push(card);
      else if (choice.to === "graveyard") state.graveyard.push(card);
      else if (choice.to === "hand") state.hand.push(card);
    }
    state.library.unshift(...keepTop);
    note(state, `Looked at ${choices.length} card(s): ` +
      choices.map((c) => `${byId[c.uid] ? byId[c.uid].name : "?"} → ${c.to}`).join(", ") + ".");
    return state;
  }

  function untappedLands(state) {
    return state.battlefield.filter((c) => isLand(c) && !c.tapped).length;
  }

  // ------------------------------------------------------------------ odds

  function logChoose(n, k) {
    if (k < 0 || k > n) return -Infinity;
    let s = 0;
    for (let i = 1; i <= k; i++) s += Math.log(n - k + i) - Math.log(i);
    return s;
  }

  /** P(exactly k successes) drawing n cards from a deck of N with K successes. */
  function hypergeom(N, K, n, k) {
    if (k < 0 || k > n || k > K || n - k > N - K) return 0;
    return Math.exp(logChoose(K, k) + logChoose(N - K, n - k) - logChoose(N, n));
  }

  /** P(at least `atLeast` successes) in n cards. */
  function atLeast(N, K, n, atLeastK) {
    let p = 0;
    for (let k = atLeastK; k <= Math.min(n, K); k++) p += hypergeom(N, K, n, k);
    return Math.min(1, p);
  }

  /** Cards seen by the start of your turn t (opening 7, plus draws). */
  function cardsSeen(turn, onThePlay, handSize) {
    return (handSize || 7) + turn - (onThePlay ? 1 : 0);
  }

  const Engine = {
    ZONES, shuffle, isLand, isPermanentSpell, newGame, draw, mulligan, keep, bottom, nextTurn,
    find, move, play, toggleTap, untapAll, mill, shuffleLibrary, resolveTop, untappedLands,
    commanderTax, createToken, hypergeom, atLeast, cardsSeen,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = Engine;
  else root.PlaytestEngine = Engine;
})(typeof window !== "undefined" ? window : this);
