"""Upgrade plan: swaps that take a Commander deck UP to a higher bracket (up to cEDH).

The candidates are the cards the bracket estimate already knows are strong (the
Game Changers and the impact list in brackets.py) plus a list of high-power
staples that aren't on either: efficient interaction, card draw, mana rocks and
the lands cEDH decks play. Cards you own come first; the rest show what they cost.

Each addition is paired with a cut: the deck's weakest cards (no clear job,
expensive to cast, not part of a combo), basic and tapped lands for land upgrades,
and at cEDH a few extra lands, since fast mana lets those decks run about 30.

Everything here works on plain dicts so it can be tested without a database:
    entries:    views._deck_entries rows (name, board, quantity, type_line, oracle_text, cmc,
                color_identity, game_changer, ...)
    candidates: {name: card dict} for staples, with card_id, name, type_line, oracle_text,
                mana_cost, cmc, color_identity, legalities, game_changer, price, image_url, owned
"""
import re
from collections import Counter

from . import brackets
from .suggest import roles_of, themes_of, deck_themes, identity_of, is_land

# High-power cards that aren't Game Changers or on the impact list: (role, why)
STAPLES = {
    # Mana rocks and lands
    "Sol Ring": ("fast_mana", "The best mana rock in Commander."),
    "Arcane Signet": ("fast_mana", "Two-mana rock that makes any of your colors."),
    "Fellwar Stone": ("fast_mana", "Two-mana rock that fixes colors."),
    "Talisman of Dominance": ("fast_mana", "Two-mana rock (blue/black)."),
    "Talisman of Indulgence": ("fast_mana", "Two-mana rock (black/red)."),
    "Talisman of Impulse": ("fast_mana", "Two-mana rock (red/green)."),
    "Talisman of Unity": ("fast_mana", "Two-mana rock (green/white)."),
    "Talisman of Progress": ("fast_mana", "Two-mana rock (white/blue)."),
    "Talisman of Hierarchy": ("fast_mana", "Two-mana rock (white/black)."),
    "Talisman of Creativity": ("fast_mana", "Two-mana rock (blue/red)."),
    "Talisman of Resilience": ("fast_mana", "Two-mana rock (black/green)."),
    "Talisman of Conviction": ("fast_mana", "Two-mana rock (red/white)."),
    "Talisman of Curiosity": ("fast_mana", "Two-mana rock (green/blue)."),
    # Interaction
    "Swords to Plowshares": ("interaction", "One-mana instant removal."),
    "Path to Exile": ("interaction", "One-mana instant removal."),
    "Silence": ("interaction", "Stops opponents casting spells on your combo turn."),
    "Orim's Chant": ("interaction", "Stops opponents casting spells on your combo turn."),
    "Grand Abolisher": ("interaction", "Protects your turn from instant-speed answers."),
    "Counterspell": ("interaction", "Two-mana hard counter."),
    "Mana Drain": ("interaction", "Counter that ramps you."),
    "Swan Song": ("interaction", "One-mana counter for combo pieces."),
    "Flusterstorm": ("interaction", "One-mana counter for the stack war."),
    "Mental Misstep": ("interaction", "Free counter for one-drops."),
    "Spell Pierce": ("interaction", "One-mana counter."),
    "Stern Scolding": ("interaction", "One-mana counter for big creatures."),
    "Chain of Vapor": ("interaction", "One-mana bounce; also a combo enabler."),
    "Rapid Hybridization": ("interaction", "One-mana creature answer."),
    "Fatal Push": ("interaction", "One-mana instant removal."),
    "Snuff Out": ("interaction", "Free removal."),
    "Deadly Rollick": ("interaction", "Free exile removal with a commander out."),
    "Pyroblast": ("interaction", "One-mana answer to blue spells."),
    "Red Elemental Blast": ("interaction", "One-mana answer to blue spells."),
    "Veil of Summer": ("interaction", "One-mana protection from blue and black."),
    "Nature's Claim": ("interaction", "One-mana artifact and enchantment answer."),
    "Assassin's Trophy": ("interaction", "Two-mana instant answer to anything."),
    "Abrupt Decay": ("interaction", "Uncounterable cheap removal."),
    # Card draw and engines
    "Mystic Remora": ("draw", "Draws a pile of cards early for one mana."),
    "Esper Sentinel": ("draw", "One-mana draw engine."),
    "Sylvan Library": ("draw", "Card selection and draw every turn."),
    "Dark Confidant": ("draw", "Two-mana card advantage."),
    "Phyrexian Arena": ("draw", "Extra card every turn."),
    "Night's Whisper": ("draw", "Two cards for two mana."),
    "Sign in Blood": ("draw", "Two cards for two mana."),
    "Wheel of Fortune": ("draw", "Refills your hand."),
    "Windfall": ("draw", "Refills your hand."),
    "Brainstorm": ("draw", "One-mana selection."),
    "Ponder": ("draw", "One-mana selection."),
    "Preordain": ("draw", "One-mana selection."),
    "Gitaxian Probe": ("draw", "Free cantrip."),
    "Skullclamp": ("draw", "Huge card advantage with small creatures."),
    "Entomb": ("tutor", "Puts any card in your graveyard for one mana."),
    "Reanimate": ("engine", "One-mana reanimation."),
    "Animate Dead": ("engine", "Two-mana reanimation."),
    # Lands
    "Command Tower": ("land", "Makes every color your commander has."),
    "City of Brass": ("land", "Makes any color."),
    "Mana Confluence": ("land", "Makes any color."),
    "Exotic Orchard": ("land", "Makes colors your opponents can."),
    "Forbidden Orchard": ("land", "Makes any color untapped."),
    "Gemstone Caverns": ("land", "Free extra mana if you don't start the game."),
    "Urborg, Tomb of Yawgmoth": ("land", "Makes every land tap for black."),
    "Boseiju, Who Endures": ("land", "Land that's also removal."),
    "Otawara, Soaring City": ("land", "Land that's also a bounce spell."),
    "Takenuma, Abandoned Mire": ("land", "Land that's also recursion."),
    "Cavern of Souls": ("land", "Makes your creatures uncounterable."),
    # Fetches, duals and shocks (each pair named by colors so they only show in decks that use them)
    "Polluted Delta": ("land", "Fetch land (Island/Swamp)."), "Bloodstained Mire": ("land", "Fetch land (Swamp/Mountain)."),
    "Wooded Foothills": ("land", "Fetch land (Mountain/Forest)."), "Windswept Heath": ("land", "Fetch land (Forest/Plains)."),
    "Flooded Strand": ("land", "Fetch land (Plains/Island)."), "Marsh Flats": ("land", "Fetch land (Plains/Swamp)."),
    "Scalding Tarn": ("land", "Fetch land (Island/Mountain)."), "Verdant Catacombs": ("land", "Fetch land (Swamp/Forest)."),
    "Arid Mesa": ("land", "Fetch land (Mountain/Plains)."), "Misty Rainforest": ("land", "Fetch land (Forest/Island)."),
    "Underground Sea": ("land", "Original dual land."), "Badlands": ("land", "Original dual land."),
    "Taiga": ("land", "Original dual land."), "Savannah": ("land", "Original dual land."),
    "Tundra": ("land", "Original dual land."), "Scrubland": ("land", "Original dual land."),
    "Volcanic Island": ("land", "Original dual land."), "Bayou": ("land", "Original dual land."),
    "Plateau": ("land", "Original dual land."), "Tropical Island": ("land", "Original dual land."),
    "Watery Grave": ("land", "Shock land."), "Blood Crypt": ("land", "Shock land."),
    "Stomping Ground": ("land", "Shock land."), "Temple Garden": ("land", "Shock land."),
    "Hallowed Fountain": ("land", "Shock land."), "Godless Shrine": ("land", "Shock land."),
    "Steam Vents": ("land", "Shock land."), "Overgrown Tomb": ("land", "Shock land."),
    "Sacred Foundry": ("land", "Shock land."), "Breeding Pool": ("land", "Shock land."),
}

