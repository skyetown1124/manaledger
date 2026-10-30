"""Commander bracket estimate: official rules, plus an impact score for strong cards
that aren't Game Changers.

How a deck's bracket is worked out
----------------------------------
1. Rule floors (official Wizards guidance, see docs/BRACKETS.md):
     * Game Changers: 1-3 -> at least Bracket 3; 4+ -> Bracket 4.
     * Mass land denial -> Bracket 4.
     * Extra-turn cards: one -> at least Bracket 2; two or more (can be chained) -> Bracket 4.
     * Two-card infinite combos: any -> Bracket 3; cheap ones that can come
       together in the first ~6 turns -> Bracket 4.
     * Commander Spellbook's estimate, when you've run it, is another floor.
2. Impact floor: every high-impact card that ISN'T a Game Changer has 1-5 points
   (fast mana, efficient tutors, free interaction, runaway engines, stax, cards
   that came off the Game Changers list). The total is the deck's impact score,
   and enough of it pushes the deck up even when no rule does:
     * 8+  -> at least Bracket 2
     * 18+ -> at least Bracket 3
     * 36+ -> at least Bracket 4
     * 60+ -> Bracket 4, with a note that it looks like a cEDH list
3. The deck plays at the highest floor. If you set an intended bracket and the
   deck plays higher, it's flagged. Bracket 1 vs 2 and 4 vs 5 are about intent
   (theme, cEDH metagame), so they come from your intended bracket, not the cards.

Everything here works on plain dicts so it can be tested without a database.
"""
import re
import unicodedata

BRACKET_NAMES = {1: "Exhibition", 2: "Core", 3: "Upgraded", 4: "Optimized", 5: "cEDH"}
BRACKET_TURNS = {1: "9+ turns", 2: "8+ turns", 3: "6+ turns", 4: "4+ turns", 5: "any turn"}

# Impact score thresholds: (minimum score, bracket floor)
IMPACT_THRESHOLDS = [(36, 4), (18, 3), (8, 2)]
CEDH_HINT_SCORE = 60
SCALE_MAX = 72  # right end of the impact meter on the deck page
SCALE_MARKS = [(8, "2"), (18, "3"), (36, "4"), (60, "cEDH")]

# Official Game Changers list: 53 cards, last changed February 9, 2026.
# The app also reads Scryfall's game_changer flag, which stays current; this list
# covers cards whose details haven't been refreshed yet.
GAME_CHANGERS = {
    # White
    "Drannith Magistrate", "Enlightened Tutor", "Farewell", "Humility", "Serra's Sanctum",
    "Smothering Tithe", "Teferi's Protection",
    # Blue
    "Consecrated Sphinx", "Cyclonic Rift", "Fierce Guardianship", "Force of Will", "Gifts Ungiven",
    "Intuition", "Mystical Tutor", "Narset, Parter of Veils", "Rhystic Study", "Thassa's Oracle",
    # Black
    "Ad Nauseam", "Bolas's Citadel", "Braids, Cabal Minion", "Demonic Tutor", "Imperial Seal",
    "Necropotence", "Opposition Agent", "Orcish Bowmasters", "Tergrid, God of Fright", "Vampiric Tutor",
    # Red
    "Gamble", "Jeska's Will", "Underworld Breach",
    # Green
    "Biorhythm", "Crop Rotation", "Gaea's Cradle", "Natural Order", "Seedborn Muse",
    "Survival of the Fittest", "Worldly Tutor",
    # Multicolor
    "Aura Shards", "Coalition Victory", "Grand Arbiter Augustin IV", "Notion Thief",
    # Colorless and lands
    "Ancient Tomb", "Chrome Mox", "Field of the Dead", "Glacial Chasm", "Grim Monolith",
    "Lion's Eye Diamond", "Mana Vault", "Mishra's Workshop", "Mox Diamond", "Panoptic Mirror",
    "The One Ring", "The Tabernacle at Pendrell Vale",
}

CATEGORIES = {
    "fast_mana": "Fast mana",
    "tutor": "Efficient tutor",
    "free_interaction": "Free or cheap interaction",
    "engine": "Runaway engine",
    "stax": "Stax and locks",
    "former_gc": "Former Game Changer",
    "unfun": "Game-warping effect",
}

