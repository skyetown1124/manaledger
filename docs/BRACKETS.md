# Commander brackets in Mana Ledger

Research notes behind `collection/brackets.py`: what the official bracket system
says (as of October 2026), and how the app goes further with an impact score
for strong cards that aren't Game Changers.

## The official system

Wizards introduced Commander Brackets in February 2025 and has updated it since.
The October 21, 2025 update redefined the brackets mainly by intent and expected
game length. It also removed tutor limits and dropped 10 cards from the Game
Changers list. The February 9, 2026 update added Farewell and Biorhythm. Later
announcements through October 2026 left Commander unchanged.

| Bracket | Name | Intent (Wizards' summary) | You should get to play |
|---|---|---|---|
| 1 | Exhibition | Theme over power; show off a creation | 9+ turns |
| 2 | Core | Unoptimized, straightforward; incremental, telegraphed wins | 8+ turns |
| 3 | Upgraded | Strong synergy and card quality; can win in one big turn | 6+ turns |
| 4 | Optimized | Lethal, consistent, fast; efficient instant wins | 4+ turns |
| 5 | cEDH | Built for the competitive metagame | any turn |

Deck-building limits:

| | 1 | 2 | 3 | 4–5 |
|---|---|---|---|---|
| Game Changers | none | none | up to 3 | no limit |
| Mass land denial | no | no | no | allowed |
| Extra turns | no | few, not chained | few, not chained | allowed |
| Two-card infinite combos | no | no | not cheap/early (about the first six turns) | allowed |
| Tutors | no limit since Oct 2025 (the Game Changers list catches the most efficient ones) | | | |

Official definition of mass land denial: cards that "regularly destroy, exile,
and bounce other lands, keep lands tapped, or change what mana is produced by
four or more lands per player without replacing them."

Game Changers are cards that "easily and dramatically warp Commander games":
running away with resources, blocking people from playing, or tutoring for the
best cards without downside. The list has 53 cards (last changed February 9,
2026) and is in `GAME_CHANGERS`. The app also stores Scryfall's `game_changer`
flag, which stays current when the list changes.

## Why an impact score

The bracket rules only count Game Changers, mass land denial, extra turns and
two-card combos. A deck can follow every rule and still play like Bracket 4, for
example with a dozen zero-mana rocks, rituals, efficient tutors and free
counterspells, none of which are Game Changers. Wizards' own guidance is to
"bracket up" when a deck plays stronger than its list suggests. The impact score
puts a number on that, the way EDHREC's salt score does for how much players
dislike a card.

### Card categories and points

Each listed card gets 1–5 points by the same test Wizards uses for Game Changers
(how easily it warps a game, speeds you up, or stops others from playing):

| Category | What it covers | Examples (points) |
|---|---|---|
| Fast mana | Mana ahead of curve, often for free | Lotus Petal (4), Mox Opal (4), City of Traitors (4), Dark Ritual (3), turn-1 dorks (1–2) |
| Efficient tutor | Finds the best card cheaply | Demonic Consultation (4), Grim Tutor (4), Eladamri's Call (3), Entomb (3) |
| Free or cheap interaction | Stops others for little or no mana | Pact of Negation (4), Grand Abolisher (4), Mana Drain (3), Deadly Rollick (3) |
| Runaway engine | Card or mana advantage that snowballs | Necrodominance (4), Yawgmoth's Will (4), Mystic Remora (3), Esper Sentinel (3) |
| Stax and locks | Stops opponents from playing | Stasis (5), Trinisphere (4), Smokestack (4), Aven Mindcensor (3) |
| Former Game Changer | Removed from the list in Oct 2025, still game-warping | Kinnan, Urza, Winota (5); Food Chain, Deflecting Swat (4) |
| Game-warping effect | Cards players rate as most unfun (2026 salt poll) | Vivi Ornitier (4), Sen Triplets (3) |

Sol Ring and Arcane Signet score 0 on purpose: they're in nearly every
preconstructed deck, so they don't separate one bracket from another.

### Thresholds

| Impact score | Plays like at least |
|---|---|
| 8+ | Bracket 2 |
| 18+ | Bracket 3 |
| 36+ | Bracket 4 |
| 60+ | Bracket 4, with a note that it looks like a cEDH list |

These were set so typical decks land where players would put them: a
preconstructed deck scores 0–5, an upgraded deck with a few efficient tutors and
mana dorks around 15–25, and a fast-mana, tutor and free-counterspell shell 40+.
They're constants at the top of `brackets.py`, so they're easy to tune.

## How the app picks a bracket

1. **Rule floors:** Game Changers (1–3 → 3, 4+ → 4), mass land denial (→ 4),
   extra turns (1 → 2, 2+ → 4 because they can be chained), known two-card
   combos (any → 3, cheap/fast → 4), and Commander Spellbook's rating when you've
   checked it.
2. **Impact floor** from the thresholds above.
3. The deck plays at the **highest floor**. If you set an intended bracket and
   the deck plays higher, the deck page and checklist flag it with the cards
   responsible.
4. Bracket 1 vs 2 and 4 vs 5 are about intent (theme, cEDH metagame), so those
   come from the intended bracket you choose, not from the cards.

## Combos

The built-in list (`COMBOS`) has about 50 of the best-known two-card infinite
combos. For full coverage, the deck page can ask
[Commander Spellbook](https://commanderspellbook.com), the community combo
database. Its `/estimate-bracket` endpoint returns every combo in the deck and a
bracket tag, which the app maps using Spellbook's own open-source backend:
Ruthless → 4, Spicy/Powerful → 3, Oddball/Core → 2, Exhibition → 1.

## Ideas for later

- Curve and speed: a very low average mana value with lots of fast mana is a
  Bracket 4 sign even without named cards.
- Let players adjust a card's points for their own playgroup.
- Pull EDHREC salt scores to show a separate "salt" total next to the impact score.

## Sources

- Wizards: [Introducing Commander Brackets Beta](https://magic.wizards.com/en/news/announcements/introducing-commander-brackets-beta) (Feb 11, 2025)
- Wizards: [Commander Brackets Beta Update](https://magic.wizards.com/en/news/announcements/commander-brackets-beta-update-october-21-2025) (Oct 21, 2025)
- Wizards: [Commander Brackets Beta Update](https://magic.wizards.com/en/news/announcements/commander-brackets-beta-update-february-9-2026) (Feb 9, 2026)
- Wizards: [Commander format page](https://magic.wizards.com/en/formats/commander)
- [EDHcheck Game Changers list](https://edhcheck.com/game-changers/) and [commanderbrackets.com](https://commanderbrackets.com/game-changers) (cross-checked, 53 cards)
- [EDHREC 2026 salt list](https://edhrec.com/top/salt)
- [EDHREC: Top 10 Game Changer Alternatives](https://edhrec.com/articles/top-10-game-changer-alternatives)
- [edhtop16 cEDH staples by play rate](https://edhtop16.com/staples)
- [Commander Spellbook backend](https://github.com/SpaceCowMedia/commander-spellbook-backend) (bracket estimator and tag meanings)