# Game Changers the planner suggests, by job. Others on the list (Glacial Chasm, Field of the
# Dead, Panoptic Mirror, Coalition Victory...) only belong in decks built around them.
GC_KIND = {
    "Demonic Tutor": "tutor", "Vampiric Tutor": "tutor", "Imperial Seal": "tutor", "Mystical Tutor": "tutor",
    "Enlightened Tutor": "tutor", "Worldly Tutor": "tutor", "Gamble": "tutor", "Intuition": "tutor",
    "Natural Order": "tutor", "Crop Rotation": "tutor",
    "Mana Vault": "fast_mana", "Grim Monolith": "fast_mana", "Chrome Mox": "fast_mana", "Mox Diamond": "fast_mana",
    "Jeska's Will": "fast_mana",
    "Ancient Tomb": "land", "Gaea's Cradle": "land",
    "Force of Will": "interaction", "Fierce Guardianship": "interaction", "Cyclonic Rift": "interaction",
    "Teferi's Protection": "interaction", "Opposition Agent": "interaction", "Orcish Bowmasters": "interaction",
    "Drannith Magistrate": "interaction", "Narset, Parter of Veils": "interaction",
    "Rhystic Study": "draw", "Necropotence": "draw", "Ad Nauseam": "draw", "Smothering Tithe": "draw",
    "The One Ring": "draw", "Notion Thief": "draw", "Consecrated Sphinx": "draw",
    "Bolas's Citadel": "engine", "Survival of the Fittest": "engine", "Seedborn Muse": "engine",
    "Tergrid, God of Fright": "engine",
}