# High-impact cards that are NOT Game Changers: name -> (points 1-5, category, why).
# Points follow the Game Changers test: how easily the card warps a game, how much
# it speeds you up, and how often it stops other people from playing.
# Sources: EDHREC 2026 salt poll, edhtop16 cEDH play rates, EDHREC "Game Changer
# alternatives", and cards Wizards removed from the list (see docs/BRACKETS.md).
IMPACT_CARDS = {
    # ---- Fast mana: mana a turn or more ahead of curve, often for zero cards' worth
    "Lotus Petal": (4, "fast_mana", "Free mana on turn 1; a cEDH staple."),
    "Mox Opal": (4, "fast_mana", "Zero-cost mana rock in artifact decks."),
    "Mox Amber": (3, "fast_mana", "Zero-cost mana with a legendary out."),
    "City of Traitors": (4, "fast_mana", "Two mana from one land drop."),
    "Dark Ritual": (3, "fast_mana", "Turn-1 three mana; powers out early threats."),
    "Cabal Ritual": (3, "fast_mana", "Ritual that can net four mana."),
    "Culling the Weak": (3, "fast_mana", "Ritual: four mana for one card."),
    "Rite of Flame": (2, "fast_mana", "Red ritual for explosive starts."),
    "Pyretic Ritual": (2, "fast_mana", "Red ritual for explosive starts."),
    "Desperate Ritual": (2, "fast_mana", "Red ritual for explosive starts."),
    "Simian Spirit Guide": (3, "fast_mana", "Free mana from hand; enables turn-1 plays."),
    "Elvish Spirit Guide": (3, "fast_mana", "Free mana from hand; enables turn-1 plays."),
    "Carpet of Flowers": (3, "fast_mana", "Called the only ramp on Smothering Tithe's level."),
    "Crystal Vein": (2, "fast_mana", "One-shot double mana land."),
    "Springleaf Drum": (2, "fast_mana", "Turns any creature into fast mana."),
    "Basalt Monolith": (2, "fast_mana", "Turn-3 five mana; also a combo piece."),
    "Nyxbloom Ancient": (3, "fast_mana", "Triples mana; swings games instantly."),
    "Birds of Paradise": (2, "fast_mana", "Most-played cEDH card; turn-1 ramp."),
    "Noble Hierarch": (2, "fast_mana", "Turn-1 ramp creature."),
    "Ignoble Hierarch": (2, "fast_mana", "Turn-1 ramp creature."),
    "Delighted Halfling": (2, "fast_mana", "Turn-1 ramp creature that protects legends."),
    "Deathrite Shaman": (2, "fast_mana", "Turn-1 ramp creature."),
    "Utopia Sprawl": (2, "fast_mana", "Turn-1 land aura ramp."),
    "Llanowar Elves": (1, "fast_mana", "Turn-1 mana creature."),
    "Elvish Mystic": (1, "fast_mana", "Turn-1 mana creature."),
    "Fyndhorn Elves": (1, "fast_mana", "Turn-1 mana creature."),
    "Avacyn's Pilgrim": (1, "fast_mana", "Turn-1 mana creature."),
    "Elves of Deep Shadow": (1, "fast_mana", "Turn-1 mana creature."),
    "Arbor Elf": (1, "fast_mana", "Turn-1 mana creature."),
    "Bloom Tender": (2, "fast_mana", "Big mana for multicolor decks; combo piece."),
    "Faeburrow Elder": (2, "fast_mana", "Big mana for multicolor decks; combo piece."),
    "Wild Growth": (1, "fast_mana", "Turn-1 land aura ramp."),

    # ---- Tutors: finds your best card, which makes the deck consistent
    "Demonic Consultation": (4, "tutor", "One-mana instant tutor and Thassa's Oracle combo half."),
    "Tainted Pact": (4, "tutor", "Two-mana instant tutor and Thassa's Oracle combo half."),
    "Grim Tutor": (4, "tutor", "Three-mana Demonic Tutor."),
    "Diabolic Intent": (3, "tutor", "Two-mana tutor to hand."),
    "Wishclaw Talisman": (3, "tutor", "Repeatable tutor if you go first."),
    "Beseech the Mirror": (3, "tutor", "Tutor that can cast what it finds for free."),
    "Eladamri's Call": (3, "tutor", "Instant creature tutor to hand."),
    "Finale of Devastation": (3, "tutor", "Creature tutor straight onto the battlefield."),
    "Green Sun's Zenith": (3, "tutor", "Creature tutor onto the battlefield."),
    "Chord of Calling": (2, "tutor", "Instant creature tutor onto the battlefield."),
    "Sylvan Tutor": (2, "tutor", "One-mana creature tutor."),
    "Idyllic Tutor": (2, "tutor", "Enchantment tutor to hand."),
    "Personal Tutor": (2, "tutor", "One-mana sorcery tutor."),
    "Solve the Equation": (2, "tutor", "Instant or sorcery tutor."),
    "Merchant Scroll": (2, "tutor", "Finds blue instants (counters, combo pieces)."),
    "Spellseeker": (3, "tutor", "Finds Demonic Consultation, Tainted Pact and similar."),
    "Imperial Recruiter": (3, "tutor", "Repeatable creature tutor."),
    "Ranger-Captain of Eos": (3, "tutor", "Tutor plus a free silence effect."),
    "Recruiter of the Guard": (2, "tutor", "Creature tutor on a body."),
    "Trophy Mage": (2, "tutor", "Artifact tutor on a body."),
    "Fabricate": (2, "tutor", "Artifact tutor."),
    "Transmute Artifact": (3, "tutor", "Artifact tutor straight onto the battlefield."),
    "Steelshaper's Gift": (2, "tutor", "One-mana equipment tutor."),
    "Muddle the Mixture": (2, "tutor", "Transmute tutor for two-drops (combo pieces)."),
    "Drift of Phantasms": (2, "tutor", "Transmute tutor for three-drops."),
    "Entomb": (3, "tutor", "One-mana tutor into the graveyard for reanimation."),
    "Buried Alive": (2, "tutor", "Graveyard tutor for reanimation."),
    "Tooth and Nail": (3, "tutor", "Tutors and puts two creatures in play; ends games."),
    "Defense of the Heart": (2, "tutor", "Puts two creatures onto the battlefield."),
    "Fauna Shaman": (3, "tutor", "Repeatable creature tutor every turn."),
    "Lim-Dûl's Vault": (2, "tutor", "Cheap instant tutor."),
    "Scheming Symmetry": (2, "tutor", "One-mana tutor."),
    "Profane Tutor": (2, "tutor", "Suspend Demonic Tutor."),
    "Diabolic Tutor": (1, "tutor", "Tutor any card."),

    # ---- Free or cheap interaction: stops other players for little or no mana
    "Pact of Negation": (4, "free_interaction", "Free counterspell; protects combos."),
    "Force of Negation": (3, "free_interaction", "Free counterspell on other turns."),
    "Mental Misstep": (3, "free_interaction", "Free counter for one-drops."),
    "Flusterstorm": (3, "free_interaction", "One-mana counter that beats counter wars."),
    "Mindbreak Trap": (3, "free_interaction", "Free counter against combo turns."),
    "Mana Drain": (3, "free_interaction", "Two-mana counter that ramps you."),
    "Swan Song": (2, "free_interaction", "One-mana counterspell."),
    "An Offer You Can't Refuse": (2, "free_interaction", "One-mana counterspell."),
    "Silence": (3, "free_interaction", "One mana to stop a whole turn of answers."),
    "Orim's Chant": (2, "free_interaction", "One mana to stop a turn of spells."),
    "Grand Abolisher": (4, "free_interaction", "Opponents can't respond on your turn; protects combos."),
    "Deadly Rollick": (3, "free_interaction", "Free removal with a commander out."),
    "Snuff Out": (3, "free_interaction", "Free removal."),
    "Misdirection": (2, "free_interaction", "Free redirect of a key spell."),
    "Commandeer": (2, "free_interaction", "Free steal of a key spell."),
    "Subtlety": (3, "free_interaction", "Free answer to a creature or planeswalker."),
    "Solitude": (3, "free_interaction", "Free creature removal."),
    "Endurance": (2, "free_interaction", "Free flash body that resets graveyards."),
    "Fury": (3, "free_interaction", "Free multi-creature removal."),
    "Chain of Vapor": (2, "free_interaction", "One-mana bounce that can clear a board."),
    "Snapback": (2, "free_interaction", "Free bounce."),
    "Veil of Summer": (2, "free_interaction", "One mana: your spells can't be countered."),
    "Autumn's Veil": (2, "free_interaction", "One mana: your spells can't be countered."),
    "Flawless Maneuver": (3, "free_interaction", "Free board protection."),
    "Silent Arbiter": (1, "free_interaction", "Shuts down go-wide attacks."),

    # ---- Engines: card or mana advantage that snowballs if unanswered
    "Mystic Remora": (3, "engine", "Draws many cards for one mana; a Rhystic Study alternative."),
    "Esper Sentinel": (3, "engine", "Taxes or draws off every opponent spell; 2nd most-played cEDH card."),
    "Sylvan Library": (3, "engine", "Card selection and draw every turn for one mana."),
    "Necrodominance": (4, "engine", "Necropotence-level draw engine."),
    "Thrasios, Triton Hero": (3, "engine", "Mana sink that turns infinite mana into a win."),
    "Tymna the Weaver": (2, "engine", "Draws several cards a turn in combat decks."),
    "Faerie Mastermind": (2, "engine", "Punishes extra draws; cEDH staple."),
    "Lotho, Corrupt Shirriff": (3, "engine", "Makes Treasure off opponents' second spells; cEDH staple."),
    "Ragavan, Nimble Pilferer": (3, "engine", "Turn-1 threat that ramps and steals cards."),
    "Wheel of Fortune": (3, "engine", "Refills your hand to seven."),
    "Timetwister": (3, "engine", "Refills your hand to seven."),
    "Echo of Eons": (3, "engine", "Refills your hand to seven."),
    "Windfall": (2, "engine", "Refills your hand."),
    "Peer into the Abyss": (2, "engine", "Draws half your library."),
    "Yawgmoth's Will": (4, "engine", "Replay your whole graveyard; Underworld Breach alternative."),
    "Mnemonic Deluge": (2, "engine", "Copies a big spell three times."),
    "Mystic Forge": (2, "engine", "Play artifacts off the top of the library."),
    "Archivist of Oghma": (2, "engine", "Draws off opponents' tutors."),
    "Smuggler's Share": (2, "engine", "Cards and Treasure off opponents' extra plays."),
    "Archaeomancer's Map": (2, "engine", "Ramp off opponents' extra lands."),
    "Sheoldred, the Apocalypse": (3, "engine", "Drains every draw; 2026 salt top 40."),
    "Displacer Kitten": (3, "engine", "Infinite-value engine with cheap spells."),
    "Hullbreaker Horror": (3, "engine", "Locks the board with bounce; combo piece."),
    "Birgi, God of Storytelling": (2, "engine", "Mana engine for storm decks."),
    "Storm-Kiln Artist": (2, "engine", "Treasure engine for spell decks."),

    # ---- Stax and locks: stop opponents from playing the game
    "Stasis": (5, "stax", "Nobody untaps; #1 on the 2026 salt list."),
    "Trinisphere": (4, "stax", "Every spell costs at least three; removed from Game Changers in 2025."),
    "Smokestack": (4, "stax", "Grinds every board down."),
    "Tangle Wire": (3, "stax", "Locks permanents tapped."),
    "Nether Void": (4, "stax", "Counters most spells unless paid for."),
    "Lodestone Golem": (3, "stax", "Taxes every non-artifact spell."),
    "Sphere of Resistance": (3, "stax", "Taxes every spell."),
    "Thorn of Amethyst": (2, "stax", "Taxes noncreature spells."),
    "Aven Mindcensor": (3, "stax", "Shuts off opponents' tutors; Opposition Agent alternative."),
    "Collector Ouphe": (3, "stax", "Shuts off artifact mana."),
    "Null Rod": (3, "stax", "Shuts off artifact mana."),
    "Stony Silence": (2, "stax", "Shuts off artifact abilities."),
    "Cursed Totem": (2, "stax", "Shuts off creature abilities."),
    "Hushbringer": (2, "stax", "Stops enter and death triggers."),
    "Torpor Orb": (2, "stax", "Stops enter triggers."),
    "Rule of Law": (2, "stax", "One spell per turn."),
    "Deafening Silence": (2, "stax", "One noncreature spell per turn."),
    "Archon of Emeria": (2, "stax", "One spell per turn."),
    "Ethersworn Canonist": (2, "stax", "One nonartifact spell per turn."),
    "Thalia, Guardian of Thraben": (2, "stax", "Taxes noncreature spells."),
    "Glowrider": (2, "stax", "Taxes noncreature spells."),
    "Stranglehold": (3, "stax", "Stops searches and extra turns."),
    "Mindlock Orb": (2, "stax", "Stops searches."),
    "Rest in Peace": (2, "stax", "Shuts off graveyards."),
    "Opposition": (3, "stax", "Taps down opponents every turn."),
    "Mana Breach": (3, "stax", "Bounces a land for every spell."),
    "Possessed Portal": (3, "stax", "Locks draws and the board."),
    "Mindslaver": (3, "stax", "Take an opponent's turn; locks with recursion."),
    "Void Winnower": (2, "stax", "Shuts off even-cost spells and blockers."),
    "Elesh Norn, Mother of Machines": (3, "stax", "Shuts off opponents' enter triggers."),
    "Toxrill, the Corrosive": (3, "stax", "Wipes every opponent board each turn; 2026 salt top 30."),
    "Grave Pact": (2, "stax", "Every creature you lose costs everyone a creature."),
    "Dictate of Erebos": (2, "stax", "Every creature you lose costs everyone a creature."),

    # ---- Removed from the Game Changers list (Oct 2025) but still game-warping
    "Food Chain": (4, "former_gc", "Infinite-mana combo engine; was a Game Changer."),
    "Deflecting Swat": (4, "former_gc", "Free redirect with a commander out; was a Game Changer."),
    "Expropriate": (4, "former_gc", "Extra turns and steals; was a Game Changer."),
    "Sway of the Stars": (3, "former_gc", "Resets the game; was a Game Changer."),
    "Jin-Gitaxias, Core Augur": (4, "former_gc", "Opponents keep no hand; was a Game Changer."),
    "Vorinclex, Voice of Hunger": (4, "former_gc", "Doubles your mana and freezes opponents' lands; was a Game Changer."),
    "Kinnan, Bonder Prodigy": (5, "former_gc", "Fast infinite-mana commander; was a Game Changer."),
    "Urza, Lord High Artificer": (5, "former_gc", "Fast infinite-mana commander; was a Game Changer."),
    "Winota, Joiner of Forces": (5, "former_gc", "Wins out of nowhere in combat; was a Game Changer."),
    "Yuriko, the Tiger's Shadow": (4, "former_gc", "Turn-1 commander that snowballs; was a Game Changer."),

    # ---- Other game-warping effects players flag as unfun (2026 salt poll)
    "Vivi Ornitier": (4, "unfun", "Commander that snowballs mana; #8 on the 2026 salt list."),
    "Jin-Gitaxias, Progress Tyrant": (3, "unfun", "Counters a spell every turn; 2026 salt top 40."),
    "Emrakul, the Promised End": (3, "unfun", "Controls an opponent's turn."),
    "Sen Triplets": (3, "unfun", "Locks an opponent out of their hand."),
    "Blightsteel Colossus": (2, "unfun", "One-hit kill with infect."),
    "Ulamog, the Defiler": (2, "unfun", "Exiles libraries."),
    "Etali, Primal Conqueror": (2, "unfun", "Steals everyone's top cards."),
    "Overwhelming Splendor": (2, "unfun", "Turns off an opponent's board."),
    "Warp World": (2, "unfun", "Randomizes everyone's board."),
    "Scrambleverse": (2, "unfun", "Randomizes everyone's board."),
    "Thieves' Auction": (2, "unfun", "Redistributes every permanent."),
    "Edgar Markov": (2, "unfun", "Snowballing vampire commander; 2026 salt top 50."),
    "Deadpool, Trading Card": (2, "unfun", "2026 salt top 60."),
    "Maha, Its Feathers Night": (2, "unfun", "Turns off opponents' toughness and abilities."),
    "Jodah, the Unifier": (2, "unfun", "Free cascades every turn."),
    "The Ur-Dragon": (2, "unfun", "Free dragons every turn."),
}

