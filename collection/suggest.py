"""Deck suggestions from your own collection, kept at the power level you want.

Plain Python on dicts (no database), like brackets.py, so it's easy to test.

For one deck:
  1. Roles: count ramp, card draw, interaction, board wipes and lands, and compare
     with a common Commander template (36-38 lands, ~10 ramp, ~10 draw, ~10
     interaction, 2-3 wipes). Short roles get priority.
  2. Themes: find what the deck is about (tokens, counters, sacrifice, graveyard,
     a creature type, ...) from its cards' rules text, the commander counting double.
  3. Candidates: cards you own that aren't in the deck, are legal in the format and
     fit the commander's color identity.
  4. Power level: each candidate is added to the deck and the bracket estimate is run
     again. Anything that would push the deck above the target bracket is left out
     (Game Changer limits, mass land denial, chained extra turns, combos, impact score).
  5. If the deck already plays above its target, cuts that bring it back down are
     suggested, each paired with a replacement from your collection in the same role.
"""
import re
from collections import Counter

from . import brackets

# ------------------------------------------------------------------ roles

ROLE_PATTERNS = {
    "ramp": [
        r"\badd \{", r"\badd (one|two|three) mana\b", r"\badd an amount of\b",
        r"search your library for (a|an|up to \w+) (basic )?(land|forest|plains|island|swamp|mountain)",
        r"put (a|an|up to \w+) (basic )?land cards? .*onto the battlefield",
        r"\bcreate (a|an|\w+) treasure\b", r"you may play an additional land",
    ],
    "draw": [
        r"\bdraws? (a|an|one|two|three|four|five|x|that many|\d+) cards?\b",
        r"\bdraw cards equal\b", r"\bexile the top .* you may play\b",
        r"\binvestigate\b", r"\bconnive", r"\bscry \d+, then draw\b",
    ],
    "interaction": [
        r"\b(destroy|exile) target\b", r"\breturn target (nonland )?(permanent|creature|artifact|enchantment)[^.]* to its owner's hand",
        r"\bdeals? (\d+|x) damage to (target|any target|another target)", r"\bcounter target\b",
        r"\btarget (creature|permanent) gets -\d+/-\d+", r"\bfights? (target|up to one target)\b",
        r"\btarget player sacrifices\b", r"\beach opponent sacrifices\b",
    ],
    "wipe": [
        r"\b(destroy|exile) all (creatures|nonland permanents|other creatures|artifacts|enchantments)\b",
        r"\ball creatures get -", r"\bdeals? \d+ damage to each creature\b",
        r"\breturn all (creatures|nonland permanents)", r"\beach player sacrifices (all|\w+ creatures)",
    ],
    "tutor": [r"search your library for an? (card|creature|artifact|enchantment|instant|sorcery)\b(?![^.]*\bland\b)"],
}
_ROLE_RE = {role: [re.compile(p, re.I) for p in pats] for role, pats in ROLE_PATTERNS.items()}

ROLE_LABELS = {
    "land": "Lands", "ramp": "Ramp", "draw": "Card draw", "interaction": "Removal & interaction",
    "wipe": "Board wipes", "tutor": "Tutors", "synergy": "Themes & power",
}

# Typical Commander deck (Command Zone template and EDHREC averages, rounded).
COMMANDER_TARGETS = {"land": 37, "ramp": 10, "draw": 10, "interaction": 10, "wipe": 3}
SIXTY_TARGETS = {"land": 24, "interaction": 8, "draw": 4}


def front_type(card):
    return (card.get("type_line") or "").split("//")[0]


def is_land(card):
    return bool(re.search(r"\bLand\b", front_type(card)))


def roles_of(card):
    """The roles a card plays: subset of land, ramp, draw, interaction, wipe, tutor."""
    roles = set()
    if is_land(card):
        roles.add("land")
        return roles
    text = card.get("oracle_text") or ""
    for role, patterns in _ROLE_RE.items():
        if any(p.search(text) for p in patterns):
            roles.add(role)
    if "wipe" in roles:
        roles.discard("interaction")  # count a Wrath as a wipe, not spot removal
    return roles


# ------------------------------------------------------------------ themes