# Combo wins worth adding at Bracket 4-5 when the deck has none: pieces, why.
WIN_COMBOS = [
    (("Thassa's Oracle", "Demonic Consultation"), "Two-card win: the most played cEDH finish."),
    (("Thassa's Oracle", "Tainted Pact"), "Two-card win with an empty library."),
    (("Underworld Breach", "Brain Freeze"), "Mill everyone out with the graveyard (better with Lion's Eye Diamond)."),
    (("Isochron Scepter", "Dramatic Reversal"), "Infinite mana with nonland mana rocks."),
    (("Kiki-Jiki, Mirror Breaker", "Zealous Conscripts"), "Infinite hasty creatures."),
    (("Heliod, Sun-Crowned", "Walking Ballista"), "Infinite damage."),
    (("Mikaeus, the Unhallowed", "Walking Ballista"), "Infinite damage."),
    (("Food Chain", "Eternal Scourge"), "Infinite creature mana."),
    (("Grim Monolith", "Power Artifact"), "Infinite colorless mana."),
]

ROLE_LABELS = {
    "fast_mana": "Fast mana", "tutor": "Tutor", "interaction": "Interaction", "free_interaction": "Interaction",
    "draw": "Card draw", "engine": "Engine", "land": "Mana base", "combo": "Win condition",
    "stax": "Stax", "former_gc": "Power card", "unfun": "Power card",
}
# How many additions of each kind to aim for: Bracket 3 / 4 / 5.
QUOTAS = {
    3: {"fast_mana": 2, "tutor": 1, "interaction": 2, "draw": 1, "engine": 1, "land": 3, "game_changer": 3},
    4: {"fast_mana": 5, "tutor": 4, "interaction": 5, "draw": 3, "engine": 2, "land": 6, "game_changer": 99},
    5: {"fast_mana": 9, "tutor": 7, "interaction": 9, "draw": 5, "engine": 3, "land": 12, "game_changer": 99},
}
CEDH_LANDS = 31   # cEDH decks run about 28-31 lands thanks to fast mana
TAPPED_RE = re.compile(r"enters(?: the battlefield)? tapped", re.I)


def candidate_names():
    """Every card name the planner may suggest (to look up once from Scryfall)."""
    names = set(brackets.GAME_CHANGERS) | set(brackets.IMPACT_CARDS) | set(STAPLES)
    for pieces, _ in WIN_COMBOS:
        names |= set(pieces)
    return sorted(names)