# Mass land denial (official: destroys, exiles or bounces lands, keeps them tapped,
# or changes what four or more lands per player make). Named cards, plus text patterns below.
MLD_CARDS = {
    "Armageddon", "Ravages of War", "Catastrophe", "Jokulhaups", "Obliterate", "Devastation",
    "Decree of Annihilation", "Apocalypse", "Worldfire", "Sunder", "Global Ruin", "Ruination",
    "Tectonic Break", "Boil", "Boiling Seas", "Flashfires", "Tsunami", "Acid Rain", "Epicenter",
    "Impending Disaster", "Wake of Destruction", "Thoughts of Ruin", "Keldon Firebombers",
    "Winter Orb", "Static Orb", "Rising Waters", "Blood Moon", "Magus of the Moon", "Back to Basics",
    "Winter Moon", "Hokori, Dust Drinker", "Mana Vortex", "Bearer of the Heavens",
    "Cataclysm", "Destructive Force", "Wildfire", "Burning of Xinye", "Stasis",
}
MLD_PATTERNS = [
    re.compile(r"destroy all lands", re.I),
    re.compile(r"sacrifices? (all|half|four|five|six|seven)\b[^.]*\blands", re.I),
    re.compile(r"return all lands", re.I),
    re.compile(r"exile all lands", re.I),
    re.compile(r"lands don't untap", re.I),
    re.compile(r"nonbasic lands are (mountains|islands|swamps|plains|forests)", re.I),
]

