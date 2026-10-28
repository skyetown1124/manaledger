// Run with:  node --test collection/js_tests
const test = require("node:test");
const assert = require("node:assert/strict");
const E = require("../static/collection/playtest-engine.js");

// Deterministic random numbers so shuffles repeat.
function seeded(seed) {
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}

function deck(n = 60, lands = 24, extra = {}) {
  const cards = [];
  for (let i = 0; i < n; i++) {
    const land = i < lands;
    cards.push({ id: i, name: land ? `Land ${i}` : `Spell ${i}`, type_line: land ? "Basic Land — Island" : (i % 2 ? "Instant" : "Creature — Elf") });
  }
  return Object.assign({ cards, commanders: [], life: 20, freeMulligan: false }, extra);
}

test("new game shuffles and draws seven", () => {
  const s = E.newGame(deck(), {}, seeded(1));
  assert.equal(s.hand.length, 7);
  assert.equal(s.library.length, 53);
  assert.equal(s.phase, "mulligan");
  const all = [...s.hand, ...s.library].map((c) => c.id).sort((a, b) => a - b);
  assert.deepEqual(all, [...Array(60).keys()]);
  assert.notDeepEqual(s.library.map((c) => c.id), [...Array(60).keys()].slice(7));  // actually shuffled
});

test("London mulligan: draw 7, then bottom one per mulligan", () => {
  const s = E.newGame(deck(), {}, seeded(2));
  E.mulligan(s, seeded(3));
  E.mulligan(s, seeded(4));
  assert.equal(s.hand.length, 7);
  assert.equal(s.toBottom, 2);
  E.keep(s);
  assert.equal(s.phase, "bottom");
  assert.throws(() => E.bottom(s, [s.hand[0].uid]), /exactly 2/);
  const chosen = [s.hand[0].uid, s.hand[1].uid];
  E.bottom(s, chosen);
  assert.equal(s.hand.length, 5);
  assert.deepEqual(s.library.slice(-2).map((c) => c.uid), chosen);
  assert.equal(s.phase, "playing");
  assert.equal(s.turn, 1);
  assert.equal(s.library.length + s.hand.length, 60);
});

test("Commander's first mulligan is free", () => {
  const s = E.newGame(deck(99, 37, { freeMulligan: true, life: 40 }), {}, seeded(5));
  E.mulligan(s, seeded(6));
  assert.equal(s.toBottom, 0);
  E.mulligan(s, seeded(7));
  assert.equal(s.toBottom, 1);
  assert.equal(s.life, 40);
});

test("on the play skips the first draw; on the draw doesn't", () => {
  const play = E.newGame(deck(), { onThePlay: true }, seeded(8));
  E.keep(play);
  assert.equal(play.hand.length, 7);
  const drawn = E.newGame(deck(), { onThePlay: false }, seeded(8));
  E.keep(drawn);
  assert.equal(drawn.hand.length, 8);
});

test("turns untap and draw; playing cards goes to the right zone", () => {
  const s = E.newGame(deck(), {}, seeded(9));
  E.keep(s);
  const land = s.hand.find((c) => E.isLand(c)) || (s.hand.push(s.library.find((c) => E.isLand(c))), s.hand[s.hand.length - 1]);
  E.play(s, land.uid);
  assert.equal(s.battlefield[0].uid, land.uid);
  assert.equal(s.landsPlayed, 1);
  E.toggleTap(s, land.uid);
  assert.equal(E.untappedLands(s), 0);
  const instant = { uid: 999, name: "Bolt", type_line: "Instant", tapped: false };
  s.hand.push(instant);
  E.play(s, 999);
  assert.equal(s.graveyard[0].uid, 999);       // instants resolve to the graveyard
  const handBefore = s.hand.length;
  E.nextTurn(s);
  assert.equal(s.turn, 2);
  assert.equal(s.hand.length, handBefore + 1);
  assert.equal(E.untappedLands(s), 1);
  assert.equal(s.landsPlayed, 0);
});

test("moving cards, library top/bottom, mill, and scry", () => {
  const s = E.newGame(deck(), {}, seeded(10));
  E.keep(s);
  const card = s.hand[0];
  E.move(s, card.uid, "library", "bottom");
  assert.equal(s.library[s.library.length - 1].uid, card.uid);
  E.move(s, card.uid, "library", "top");
  assert.equal(s.library[0].uid, card.uid);
  const milled = E.mill(s, 2);
  assert.equal(s.graveyard.length, 2);
  assert.equal(milled[0].uid, card.uid);
  const [a, b, c] = s.library.slice(0, 3);
  E.resolveTop(s, [{ uid: c.uid, to: "top" }, { uid: a.uid, to: "bottom" }, { uid: b.uid, to: "hand" }]);
  assert.equal(s.library[0].uid, c.uid);
  assert.equal(s.library[s.library.length - 1].uid, a.uid);
  assert.ok(s.hand.some((x) => x.uid === b.uid));
});

test("commander tax counts casts from the command zone", () => {
  const s = E.newGame(deck(99, 37, { commanders: [{ id: "c", name: "Sheoldred", type_line: "Legendary Creature" }] }), {}, seeded(11));
  E.keep(s);
  const uid = s.command[0].uid;
  assert.equal(E.commanderTax(s, uid), 0);
  E.move(s, uid, "battlefield");
  E.move(s, uid, "command");
  assert.equal(E.commanderTax(s, uid), 2);
  E.move(s, uid, "battlefield");
  assert.equal(E.commanderTax(s, uid), 4);
});

test("tokens exist only on the battlefield", () => {
  const s = E.newGame(deck(), {}, seeded(12));
  E.keep(s);
  E.createToken(s, "Treasure", 2);
  assert.equal(s.battlefield.filter((c) => c.token).length, 2);
  E.move(s, s.battlefield[0].uid, "graveyard");
  assert.equal(s.battlefield.length, 1);
  assert.equal(s.graveyard.length, 0);
});

test("hypergeometric odds", () => {
  // 24 lands in 60: P(exactly 3 lands in 7) = C(24,3)·C(36,4) / C(60,7) ≈ 0.3087
  assert.ok(Math.abs(E.hypergeom(60, 24, 7, 3) - 0.3087) < 0.001);
  let total = 0;
  for (let k = 0; k <= 7; k++) total += E.hypergeom(60, 24, 7, k);
  assert.ok(Math.abs(total - 1) < 1e-9);
  // One copy in 99: chance to see it in the opening 7 is 7/99
  assert.ok(Math.abs(E.atLeast(99, 1, 7, 1) - 7 / 99) < 1e-9);
  assert.equal(E.cardsSeen(1, true), 7);
  assert.equal(E.cardsSeen(1, false), 8);
  assert.equal(E.cardsSeen(4, true), 10);
});