def _kind(name, card):
    """The planner's bucket for a candidate: land, fast_mana, tutor, interaction, draw, engine or game_changer."""
    if is_land(card):
        return "land"
    if brackets.is_game_changer(card):
        return GC_KIND.get(name)  # None: a Game Changer the planner doesn't suggest
    hit = brackets.IMPACT_CARDS.get(name)
    if hit:
        cat = hit[1]
        return {"free_interaction": "interaction", "former_gc": "engine", "unfun": "engine", "stax": "engine"}.get(cat, cat)
    return STAPLES.get(name, ("engine", ""))[0]


def _why(name, card):
    if brackets.is_game_changer(card):
        return "Game Changer: one of the most powerful cards in Commander."
    hit = brackets.IMPACT_CARDS.get(name)
    if hit:
        return hit[2]
    return STAPLES.get(name, ("", ""))[1]


def _power(entry, themes, combo_names):
    """How much a card in the deck pulls its weight (low = cut first)."""
    if entry["name"] in combo_names:
        return 99
    if brackets.is_game_changer(entry):
        return 50
    pts = 0.0
    hit = brackets.impact_of(entry)
    if hit:
        pts += 10 + hit[0] * 3
    if entry["name"] in STAPLES:
        pts += 12
    roles = roles_of(entry)
    pts += 4 * len(roles & {"tutor", "interaction", "wipe"}) + 3 * len(roles & {"ramp", "draw"})
    pts += 2 * len(themes_of(entry) & set(themes))
    cmc = entry.get("cmc") or 0
    pts += 3 if cmc <= 2 else 1 if cmc <= 3 else -(cmc - 3) * 1.5
    return pts


def _land_rank(entry):
    """Which lands to replace first: basics, then lands that enter tapped, then the rest."""
    text = entry.get("oracle_text") or ""
    if entry.get("is_basic"):
        return 0
    if TAPPED_RE.search(text) and "unless" not in text and entry["name"] not in STAPLES:
        return 1
    return 9  # utility and untapped lands: keep