EXTRA_TURN_RE = re.compile(r"\btakes? an extra turn\b", re.I)

# Well-known two-card infinite combos (fallback when Commander Spellbook isn't checked).
# fast=True: cheap enough to come together in about the first six turns -> Bracket 4.
COMBOS = [
    ({"Thassa's Oracle", "Demonic Consultation"}, True, "Win the game with an empty library."),
    ({"Thassa's Oracle", "Tainted Pact"}, True, "Win the game with an empty library."),
    ({"Laboratory Maniac", "Demonic Consultation"}, True, "Win the game with an empty library."),
    ({"Jace, Wielder of Mysteries", "Demonic Consultation"}, True, "Win the game with an empty library."),
    ({"Jace, Wielder of Mysteries", "Tainted Pact"}, True, "Win the game with an empty library."),
    ({"Isochron Scepter", "Dramatic Reversal"}, True, "Infinite mana with nonland mana sources."),
    ({"Heliod, Sun-Crowned", "Walking Ballista"}, True, "Infinite damage."),
    ({"Kiki-Jiki, Mirror Breaker", "Zealous Conscripts"}, True, "Infinite hasty creatures."),
    ({"Kiki-Jiki, Mirror Breaker", "Pestermite"}, True, "Infinite hasty creatures."),
    ({"Kiki-Jiki, Mirror Breaker", "Deceiver Exarch"}, True, "Infinite hasty creatures."),
    ({"Kiki-Jiki, Mirror Breaker", "Village Bell-Ringer"}, True, "Infinite hasty creatures."),
    ({"Kiki-Jiki, Mirror Breaker", "Felidar Guardian"}, True, "Infinite hasty creatures."),
    ({"Kiki-Jiki, Mirror Breaker", "Restoration Angel"}, True, "Infinite hasty creatures."),
    ({"Splinter Twin", "Pestermite"}, True, "Infinite hasty creatures."),
    ({"Splinter Twin", "Deceiver Exarch"}, True, "Infinite hasty creatures."),
    ({"Splinter Twin", "Village Bell-Ringer"}, True, "Infinite hasty creatures."),
    ({"Saheeli Rai", "Felidar Guardian"}, True, "Infinite hasty creatures."),
    ({"Dualcaster Mage", "Twinflame"}, True, "Infinite hasty creatures."),
    ({"Dualcaster Mage", "Heat Shimmer"}, True, "Infinite hasty creatures."),
    ({"Basalt Monolith", "Rings of Brighthearth"}, True, "Infinite colorless mana."),
    ({"Basalt Monolith", "Power Artifact"}, True, "Infinite colorless mana."),
    ({"Grim Monolith", "Power Artifact"}, True, "Infinite colorless mana."),
    ({"Kinnan, Bonder Prodigy", "Basalt Monolith"}, True, "Infinite colorless mana."),
    ({"Devoted Druid", "Vizier of Remedies"}, True, "Infinite green mana."),
    ({"Bloom Tender", "Freed from the Real"}, True, "Infinite mana."),
    ({"Faeburrow Elder", "Freed from the Real"}, True, "Infinite mana."),
    ({"Underworld Breach", "Brain Freeze"}, True, "Mill everyone with infinite storm."),
    ({"Painter's Servant", "Grindstone"}, True, "Mill an opponent's whole library."),
    ({"Worldgorger Dragon", "Animate Dead"}, True, "Infinite enter and leave loop."),
    ({"Worldgorger Dragon", "Necromancy"}, True, "Infinite enter and leave loop."),
    ({"Worldgorger Dragon", "Dance of the Dead"}, True, "Infinite enter and leave loop."),
    ({"Thopter Foundry", "Sword of the Meek"}, True, "Infinite Thopters and life with mana."),
    ({"Ad Nauseam", "Angel's Grace"}, True, "Draw your library without dying."),
    ({"Food Chain", "Squee, the Immortal"}, True, "Infinite creature mana."),
    ({"Food Chain", "Eternal Scourge"}, True, "Infinite creature mana."),
    ({"Food Chain", "Misthollow Griffin"}, True, "Infinite creature mana."),
    ({"Exquisite Blood", "Sanguine Bond"}, False, "Infinite life drain."),
    ({"Exquisite Blood", "Vito, Thorn of the Dusk Rose"}, False, "Infinite life drain."),
    ({"Exquisite Blood", "Bloodthirsty Conqueror"}, False, "Infinite life drain."),
    ({"Niv-Mizzet, Parun", "Curiosity"}, False, "Infinite damage."),
    ({"Niv-Mizzet, Parun", "Ophidian Eye"}, False, "Infinite damage."),
    ({"Niv-Mizzet, the Firemind", "Curiosity"}, False, "Infinite damage."),
    ({"Niv-Mizzet, the Firemind", "Ophidian Eye"}, False, "Infinite damage."),
    ({"Mikaeus, the Unhallowed", "Walking Ballista"}, False, "Infinite damage."),
    ({"Mikaeus, the Unhallowed", "Triskelion"}, False, "Infinite damage."),
    ({"Peregrine Drake", "Deadeye Navigator"}, False, "Infinite mana."),
    ({"Palinchron", "Deadeye Navigator"}, False, "Infinite mana."),
]

