// Run with:  node --test collection/js_tests
const test = require("node:test");
const assert = require("node:assert/strict");
const S = require("../static/collection/interaction-engine.js");

function seeded(seed) {
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}

const creature = (name) => ({ name, type_line: "Creature — Elf" });
const land = (name) => ({ name, type_line: "Basic Land — Forest" });
const rock = (name) => ({ name, type_line: "Artifact" });

function table(extra = {}) {
  return Object.assign({
    turn: 5, life: 40,
    battlefield: [creature("Elf A"), creature("Elf B"), creature("Elf C"), rock("Sol Ring"), land("Forest")],
    hand: [creature("Big Thing"), land("Forest"), creature("Other Thing")],
    graveyard: [creature("Dead Elf")], library: Array.from({ length: 50 }, (_, i) => land(`L${i}`)),
  }, extra);
}

function on(settings) {
  return Object.assign(S.normalize({}, "commander"), { enabled: true }, settings);
}

test("defaults: off, three opponents in Commander, one otherwise", () => {
  assert.equal(S.defaultSettings("commander").enabled, false);
  assert.equal(S.defaultSettings("commander").opponents, 3);
  assert.equal(S.defaultSettings("modern").opponents, 1);
});

test("normalize clamps bad stored settings and keeps known presets", () => {
  const s = S.normalize({ opponents: 99, startTurn: -4, maxPerRound: "x", chances: { attack: 250, wipe: "nope" }, preset: "weird" });
  assert.equal(s.opponents, 5);
  assert.equal(s.startTurn, 1);
  assert.equal(s.maxPerRound, 2);
  assert.equal(s.chances.attack, 100);
  assert.equal(s.chances.wipe, 3);
  assert.equal(s.preset, "custom");
});

test("presets change the dials", () => {
  const base = S.normalize({}, "commander");
  const cedh = S.applyPreset(base, "cedh");
  assert.equal(cedh.startTurn, 1);
  assert.ok(cedh.chances.counter > base.chances.counter * 3);
  assert.ok(S.expectedPerRound(S.applyPreset(base, "casual")) < S.expectedPerRound(base));
  assert.equal(S.applyPreset(base, "nope"), base);
});

test("nothing happens when off or before the start turn", () => {
  assert.deepEqual(S.roll(S.normalize({}), table(), seeded(1)), []);
  assert.deepEqual(S.roll(on({ startTurn: 6 }), table({ turn: 5 }), seeded(1)), []);
});

test("the cap and the one-wipe-per-round rule hold", () => {
  const all = {}; for (const c of S.CATEGORIES) all[c.id] = 100;
  for (let seed = 1; seed < 40; seed++) {
    const events = S.roll(on({ chances: all, maxPerRound: 4, ramp: false }), table(), seeded(seed));
    assert.equal(events.length, 4);
    assert.ok(events.filter((e) => e.category === "wipe").length <= 1);
    assert.ok(events.filter((e) => e.category === "counter").length <= 1);
  }
});

test("opponents don't aim removal at an empty board", () => {
  const removalOnly = { attack: 0, creature_removal: 100, permanent_removal: 100, wipe: 100, counter: 0, hand: 0, library: 0, drain: 0, theft: 0 };
  const empty = table({ battlefield: [land("Forest")] });
  assert.deepEqual(S.roll(on({ chances: removalOnly, ramp: false }), empty, seeded(3)), []);
});

test("attacks list attackers whose power adds up to the damage", () => {
  const attacks = { attack: 100, creature_removal: 0, permanent_removal: 0, wipe: 0, counter: 0, hand: 0, library: 0, drain: 0, theft: 0 };
  for (let seed = 1; seed < 30; seed++) {
    const events = S.roll(on({ chances: attacks, maxPerRound: 3, ramp: false }), table(), seeded(seed));
    assert.equal(events.length, 3);
    for (const e of events) {
      assert.equal(e.category, "attack");
      assert.equal(e.life, e.attackers.reduce((n, a) => n + a.power, 0));
      assert.match(e.title, /attacks you$/);
      assert.ok(e.attackers.length >= 1 && e.attackers.length <= 3);
    }
  }
});

test("counterspells are held for your next turn; discards name cards from your hand", () => {
  const counters = { attack: 0, creature_removal: 0, permanent_removal: 0, wipe: 0, counter: 100, hand: 0, library: 0, drain: 0, theft: 0 };
  const [c] = S.roll(on({ chances: counters, ramp: false }), table(), seeded(5));
  assert.equal(c.category, "counter");
  assert.equal(c.pending, true);

  let sawHymn = false;
  const discard = { attack: 0, creature_removal: 0, permanent_removal: 0, wipe: 0, counter: 0, hand: 100, library: 0, drain: 0, theft: 0 };
  for (let seed = 1; seed < 80 && !sawHymn; seed++) {
    for (const e of S.roll(on({ chances: discard, ramp: false, maxPerRound: 3 }), table(), seeded(seed))) {
      if (e.card === "Hymn to Tourach") {
        sawHymn = true;
        assert.match(e.text, /(Big Thing|Forest|Other Thing) and (Big Thing|Forest|Other Thing)/);
      }
    }
  }
  assert.ok(sawHymn);
});

test("life loss events carry the amount and no template placeholders leak", () => {
  const all = {}; for (const c of S.CATEGORIES) all[c.id] = 60;
  for (let seed = 1; seed < 200; seed++) {
    for (const e of S.roll(on({ chances: all, maxPerRound: 6, ramp: false }), table(), seeded(seed))) {
      assert.doesNotMatch(e.text, /\{x\}|\{discard\}/);
      if (e.category === "drain") assert.ok(e.life > 0);
    }
  }
});

test("ramp makes the first interaction turns quieter", () => {
  const count = (turn) => {
    let n = 0;
    for (let seed = 1; seed < 300; seed++) n += S.roll(on({ maxPerRound: 6 }), table({ turn }), seeded(seed)).length;
    return n;
  };
  assert.ok(count(3) < count(6));
});