THEMES = {
    "tokens": ("Tokens", r"\bcreate[s]? [^.]*token"),
    "counters": ("+1/+1 counters", r"\+1/\+1 counter"),
    "sacrifice": ("Sacrifice", r"\bsacrifice (a|an|another|one|two|x) (creature|permanent|artifact)|\bwhenever [^.]*dies\b"),
    "graveyard": ("Graveyard", r"\bfrom your graveyard\b|\bmill\b|\bput into your graveyard\b|\bin your graveyard\b"),
    "lifegain": ("Life gain", r"\byou gain (\d+ |x )?life\b|\bwhenever you gain life\b|\blifelink\b"),
    "spells": ("Instants & sorceries", r"\binstant or sorcery\b|\bnoncreature spell\b|\bmagecraft\b"),
    "artifacts": ("Artifacts", r"\bartifacts? you control\b|\bwhenever (an|another) artifact\b"),
    "enchantments": ("Enchantments", r"\benchantments? you control\b|\bconstellation\b|\baura\b"),
    "landfall": ("Lands & landfall", r"\blandfall\b|\bwhenever a land enters\b|\bplay an additional land\b"),
    "attack": ("Attacking", r"\bwhenever [^.]* attacks\b|\bcombat damage to a player\b"),
    "flyers": ("Flying", r"\bflying\b"),
    "etb": ("Enters-the-battlefield", r"\bwhen [^.]* enters\b|\bblink\b|\bflicker\b"),
    "draw_matters": ("Drawing cards", r"\bwhenever you draw\b"),
    "discard": ("Discard", r"\bdiscard"),
}
_THEME_RE = {k: re.compile(v[1], re.I) for k, v in THEMES.items()}

CREATURE_TYPE_SKIP = {"Human", "Legendary", "—", "Creature"}


def creature_types(card):
    t = front_type(card)
    if "Creature" not in t or "—" not in t:
        return []
    return [w for w in t.split("—", 1)[1].split() if w not in CREATURE_TYPE_SKIP]


def themes_of(card):
    text = card.get("oracle_text") or ""
    return {k for k, rx in _THEME_RE.items() if rx.search(text)}


def deck_themes(entries):
    """{theme: strength} for themes common enough to build around, plus a tribe if there is one."""
    nonland = [e for e in entries if not is_land(e)]
    counts = Counter()
    tribes = Counter()
    for e in nonland:
        weight = 2 if e.get("board") == "commander" else 1
        for t in themes_of(e):
            counts[t] += weight
        for ct in creature_types(e):
            tribes[ct] += weight
        if e.get("board") == "commander":
            for word in re.findall(r"\b([A-Z][a-z]+)s?\b", e.get("oracle_text") or ""):
                if word in tribes:
                    tribes[word] += 2
    need = max(3, round(len(nonland) * 0.12))
    active = {t: n for t, n in counts.items() if n >= need or (n >= 2 and _commander_has(entries, t))}
    tribe = None
    if tribes:
        name, n = tribes.most_common(1)[0]
        if n >= max(5, need):
            tribe = name
    return active, tribe


def _commander_has(entries, theme):
    return any(e.get("board") == "commander" and theme in themes_of(e) for e in entries)


# ------------------------------------------------------------------ the deck as it stands

def role_counts(entries):
    counts = Counter()
    for e in entries:
        if e.get("board") == "side":
            continue
        for r in roles_of(e):
            counts[r] += e.get("quantity", 1)
    return counts


def role_gaps(entries, fmt):
    targets = COMMANDER_TARGETS if fmt == "commander" else SIXTY_TARGETS
    counts = role_counts(entries)
    rows = []
    for role, target in targets.items():
        have = counts.get(role, 0)
        rows.append({"role": role, "label": ROLE_LABELS[role], "have": have, "target": target,
                     "gap": max(0, target - have), "pct": min(100, round(100 * have / target)) if target else 100})
    return rows


def identity_of(entries):
    """Commander color identity; without a commander, the deck's colors."""
    commanders = [e for e in entries if e.get("board") == "commander"]
    source = commanders or [e for e in entries if e.get("board") != "side"]
    letters = set()
    for e in source:
        letters |= set(e.get("color_identity") or "")
    return letters, bool(commanders)


# ------------------------------------------------------------------ power level

def target_bracket(intended, current):
    """The bracket to keep: the one you set, else where the deck already plays."""
    return int(intended) if intended else current


def budget(entries, target):
    """What room is left before the deck would play above `target`."""
    est = brackets.estimate(entries)
    gc_allowed = 0 if target <= 2 else 3 if target == 3 else None
    next_threshold = {1: 8, 2: 18, 3: 36}.get(target)
    return {
        "game_changers": len(est["game_changers"]),
        "game_changers_allowed": gc_allowed,
        "game_changers_left": None if gc_allowed is None else max(0, gc_allowed - len(est["game_changers"])),
        "impact_score": est["impact_score"],
        "impact_limit": None if next_threshold is None else next_threshold - 1,
        "impact_left": None if next_threshold is None else max(0, next_threshold - 1 - est["impact_score"]),
        "computed": est["computed"],
    }


def fits_bracket(entries, card, target):
    """True when adding `card` keeps the deck's computed bracket at or below `target`."""
    if target >= 4:
        return True  # Brackets 4-5 allow everything this app measures
    trial = entries + [dict(card, board="main", quantity=1)]
    return brackets.estimate(trial)["computed"] <= target