# Commander Spellbook bracket tags -> bracket (from its open-source backend)
SPELLBOOK_TAGS = {"R": 4, "S": 3, "P": 3, "O": 2, "C": 2, "E": 1}
SPELLBOOK_TAG_NAMES = {"R": "Ruthless", "S": "Spicy", "P": "Powerful", "O": "Oddball", "C": "Core",
                       "E": "Exhibition", "B": "Banned cards"}


def normalize(name):
    text = unicodedata.normalize("NFKD", (name or "").split(" // ")[0]).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text.replace("’", "'")).strip().lower()


_GC = {normalize(n) for n in GAME_CHANGERS}
_IMPACT = {normalize(n): (n, *v) for n, v in IMPACT_CARDS.items()}
_MLD = {normalize(n) for n in MLD_CARDS}
_COMBOS = [({normalize(n) for n in pieces}, pieces, fast, result) for pieces, fast, result in COMBOS]


def is_game_changer(entry):
    return bool(entry.get("game_changer")) or normalize(entry["name"]) in _GC


def is_mld(entry):
    if normalize(entry["name"]) in _MLD:
        return True
    text = entry.get("oracle_text") or ""
    return any(p.search(text) for p in MLD_PATTERNS)


def is_extra_turn(entry):
    return bool(EXTRA_TURN_RE.search(entry.get("oracle_text") or ""))


