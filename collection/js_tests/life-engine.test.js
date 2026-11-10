// Run with:  node --test collection/js_tests
const test = require("node:test");
const assert = require("node:assert/strict");
const L = require("../static/collection/life-engine.js");

function game(extra) {
  return L.newGame(Object.assign({
    format: "commander",
    players: [
      { name: "Ana", commanders: [{ name: "Meren of Clan Nel Toth" }] },
      { name: "Ben", commanders: [{ name: "Tymna the Weaver" }, { name: "Thrasios, Triton Hero" }] },
      { name: "Cy", commanders: [{ name: "Atraxa, Praetors' Voice" }] },
    ],
  }, extra));
}
const run = (s, ...actions) => actions.reduce(L.apply, s);

test("a new Commander game starts everyone at 40 with a damage row per opposing commander", () => {
  const s = game();
  assert.deepEqual(s.players.map((p) => p.life), [40, 40, 40]);
  assert.deepEqual(s.damage.p1.p2, [0, 0]);  // Ben has partners
  assert.deepEqual(s.damage.p2.p1, [0]);
  assert.equal(s.damage.p1.p1, undefined);
  assert.equal(s.commanderDamage, true);
  assert.equal(L.newGame({ format: "standard", players: [{}, {}] }).players[0].life, 20);
  assert.equal(L.newGame({ format: "custom", life: 30, players: [{}, {}] }).players[1].life, 30);
  assert.equal(L.newGame({ players: Array(9).fill({}) }).players.length, 6);
});

test("apply never changes the old state, so undo is just the previous state", () => {
  const s0 = game();
  const s1 = L.apply(s0, { type: "life", player: "p1", delta: -3 });
  assert.equal(s0.players[0].life, 40);
  assert.equal(s1.players[0].life, 37);
});

test("quick taps merge into one log line", () => {
  let s = game();
  for (let i = 0; i < 5; i++) s = L.apply(s, { type: "life", player: "p1", delta: -1 });
  const last = s.log[s.log.length - 1];
  assert.equal(last.text, "Ana −5 life (35)");
  s = L.apply(s, { type: "life", player: "p1", delta: 5 });
  assert.notEqual(s.log[s.log.length - 1].text, "Ana +0 life (40)");
});

test("commander damage also comes off life, tracked per partner, and 21 from one commander kills", () => {
  let s = game();
  s = run(s, { type: "commanderDamage", target: "p1", source: "p2", index: 0, delta: 11 },
             { type: "commanderDamage", target: "p1", source: "p2", index: 1, delta: 11 });
  assert.equal(s.players[0].life, 18);
  assert.equal(L.totalCommanderDamage(s, "p1", "p2"), 22);
  assert.deepEqual(L.lossReasons(s, "p1"), []);  // 11 + 11 from two partners is not lethal
  s = L.apply(s, { type: "commanderDamage", target: "p1", source: "p2", index: 0, delta: 10 });
  assert.match(L.lossReasons(s, "p1").join(), /21 commander damage from Tymna the Weaver/);
  assert.match(s.log.map((l) => l.text).join("\n"), /Ana is out/);
});

test("commander damage can skip life, and is ignored outside commander rules", () => {
  let s = L.apply(game(), { type: "commanderDamage", target: "p3", source: "p1", delta: 21, lifeToo: false });
  assert.equal(s.players[2].life, 40);
  assert.equal(L.lossReasons(s, "p3").length, 1);
  let b = L.newGame({ format: "brawl", players: [{ commanders: [{ name: "X" }] }, {}] });
  b = L.apply(b, { type: "commanderDamage", target: "p2", source: "p1", delta: 25 });
  assert.deepEqual(L.lossReasons(b, "p2"), ["0 life"]);
});

test("poison, counters and losing", () => {
  let s = game();
  s = L.apply(s, { type: "counter", player: "p2", kind: "poison", delta: 10 });
  assert.deepEqual(L.lossReasons(s, "p2"), ["10 poison"]);
  s = L.apply(s, { type: "counter", player: "p3", kind: "energy", delta: -2 });
  assert.equal(s.players[2].energy, 0);  // counters never go below 0
  s = L.apply(s, { type: "counter", player: "p3", kind: "nonsense", delta: 2 });
  assert.equal(s.players[2].nonsense, undefined);
  s = L.apply(s, { type: "life", player: "p3", delta: -40 });
  assert.equal(L.alive(s).length, 1);
  assert.match(s.log[s.log.length - 1].text, /Ana wins!/);
});

test("commander tax goes up by 2 per cast", () => {
  let s = run(game(), { type: "cast", player: "p2", index: 1 }, { type: "cast", player: "p2", index: 1 });
  assert.equal(L.commanderTax(s.players[1].commanders[1]), 4);
  assert.equal(L.commanderTax(s.players[1].commanders[0]), 0);
  s = L.apply(s, { type: "cast", player: "p2", index: 1, delta: -1 });
  assert.equal(L.commanderTax(s.players[1].commanders[1]), 2);
});

test("monarch and initiative have one holder; blessing can be several", () => {
  let s = run(game(), { type: "monarch", player: "p1" }, { type: "monarch", player: "p2" }, { type: "initiative", player: "p3" });
  assert.equal(s.monarch, "p2");
  assert.equal(s.initiative, "p3");
  s = L.apply(s, { type: "monarch", player: "p2" });
  assert.equal(s.monarch, null);
  s = run(s, { type: "blessing", player: "p1" }, { type: "blessing", player: "p3" });
  assert.deepEqual(s.blessing, ["p1", "p3"]);
});

test("day and night", () => {
  let s = L.apply(game(), { type: "dayNight" });
  assert.equal(s.dayNight, "day");
  s = L.apply(s, { type: "dayNight" });
  assert.equal(s.dayNight, "night");
});

test("turns go round, skip players who are out, and count rounds", () => {
  let s = game();
  s = run(s, { type: "nextTurn" }, { type: "nextTurn" });
  assert.equal(s.active, "p3");
  assert.equal(s.turn, 1);
  s = L.apply(s, { type: "nextTurn" });
  assert.equal(s.active, "p1");
  assert.equal(s.turn, 2);
  s = L.apply(s, { type: "concede", player: "p2" });
  s = L.apply(s, { type: "nextTurn" });
  assert.equal(s.active, "p3");
});

test("a player who goes out on their own turn passes the turn on", () => {
  let s = L.apply(game(), { type: "life", player: "p1", delta: -40 });
  assert.equal(s.active, "p2");
});

test("rematch keeps players and commanders, resets everything else", () => {
  let s = run(game(), { type: "life", player: "p1", delta: -7 }, { type: "cast", player: "p1" }, { type: "monarch", player: "p1" });
  const r = L.rematch(s);
  assert.equal(r.players[0].life, 40);
  assert.equal(r.players[0].commanders[0].casts, 0);
  assert.equal(r.players[0].commanders[0].name, "Meren of Clan Nel Toth");
  assert.equal(r.monarch, null);
});

test("restore only accepts a saved game of the right shape", () => {
  assert.equal(L.restore(null), null);
  assert.equal(L.restore({ version: 2 }), null);
  const s = game();
  assert.equal(L.restore(JSON.parse(JSON.stringify(s))).players.length, 3);
});

test("dice", () => {
  let n = 0;
  const rand = () => (n++ % 2 ? 0.999 : 0);
  assert.equal(L.rollDie(20, rand), 1);
  assert.equal(L.rollDie(20, rand), 20);
});
