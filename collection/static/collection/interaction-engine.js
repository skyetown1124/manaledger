/* Commander interaction simulator: hypothetical opponents who push back between your turns.
 *
 * Plain functions, no DOM: runs in the browser (window.InteractionSim) and in Node tests.
 * It only decides WHAT the opponents do. It never changes the game itself; the player
 * resolves each event (picks the target for Beast Within, declares blocks, and so on).
 *
 * How a round works (after your turn, once the start turn is reached):
 *   each opponent rolls every kind of interaction at its chance per opponent turn,
 *   scaled by how relevant it is to your board (no creature removal with no creatures,
 *   wipes mostly when you have several creatures) and, optionally, ramped up over the
 *   first turns. The hits are shuffled and capped at "max per round".
 */
(function (root) {
  "use strict";

  const CATEGORIES = [
    { id: "attack", label: "Attacks", hint: "Opponents swing at you with creatures." },
    { id: "creature_removal", label: "Creature removal", hint: "Single-target kill, exile or bounce on a creature." },
    { id: "permanent_removal", label: "Artifact, enchantment & permanent removal", hint: "Answers to your rocks, enchantments or any permanent." },
    { id: "wipe", label: "Board wipes", hint: "Wrath effects. Mostly when you have several creatures out." },
    { id: "counter", label: "Counterspells", hint: "An opponent holds one up during your next turn." },
    { id: "hand", label: "Hand disruption", hint: "Discard and wheels." },
    { id: "library", label: "Graveyard & library hate", hint: "Graveyard exile and mill." },
    { id: "drain", label: "Life loss & burn", hint: "Drain triggers, burn spells, Gray Merchant." },
    { id: "theft", label: "Theft", hint: "Opponents take or borrow your things." },
  ];

  // Chance (%) that ONE opponent does this on ONE of their turns.
  const BALANCED = { attack: 30, creature_removal: 12, permanent_removal: 7, wipe: 3, counter: 6,
                     hand: 2, library: 3, drain: 6, theft: 1.5 };

  function scaled(base, factors) {
    const out = {};
    for (const k of Object.keys(base)) out[k] = Math.min(100, Math.round(base[k] * (factors[k] || 1) * 10) / 10);
    return out;
  }

  const PRESETS = [
    { id: "balanced", label: "Balanced table (default)", note: "A typical mid-power pod: some attacks, a removal spell now and then, the occasional wipe.",
      settings: { startTurn: 3, maxPerRound: 2, chances: BALANCED } },
    { id: "casual", label: "Casual / low interaction", note: "Battlecruiser games: people mostly develop their own boards.",
      settings: { startTurn: 4, maxPerRound: 1, chances: scaled(BALANCED, { attack: 0.6, creature_removal: 0.5, permanent_removal: 0.5, wipe: 0.6, counter: 0.3, hand: 0.5, library: 0.5, drain: 0.6, theft: 0.5 }) } },
    { id: "aggro", label: "Aggressive creature decks", note: "Lots of combat and drain; little removal.",
      settings: { startTurn: 3, maxPerRound: 3, chances: scaled(BALANCED, { attack: 1.9, creature_removal: 0.7, permanent_removal: 0.6, wipe: 0.4, counter: 0.3, drain: 1.6 }) } },
    { id: "control", label: "Removal-heavy control", note: "Spot removal and wipes keep your board small.",
      settings: { startTurn: 3, maxPerRound: 2, chances: scaled(BALANCED, { attack: 0.5, creature_removal: 2, permanent_removal: 2, wipe: 2.5, counter: 1.3 }) } },
    { id: "blue", label: "Counterspells & bounce (blue tables)", note: "Your big spells get countered; theft and bounce happen.",
      settings: { startTurn: 3, maxPerRound: 2, chances: scaled(BALANCED, { attack: 0.6, counter: 3.5, theft: 3, hand: 1.5 }) } },
    { id: "graveyard", label: "Graveyard hate & stax", note: "Your graveyard gets exiled and your hand gets wheeled.",
      settings: { startTurn: 3, maxPerRound: 2, chances: scaled(BALANCED, { library: 4, hand: 3, permanent_removal: 1.4 }) } },
    { id: "cedh", label: "cEDH", note: "Fast, efficient interaction from turn 1, especially counterspells.",
      settings: { startTurn: 1, maxPerRound: 3, chances: scaled(BALANCED, { attack: 0.4, creature_removal: 1.8, permanent_removal: 1.8, wipe: 0.5, counter: 5, hand: 2, library: 2 }) } },
  ];

  function defaultSettings(format) {
    return {
      enabled: false,
      showWidget: true,
      preset: "balanced",
      opponents: format === "commander" ? 3 : 1,
      startTurn: 3,
      maxPerRound: 2,
      ramp: true,
      chances: Object.assign({}, BALANCED),
    };
  }

  function clamp(n, lo, hi, fallback) {
    n = Number(n);
    return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : fallback;
  }

  /** Fill in anything missing or out of range (settings come from browser storage). */
  function normalize(settings, format) {
    const base = defaultSettings(format);
    const s = Object.assign({}, base, settings || {});
    s.enabled = !!s.enabled;
    s.showWidget = s.showWidget !== false;
    s.ramp = s.ramp !== false;
    s.opponents = Math.round(clamp(s.opponents, 1, 5, base.opponents));
    s.startTurn = Math.round(clamp(s.startTurn, 1, 20, base.startTurn));
    s.maxPerRound = Math.round(clamp(s.maxPerRound, 1, 6, base.maxPerRound));
    const chances = {};
    for (const c of CATEGORIES) chances[c.id] = clamp((s.chances || {})[c.id], 0, 100, BALANCED[c.id]);
    s.chances = chances;
    if (!PRESETS.some((p) => p.id === s.preset)) s.preset = "custom";
    return s;
  }

  function applyPreset(settings, presetId) {
    const preset = PRESETS.find((p) => p.id === presetId);
    if (!preset) return settings;
    return Object.assign({}, settings, {
      preset: preset.id,
      startTurn: preset.settings.startTurn,
      maxPerRound: preset.settings.maxPerRound,
      chances: Object.assign({}, preset.settings.chances),
    });
  }

  function shouldRoll(settings, turn) {
    return !!settings.enabled && turn >= settings.startTurn;
  }

  // ------------------------------------------------------------ the board, as opponents see it

  function front(card) {
    return (card.type_line || "").split("//")[0];
  }

  function board(state) {
    const perms = state.battlefield || [];
    const nonland = perms.filter((c) => !/\bLand\b/.test(front(c)));
    return {
      turn: state.turn || 0,
      creatures: perms.filter((c) => /\bCreature\b/.test(front(c))).length,
      artifacts: perms.filter((c) => /\bArtifact\b/.test(front(c))).length,
      enchantments: perms.filter((c) => /\bEnchantment\b/.test(front(c))).length,
      nonland: nonland.length,
      lands: perms.length - nonland.length,
      hand: (state.hand || []).length,
      spellsInHand: (state.hand || []).filter((c) => !/\bLand\b/.test(front(c))).length,
      graveyard: (state.graveyard || []).length,
      library: (state.library || []).length,
      life: state.life,
    };
  }

  /** How much each kind of interaction makes sense against this board (0 = never). */
  function relevance(category, b) {
    switch (category) {
      case "creature_removal": return b.creatures ? 1 : 0;
      case "permanent_removal": return b.nonland ? 1 : 0;
      case "wipe": return b.creatures >= 3 ? 1 : b.creatures === 2 ? 0.4 : b.nonland >= 4 ? 0.2 : 0;
      case "counter": return b.spellsInHand ? 1 : 0;
      case "hand": return b.hand ? 1 : 0;
      case "library": return b.library ? 1 : 0;
      case "theft": return b.creatures ? 1 : 0.3;
      default: return 1;
    }
  }

  function rampFactor(settings, turn) {
    if (!settings.ramp) return 1;
    return Math.min(1, 0.6 + 0.2 * (turn - settings.startTurn));
  }

  // ------------------------------------------------------------ what happens

  function pick(list, rng) {
    return list[Math.floor(rng() * list.length)];
  }

  function between(lo, hi, rng) {
    return lo + Math.floor(rng() * (hi - lo + 1));
  }

  function opponentNames(count) {
    if (count === 1) return ["Your opponent"];
    if (count === 2) return ["The player to your left", "The player to your right"];
    if (count === 3) return ["The player to your left", "The player across from you", "The player to your right"];
    return Array.from({ length: count }, (_, i) => `Opponent ${i + 1}`);
  }

  const KEYWORDS = [["", 55], ["flying", 20], ["trample", 12], ["menace", 8], ["deathtouch", 5]];

  function keyword(rng) {
    let r = rng() * 100;
    for (const [k, w] of KEYWORDS) { if ((r -= w) < 0) return k; }
    return "";
  }

  function attackers(turn, rng) {
    const r = rng();
    const count = r < 0.5 ? 1 : r < 0.85 ? 2 : 3;
    const top = Math.min(9, 1 + Math.ceil(turn * 0.75));
    const list = [];
    for (let i = 0; i < count; i++) {
      const power = between(1, top, rng);
      const toughness = Math.max(1, power + between(-1, 2, rng));
      list.push({ power, toughness, keyword: keyword(rng), commander: false });
    }
    if (rng() < 0.3) {  // sometimes the biggest one is their commander
      const biggest = list.reduce((a, b) => (b.power > a.power ? b : a));
      biggest.commander = true;
      biggest.power += 2;
      biggest.toughness += 2;
    }
    return list;
  }

  function describeAttacker(a) {
    return `${a.power}/${a.toughness}${a.keyword ? " " + a.keyword : ""}${a.commander ? " (their commander)" : ""}`;
  }

  // Each template: card, text (what to do), optional `needs(b)`, optional extras.
  const TEMPLATES = {
    creature_removal: [
      { card: "Swords to Plowshares", text: "Exile one of your creatures. You gain life equal to its power." },
      { card: "Path to Exile", text: "Exile one of your creatures. You may search your library for a basic land and put it onto the battlefield tapped, then shuffle." },
      { card: "Infernal Grasp", text: "Destroy one of your creatures." },
      { card: "Rapid Hybridization", text: "Destroy one of your creatures. You create a 3/3 green Lizard token." },
      { card: "Pongify", text: "Destroy one of your creatures. You create a 3/3 green Ape token." },
      { card: "Feed the Swarm", text: "Destroy one of your creatures." },
      { card: "Lightning Bolt", text: "Deal 3 damage to one of your creatures (or a planeswalker). If nothing has 3 or less toughness, they aim it at your face: lose 3 life." },
      { card: "Snap", text: "Return one of your creatures to your hand." },
    ],
    permanent_removal: [
      { card: "Beast Within", text: "Destroy one of your permanents (they'll pick your best nonland one). You create a 3/3 green Beast token." },
      { card: "Generous Gift", text: "Destroy one of your permanents. You create a 3/3 green Elephant token." },
      { card: "Chaos Warp", text: "Shuffle one of your permanents into your library, then reveal the top card. If it's a permanent card, put it onto the battlefield." },
      { card: "Anguished Unmaking", text: "Exile one of your nonland permanents." },
      { card: "Assassin's Trophy", text: "Destroy one of your permanents. You may search your library for a basic land and put it onto the battlefield, then shuffle." },
      { card: "Nature's Claim", text: "Destroy one of your artifacts or enchantments. You gain 4 life.", needs: (b) => b.artifacts + b.enchantments > 0 },
      { card: "Abrade", text: "Destroy one of your artifacts.", needs: (b) => b.artifacts > 0 },
      { card: "Feed the Swarm", text: "Destroy one of your enchantments.", needs: (b) => b.enchantments > 0 },
      { card: "Cyclonic Rift", text: "Return one of your nonland permanents to your hand." },
    ],
    wipe: [
      { card: "Wrath of God", text: "Destroy all creatures (yours included)." },
      { card: "Blasphemous Act", text: "Deal 13 damage to each creature." },
      { card: "Toxic Deluge", text: "All creatures get -{x}/-{x} until end of turn. Anything with toughness {x} or less dies.", x: [2, 5] },
      { card: "Farewell", text: "Exile all creatures and all artifacts.", needs: (b) => b.creatures + b.artifacts >= 3 },
      { card: "Austere Command", text: "Destroy all creatures and all enchantments." },
      { card: "Evacuation", text: "Return all creatures to their owners' hands." },
      { card: "Cyclonic Rift (overloaded)", text: "Return all your nonland permanents to your hand.", needs: (b) => b.nonland >= 4 },
      { card: "Vandalblast (overloaded)", text: "Destroy all your artifacts.", needs: (b) => b.artifacts >= 3 },
    ],
    counter: [
      { card: "Counterspell", text: "Counter the first spell you cast next turn that costs 3 or more (or your commander, whichever comes first).", pending: true },
      { card: "Arcane Denial", text: "Counter the first spell you cast next turn. You may draw up to two cards at the beginning of the following upkeep.", pending: true },
      { card: "Negate", text: "Counter the first noncreature spell you cast next turn.", pending: true },
      { card: "Essence Scatter", text: "Counter the first creature spell you cast next turn.", pending: true },
      { card: "Swan Song", text: "Counter the first instant, sorcery or enchantment you cast next turn. You create a 2/2 blue Bird with flying.", pending: true },
      { card: "Fierce Guardianship", text: "Counter the first noncreature spell you cast next turn (it's free for them).", pending: true },
    ],
    hand: [
      { card: "Wheel of Fortune", text: "Discard your hand, then draw seven cards." },
      { card: "Windfall", text: "Discard your hand, then draw as many cards as the most anyone discarded (draw at least as many as you discarded)." },
      { card: "Hymn to Tourach", text: "Discard two cards at random: {discard}.", discard: 2 },
      { card: "Mind Rot", text: "Discard two cards of your choice." },
      { card: "Syphon Mind", text: "Lose 1 life (an opponent draws a card)." , life: 1 },
    ],
    library: [
      { card: "Bojuka Bog", text: "Exile your graveyard.", needs: (b) => b.graveyard > 0 },
      { card: "Tormod's Crypt", text: "Exile your graveyard.", needs: (b) => b.graveyard > 0 },
      { card: "Rest in Peace", text: "Exile your graveyard. While it stays out, cards that would go to your graveyard are exiled instead. (Remember it until it's removed.)" },
      { card: "Glimpse the Unthinkable", text: "Mill 10 cards." },
      { card: "Mesmeric Orb", text: "Mill {x} cards (one for each permanent you untapped).", x: [2, 5] },
    ],
    drain: [
      { card: "Blood Artist triggers", text: "Lose {x} life.", x: [1, 3], life: true },
      { card: "Fireball", text: "Lose {x} life.", x: [3, 7], life: true },
      { card: "Gray Merchant of Asphodel", text: "Lose {x} life.", x: [3, 6], life: true },
      { card: "Sanguine Bond", text: "Lose {x} life.", x: [2, 5], life: true },
      { card: "Exsanguinate", text: "Lose {x} life.", x: [3, 8], life: true },
      { card: "Comet Storm", text: "Take {x} damage (or they kill a creature of yours with it instead, if that's worse for you).", x: [3, 6], life: true },
    ],
    theft: [
      { card: "Control Magic", text: "They gain control of your best creature. Move it out of play (it's theirs now).", needs: (b) => b.creatures > 0 },
      { card: "Act of Treason", text: "They take your best creature for their turn and attack you with it: lose life equal to its power unless you block.", needs: (b) => b.creatures > 0 },
      { card: "Hostage Taker", text: "Exile one of your artifacts or creatures. They may cast it.", needs: (b) => b.creatures + b.artifacts > 0 },
      { card: "Bribery", text: "They search your library for your best creature and put it onto their battlefield. Remove it from your library, then shuffle.", needs: (b) => b.library > 0 },
    ],
  };

  function makeEvent(category, opponent, b, rng, state) {
    if (category === "attack") {
      const list = attackers(Math.max(1, b.turn), rng);
      const total = list.reduce((sum, a) => sum + a.power, 0);
      return {
        category, opponent, card: null, title: `${opponent} attacks you`,
        text: `Attacking: ${list.map(describeAttacker).join(", ")}. Block with your untapped creatures, then lose life for the damage that gets through.`,
        attackers: list, life: total, lifeLabel: `Take all ${total} damage`,
      };
    }
    const options = TEMPLATES[category].filter((t) => !t.needs || t.needs(b));
    if (!options.length) return null;
    const t = pick(options, rng);
    let text = t.text;
    let life = null;
    if (t.x) {
      const x = between(t.x[0], t.x[1], rng);
      text = text.replace(/\{x\}/g, x);
      if (t.life === true) life = x;
    } else if (typeof t.life === "number") {
      life = t.life;
    }
    if (t.discard) {
      const hand = (state.hand || []).slice();
      const chosen = [];
      for (let i = 0; i < t.discard && hand.length; i++) chosen.push(hand.splice(Math.floor(rng() * hand.length), 1)[0].name);
      text = text.replace("{discard}", chosen.join(" and ") || "nothing (your hand is empty)");
    }
    return {
      category, opponent, card: t.card, title: `${opponent} casts ${t.card}`, text,
      pending: !!t.pending, life, lifeLabel: life ? `Lose ${life} life` : null,
    };
  }

  /**
   * Roll the opponents' turns that come after your current turn.
   * Returns a list of events (possibly empty). Never changes `state`.
   */
  function roll(settings, state, rng) {
    rng = rng || Math.random;
    const b = board(state);
    if (!shouldRoll(settings, b.turn)) return [];
    const ramp = rampFactor(settings, b.turn);
    const hits = [];
    for (const opponent of opponentNames(settings.opponents)) {
      for (const c of CATEGORIES) {
        const chance = (settings.chances[c.id] || 0) / 100 * relevance(c.id, b) * ramp;
        if (chance > 0 && rng() < chance) hits.push({ category: c.id, opponent });
      }
    }
    // Shuffle so the cap doesn't always favour the first opponent or category.
    for (let i = hits.length - 1; i > 0; i--) {
      const j = Math.floor(rng() * (i + 1));
      [hits[i], hits[j]] = [hits[j], hits[i]];
    }
    const events = [];
    const once = new Set();  // one wipe and one held counterspell per round is plenty
    for (const hit of hits) {
      if (events.length >= settings.maxPerRound) break;
      if ((hit.category === "wipe" || hit.category === "counter") && once.has(hit.category)) continue;
      const event = makeEvent(hit.category, hit.opponent, b, rng, state);
      if (!event) continue;
      once.add(hit.category);
      events.push(event);
    }
    // Resolve in table order: wipes and removal before attacks makes the log read naturally.
    const order = ["counter", "hand", "library", "theft", "permanent_removal", "creature_removal", "wipe", "drain", "attack"];
    events.sort((a, b2) => order.indexOf(a.category) - order.indexOf(b2.category));
    events.forEach((e, i) => { e.id = `${b.turn}-${i}`; e.turn = b.turn; });
    return events;
  }

  /** Expected number of interaction events per round at full strength (before the cap), for the settings screen. */
  function expectedPerRound(settings) {
    let total = 0;
    for (const c of CATEGORIES) total += (settings.chances[c.id] || 0) / 100;
    return total * settings.opponents;
  }

  function categoryLabel(id) {
    const c = CATEGORIES.find((x) => x.id === id);
    return c ? c.label : id;
  }

  const Sim = { CATEGORIES, PRESETS, TEMPLATES, defaultSettings, normalize, applyPreset, shouldRoll,
                board, relevance, roll, expectedPerRound, categoryLabel, opponentNames };
  if (typeof module !== "undefined" && module.exports) module.exports = Sim;
  else root.InteractionSim = Sim;
})(typeof window !== "undefined" ? window : globalThis);