def plan(entries, candidates, target, max_swaps=40):
    """The upgrade plan for a Commander deck. Returns a dict for the suggestions page."""
    from .brackets import estimate

    playing = [e for e in entries if e.get("board") in ("commander", "main")]
    current = estimate(playing)
    target = max(3, min(5, int(target or 3)))
    identity, has_commander = identity_of(playing)
    in_deck = {e["name"] for e in entries}
    themes, _tribe = deck_themes(playing)
    combo_names = {n for c in current["combos"] for n in c["cards"]}

    # ---- What to add, best first, within each kind's quota
    pool = []
    for name, card in candidates.items():
        if name in in_deck or not card:
            continue
        if (card.get("legalities") or {}).get("commander") not in ("legal", None):
            continue
        if not set(card.get("color_identity") or "") <= identity:
            continue
        if name in ("Command Tower",) and len(identity) < 2:
            continue
        kind = _kind(name, card)
        hit = brackets.IMPACT_CARDS.get(name)
        if kind is None or (hit and hit[1] in ("stax", "unfun") and target < 5):
            continue  # locks and game-warping cards only for cEDH
        gc = brackets.is_game_changer(card)
        pts = (8 if gc else 0) + (hit[0] * 2 if hit else 0) + (3 if name in STAPLES else 0)
        pts += 6 if card.get("owned") else 0
        price = card.get("price")
        pts -= 0 if price is None else min(4, (price or 0) / 50)  # gentle nudge toward cheaper cards
        pool.append(dict(card, kind=kind, gc=gc, why=_why(name, card), rank=pts))
    pool.sort(key=lambda c: (-c["rank"], c["name"]))

    quotas = dict(QUOTAS[target])
    gc_room = quotas.pop("game_changer") - len(current["game_changers"])
    adds, taken = [], set()

    # A win condition first at Bracket 4-5, if the deck has no fast combo yet.
    if target >= 4 and not any(c["fast"] for c in current["combos"]):
        for pieces, why in WIN_COMBOS:
            cards = [candidates.get(p) for p in pieces]
            if all(cards) and all(set(c.get("color_identity") or "") <= identity for c in cards):
                missing = [c for c in cards if c["name"] not in in_deck]
                if missing and len(missing) <= 2:
                    for c in missing:
                        adds.append(dict(c, kind="combo", why=f"Combo with {' + '.join(pieces)}: {why}"))
                        taken.add(c["name"])
                    break

    for card in pool:
        if card["name"] in taken:
            continue
        kind = card["kind"]
        if quotas.get(kind, 0) <= 0 or (card["gc"] and gc_room <= 0):
            continue
        if target == 3:  # keep it a Bracket 3 deck: no early two-card combos, impact under the line
            trial = playing + [dict(a, board="main", quantity=1) for a in adds] + [dict(card, board="main", quantity=1)]
            if estimate(trial)["computed"] > 3:
                continue
        quotas[kind] -= 1
        gc_room -= 1 if card["gc"] else 0
        adds.append(card)
        taken.add(card["name"])
        if len(adds) >= max_swaps:
            break

    # ---- What to cut
    lands = [e for e in playing if is_land(e) and e.get("board") == "main"]
    land_slots = []  # one entry per copy that could go, best candidates first
    for e in sorted(lands, key=lambda e: (_land_rank(e), e["name"])):
        if _land_rank(e) < 9:
            land_slots += [e] * e["quantity"]
    spells = [e for e in playing if not is_land(e) and e.get("board") == "main"]
    spell_slots = sorted(spells, key=lambda e: (_power(e, themes, combo_names), e["name"]))

    land_adds = [a for a in adds if a["kind"] == "land"]
    spell_adds = [a for a in adds if a["kind"] != "land"]
    land_count = sum(e["quantity"] for e in lands)
    extra_lands = max(0, land_count - CEDH_LANDS) if target == 5 else 0

    swaps = []
    for a in land_adds:
        if not land_slots:
            break
        cut = land_slots.pop(0)
        swaps.append({"cut": cut, "add": a, "cut_why": "Basic land" if cut.get("is_basic") else "Enters tapped"})
    for a in spell_adds:
        if extra_lands and land_slots:
            cut = land_slots.pop(0)
            extra_lands -= 1
            swaps.append({"cut": cut, "add": a, "cut_why": f"Extra land: cEDH decks run about {CEDH_LANDS} with fast mana"})
            continue
        while spell_slots and _power(spell_slots[0], themes, combo_names) >= 30:
            spell_slots.pop(0)  # never cut the deck's real power cards or combo pieces
        if not spell_slots:
            break
        cut = spell_slots.pop(0)
        cmc = cut.get("cmc") or 0
        reason = (f"Costs {int(cmc)} mana" if cmc >= 5 else
                  "No clear job in the deck" if not roles_of(cut) else "Weaker than the upgrade")
        swaps.append({"cut": cut, "add": a, "cut_why": reason})

    # ---- Where the deck lands after the swaps
    after = [dict(e) for e in playing]
    for s in swaps:
        for e in after:
            if e is not None and e["name"] == s["cut"]["name"] and e["board"] == s["cut"]["board"]:
                e["quantity"] -= 1
                break
        after.append(dict(s["add"], board="main", quantity=1))
    after = [e for e in after if e["quantity"] > 0]
    new_est = estimate(after, intended=target)
    for i, s in enumerate(swaps):
        s["key"] = f'{s["cut"]["id"]}:{s["add"]["card_id"]}'
        s["kind_label"] = ROLE_LABELS.get(s["add"]["kind"], "Upgrade")
        s["index"] = i
    buy = [s for s in swaps if not s["add"].get("owned")]
    return {
        "target": target, "target_name": brackets.BRACKET_NAMES[target],
        "current": current["computed"], "current_score": current["impact_score"],
        "after": new_est["computed"] if target < 5 else (5 if new_est["computed"] >= 4 else new_est["computed"]),
        "after_score": new_est["impact_score"], "after_combos": new_est["combos"],
        "swaps": swaps, "owned_count": len(swaps) - len(buy), "buy_count": len(buy),
        "buy_cost": round(sum(s["add"].get("price") or 0 for s in buy), 2),
        "unpriced": sum(1 for s in buy if s["add"].get("price") is None),
        "land_count": land_count, "has_commander": has_commander,
        "by_kind": Counter(s["kind_label"] for s in swaps).most_common(),
    }