def cuts_to_reach(entries, target):
    """Cards to cut so the deck plays at `target` or lower. [(entry, reason)]"""
    if target >= 4:
        return []
    working = [e for e in entries]
    cuts = []

    def remove(name, reason):
        """Cut every copy of a card (it may be in the main deck and the sideboard)."""
        nonlocal working
        hits = [e for e in working if e["name"] == name and e.get("board") != "commander"]
        if hits:
            working = [e for e in working if e not in hits]
            cuts.append((hits[0], reason, hits))

    for _ in range(60):  # each pass removes one card; stop once the deck fits
        est = brackets.estimate(working)
        if est["computed"] <= target:
            break
        before = len(cuts)
        gc_allowed = 0 if target <= 2 else 3
        if len(est["game_changers"]) > gc_allowed:
            # Keep the commander; cut the Game Changers that add least to the deck's roles first.
            names = [n for n in est["game_changers"]
                     if not any(e["name"] == n and e.get("board") == "commander" for e in working)]
            if names:
                remove(sorted(names, key=lambda n: (len(roles_of(_entry(working, n))), n))[0],
                       f"Game Changer: Bracket {target} allows {gc_allowed or 'none'}")
        elif est["mld"]:
            remove(est["mld"][0], "Mass land denial isn't allowed below Bracket 4")
        elif len(est["extra_turns"]) >= 2 or (target == 1 and est["extra_turns"]):
            remove(est["extra_turns"][-1], "Extra-turn cards can be chained" if target > 1 else "No extra turns in Bracket 1")
        elif est["combos"] and (target <= 2 or any(c["fast"] for c in est["combos"])):
            combo = next((c for c in est["combos"] if c["fast"]), est["combos"][0])
            piece = next((n for n in combo["cards"] if not any(e["name"] == n and e.get("board") == "commander" for e in working)), None)
            if piece:
                remove(piece, f"Breaks up the combo {' + '.join(combo['cards'])}")
        elif est["impact_cards"]:
            remove(est["impact_cards"][0]["name"],
                   f"Impact {est['impact_cards'][0]['points']}: {est['impact_cards'][0]['reason']}")
        if len(cuts) == before:
            break  # nothing more we can cut (e.g. the commander itself is the problem)
    return cuts, working


def _entry(entries, name):
    return next(e for e in entries if e["name"] == name)


# ------------------------------------------------------------------ candidates

def candidate_pool(collection, entries, fmt, target):
    """Owned cards that could go in this deck at this power level, before scoring."""
    in_deck = {e["name"] for e in entries}
    identity, has_commander = identity_of(entries)
    pool = []
    for card in collection:
        if card["name"] in in_deck or card.get("is_basic"):
            continue
        legal = (card.get("legalities") or {}).get(fmt)
        if fmt != "casual" and legal not in ("legal", "restricted"):
            continue
        ci = set(card.get("color_identity") or "")
        if (has_commander or fmt == "commander") and not ci <= identity:
            continue
        if not has_commander and fmt != "commander" and identity and ci and not ci <= identity:
            continue
        if fmt == "commander" and not fits_bracket(entries, card, target):
            continue
        pool.append(card)
    return pool


def score(card, gaps, themes, tribe, target):
    """(points, reasons) for how well a card fits."""
    reasons = []
    pts = 0.0
    roles = roles_of(card)
    gap_by_role = {g["role"]: g for g in gaps}
    for role in roles:
        g = gap_by_role.get(role)
        if g and g["gap"] > 0:
            pts += 3 + 4 * min(1, g["gap"] / g["target"])
            reasons.append(f"{ROLE_LABELS[role]}: the deck has {g['have']} of about {g['target']}")
    hits = themes_of(card) & set(themes)
    for t in sorted(hits):
        pts += 2
        reasons.append(f"{THEMES[t][0]} theme")
    if tribe and (tribe in creature_types(card) or re.search(rf"\b{tribe}s?\b", card.get("oracle_text") or "")):
        pts += 3
        reasons.append(f"{tribe} synergy")
    hit = brackets.impact_of(card)
    if brackets.is_game_changer(card):
        if target >= 3:
            pts += 2.5
            reasons.append("Game Changer (fits your bracket)")
    elif hit and target >= 3:
        pts += hit[0] * 0.6
        reasons.append(f"High impact ({hit[0]} pt{'s' if hit[0] != 1 else ''}): {hit[2]}")
    cmc = card.get("cmc") or 0
    if not is_land(card):
        pts += 1 if cmc <= 2 else 0.5 if cmc <= 4 else -0.5 if cmc >= 7 else 0
    return pts, reasons, roles