def impact_of(entry):
    """(points, category, reason) for a non-Game-Changer impact card, else None."""
    if is_game_changer(entry):
        return None
    hit = _IMPACT.get(normalize(entry["name"]))
    if hit and hit[1] > 0:
        return hit[1], hit[2], hit[3]
    return None


def find_combos(names):
    """Known two-card combos fully present in a set of card names."""
    have = {normalize(n) for n in names}
    return [{"cards": sorted(pieces), "fast": fast, "result": result}
            for keys, pieces, fast, result in _COMBOS if keys <= have]


def estimate(entries, intended=None, spellbook=None):
    """Work out a Commander deck's bracket.

    entries:  dicts with name, quantity, oracle_text, game_changer (bool), board
    intended: the bracket the player says the deck is for (1-5) or None
    spellbook: saved Commander Spellbook result {"bracketTag": "R", "twoCardCombos": [...]} or None
    """
    playing = [e for e in entries if e.get("board", "main") != "side"]
    names = sorted({e["name"] for e in playing})
    by_name = {}
    for e in playing:
        by_name.setdefault(e["name"], e)

    floors = []   # (bracket, reason)

    gcs = [n for n in names if is_game_changer(by_name[n])]
    if len(gcs) >= 4:
        floors.append((4, f"{len(gcs)} Game Changers (Bracket 3 allows up to three)"))
    elif gcs:
        floors.append((3, f"{len(gcs)} Game Changer{'s' if len(gcs) != 1 else ''} (Brackets 1-2 allow none)"))

    mld = [n for n in names if is_mld(by_name[n])]
    if mld:
        floors.append((4, "Mass land denial: " + ", ".join(mld)))

    turns = [n for n in names if is_extra_turn(by_name[n])]
    if len(turns) >= 2:
        floors.append((4, f"{len(turns)} extra-turn cards can be chained"))
    elif turns:
        floors.append((2, f"Extra-turn card: {turns[0]} (none in Bracket 1)"))

    combos = find_combos(names)
    for c in combos:
        if c["fast"]:
            floors.append((4, f"Fast two-card combo: {' + '.join(c['cards'])}"))
        else:
            floors.append((3, f"Two-card combo: {' + '.join(c['cards'])} (none in Brackets 1-2)"))

    if spellbook and spellbook.get("bracketTag") in SPELLBOOK_TAGS:
        tag = spellbook["bracketTag"]
        floors.append((SPELLBOOK_TAGS[tag], f"Commander Spellbook rates the combos and cards {SPELLBOOK_TAG_NAMES[tag]}"))

    impact_cards = []
    for n in names:
        hit = impact_of(by_name[n])
        if hit:
            points, category, reason = hit
            impact_cards.append({"name": n, "points": points, "category": category,
                                 "category_label": CATEGORIES[category], "reason": reason})
    impact_cards.sort(key=lambda c: (-c["points"], c["name"]))
    score = sum(c["points"] for c in impact_cards)
    by_category = {}
    for c in impact_cards:
        by_category[c["category_label"]] = by_category.get(c["category_label"], 0) + c["points"]
    for threshold, bracket in IMPACT_THRESHOLDS:
        if score >= threshold:
            floors.append((bracket, f"Impact score {score} (non-Game-Changer power cards; {threshold}+ plays like Bracket {bracket})"))
            break

    computed = max((b for b, _ in floors), default=1)
    reasons = [r for b, r in sorted(floors, key=lambda f: -f[0]) if b == computed]

    effective = computed
    if intended:
        intended = int(intended)
        if intended > computed:
            effective = intended  # built for a higher table (e.g. cEDH): honor it
        elif computed <= 2 and intended in (1, 2):
            effective = intended  # Exhibition vs Core is about theme, not cards
    elif computed == 1:
        effective = 2  # nothing pushes it up; Bracket 1 is a theme choice the player makes

    plays_above = bool(intended) and computed > intended
    return {
        "bracket": effective,
        "name": BRACKET_NAMES[effective],
        "turns": BRACKET_TURNS[effective],
        "computed": computed,
        "intended": intended,
        "plays_above": plays_above,
        "reasons": reasons,
        "floors": sorted(({"bracket": b, "reason": r} for b, r in floors), key=lambda f: -f["bracket"]),
        "game_changers": gcs,
        "mld": mld,
        "extra_turns": turns,
        "combos": combos,
        "impact_cards": impact_cards,
        "impact_score": score,
        "score_pct": min(100, round(100 * score / SCALE_MAX)),
        "scale_marks": [{"label": label, "at": round(100 * at / SCALE_MAX)} for at, label in SCALE_MARKS],
        "impact_by_category": sorted(by_category.items(), key=lambda kv: -kv[1]),
        "cedh_hint": score >= CEDH_HINT_SCORE and effective < 5,
        "low_note": computed <= 2,
    }