def suggestions(entries, collection, fmt, intended=None, limit_per_group=8):
    """Everything the suggestions page shows. `collection`: one dict per card name you own,
    with name, type_line, oracle_text, color_identity, legalities, cmc, game_changer,
    is_basic, card_id, price, owned, in_decks."""
    est = brackets.estimate(entries, intended=intended) if fmt == "commander" else None
    current = est["computed"] if est else None
    target = target_bracket(intended, current) if est else None
    cuts, working = cuts_to_reach(entries, target) if est else ([], entries)

    gaps = role_gaps(working, fmt)
    themes, tribe = deck_themes(working)
    pool = candidate_pool(collection, working, fmt, target or 5)
    identity, has_commander = identity_of(entries)

    scored = []
    for card in pool:
        pts, reasons, roles = score(card, gaps, themes, tribe, target or 5)
        if pts <= 0.5 and not reasons:
            continue
        scored.append(dict(card, score=round(pts, 1), reasons=reasons, roles=sorted(roles)))
    scored.sort(key=lambda c: (-c["score"], c["name"]))

    groups = []
    used = set()
    gap_of = {g["role"]: g for g in gaps}
    # Biggest shortfall first (as a share of the target); roles the deck already has enough of are skipped.
    short = sorted((g for g in gaps if g["gap"] > 0), key=lambda g: -g["gap"] / g["target"])
    order = [g["role"] for g in short] + (["tutor"] if (target or 5) >= 3 else [])
    for role in order:
        rows = [c for c in scored if role in c["roles"] and c["name"] not in used][:limit_per_group]
        if rows:
            used.update(c["name"] for c in rows)
            gap = gap_of.get(role)
            groups.append({"role": role, "label": ROLE_LABELS[role], "gap": gap, "cards": rows})
    synergy = [c for c in scored if c["name"] not in used and not is_land(c)
               and any("theme" in r or "synergy" in r or r.startswith(("High impact", "Game Changer"))
                       for r in c["reasons"])][:limit_per_group * 2]
    if synergy:
        groups.append({"role": "synergy", "label": ROLE_LABELS["synergy"], "gap": None, "cards": synergy})

    # Pair each cut with the best replacement in the same role (not already suggested for another cut).
    swaps = []
    taken = set()
    for entry, reason, copies in cuts:
        roles = roles_of(entry)
        if roles:
            pick = next((c for c in scored if c["name"] not in taken and set(c["roles"]) & roles), None)
        else:  # no clear role: a nonland card that fits the deck's themes
            pick = next((c for c in scored if c["name"] not in taken and not is_land(c)
                         and any("theme" in r or "synergy" in r for r in c["reasons"])), None)
        if pick:
            taken.add(pick["name"])
        swaps.append({"cut": entry, "reason": reason, "add": pick, "copies": copies})

    deck_size = sum(e.get("quantity", 1) for e in entries if e.get("board") != "side")
    return {
        "format": fmt,
        "bracket": est,
        "target": target,
        "target_name": brackets.BRACKET_NAMES.get(target) if target else None,
        "budget": budget(working, target) if est else None,
        "swaps": swaps,
        "gaps": gaps,
        "themes": [{"key": k, "label": THEMES[k][0], "count": n} for k, n in sorted(themes.items(), key=lambda kv: -kv[1])],
        "tribe": tribe,
        "groups": groups,
        "identity": "".join(c for c in "WUBRG" if c in identity),
        "has_commander": has_commander,
        "considered": len(pool),
        "deck_size": deck_size,
        "slots": (100 - deck_size) if fmt == "commander" else None,
    }


# ------------------------------------------------------------------ commanders you could build

def can_be_commander(card):
    t = front_type(card)
    text = card.get("oracle_text") or ""
    return ("Legendary" in t and "Creature" in t) or "can be your commander" in text


def commander_ideas(collection, limit=12):
    """Commanders you own, ranked by how many of your other cards fit their colors."""
    playable = [c for c in collection if (c.get("legalities") or {}).get("commander") == "legal" and not c.get("is_basic")]
    ideas = []
    for cmd in playable:
        if not can_be_commander(cmd):
            continue
        ci = set(cmd.get("color_identity") or "")
        fits = [c for c in playable if c["name"] != cmd["name"] and set(c.get("color_identity") or "") <= ci]
        nonland = [c for c in fits if not is_land(c)]
        themes = Counter()
        for t in themes_of(cmd):
            themes[t] += 3
        for c in nonland:
            for t in themes_of(c) & themes_of(cmd):
                themes[t] += 1
        ideas.append({
            "card": cmd, "identity": "".join(x for x in "WUBRG" if x in ci) or "C",
            "fits": len(fits), "nonland": len(nonland), "lands": len(fits) - len(nonland),
            "themes": [THEMES[t][0] for t, _ in themes.most_common(3)],
            "game_changers": sum(1 for c in fits if brackets.is_game_changer(c)),
        })
    ideas.sort(key=lambda i: (-i["nonland"], i["card"]["name"]))
    return ideas[:limit]
