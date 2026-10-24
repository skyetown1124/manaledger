"""Deck rules and statistics, computed from plain dicts (no database calls here).

views.py builds one dict per DeckCard with these keys:
    card_id, name, set_code, collector_number, board ('commander' | 'main' | 'side'),
    quantity, type_line, mana_cost, cmc, colors, color_identity, oracle_text,
    legalities (dict), details_loaded, price (float or None), owned (int),
    is_basic (bool)
"""
import re
from collections import Counter, defaultdict

from .stats import main_type

COLORS = "WUBRG"

FORMAT_RULES = {
    # min/max: cards in main deck (plus commander); copies: max per name; side: max sideboard
    "commander": {"min": 100, "max": 100, "copies": 1, "side": 0},
    "standard": {"min": 60, "max": None, "copies": 4, "side": 15},
    "pioneer": {"min": 60, "max": None, "copies": 4, "side": 15},
    "modern": {"min": 60, "max": None, "copies": 4, "side": 15},
    "legacy": {"min": 60, "max": None, "copies": 4, "side": 15},
    "vintage": {"min": 60, "max": None, "copies": 4, "side": 15},
    "pauper": {"min": 60, "max": None, "copies": 4, "side": 15},
    "casual": {"min": None, "max": None, "copies": None, "side": None},
}

TYPE_GROUPS = [
    ("Creature", "Creatures"), ("Planeswalker", "Planeswalkers"), ("Battle", "Battles"),
    ("Instant", "Instants"), ("Sorcery", "Sorceries"), ("Artifact", "Artifacts"),
    ("Enchantment", "Enchantments"), ("Land", "Lands"), ("Other", "Other"),
]
CURVE_LABELS = ["0", "1", "2", "3", "4", "5", "6", "7+"]
SYMBOL_RE = re.compile(r"\{([^}]+)\}")
ANY_NUMBER_RE = re.compile(r"a deck can have any number of cards named", re.I)
UP_TO_RE = re.compile(r"a deck can have up to (\w+) cards named", re.I)   # Seven Dwarves, Nazgûl
NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen".split())}

# Two-commander pairings (Commander rules 702.124 and related)
PARTNER_RE = re.compile(r"^Partner\s*(?:\(|$)", re.M)                     # plain "Partner"
PARTNER_WITH_RE = re.compile(r"^Partner with ([^\n(]+?)\s*(?:\(|$)", re.M)  # "Partner with <name>"
PARTNER_GROUP_RE = re.compile(r"^Partner—([^\n(]+?)\s*(?:\(|$)", re.M)      # "Partner—Survivors" etc.


def copy_limit_exempt(type_line, oracle_text):
    """Basic lands, and cards that say 'A deck can have any number of cards named ...'."""
    t = type_line or ""
    return ("Basic" in t and "Land" in t) or bool(ANY_NUMBER_RE.search(oracle_text or ""))


def enforces_singleton(fmt):
    return FORMAT_RULES.get(fmt, {}).get("copies") == 1


def copy_limit(fmt, type_line, oracle_text, legalities=None):
    """Most copies of one card name a deck may hold in this format, or None for no limit.

    Basic lands and "any number" cards: no limit. "Up to seven cards named ...": that number.
    Restricted in this format (Vintage): 1. Otherwise the format's limit (1 in Commander, 4 elsewhere).
    """
    rules = FORMAT_RULES.get(fmt, FORMAT_RULES["casual"])
    if rules["copies"] is None or copy_limit_exempt(type_line, oracle_text):
        return None
    match = UP_TO_RE.search(oracle_text or "")
    if match:
        word = match.group(1).lower()
        n = NUMBER_WORDS.get(word) or (int(word) if word.isdigit() else None)
        if n:
            return n
    if (legalities or {}).get(fmt) == "restricted":
        return 1
    return rules["copies"]


def legality_problem(fmt, name, legalities):
    """Why a card can't be played in this format, or None. Unknown legality isn't a problem here."""
    if fmt == "casual" or not legalities:
        return None
    status = legalities.get(fmt)
    if status == "banned":
        return f"{name} is banned in {fmt.capitalize()}."
    if status == "not_legal":
        return f"{name} isn't legal in {fmt.capitalize()}."
    return None


def _text(e):
    return e.get("oracle_text") or ""


def can_be_commander(e):
    """A legendary creature, or a card that says it can be your commander."""
    front = (e.get("type_line") or "").split("//")[0]
    return ("Legendary" in front and "Creature" in front) or "can be your commander" in _text(e).lower()


def is_background(e):
    t = e.get("type_line") or ""
    return "Enchantment" in t and "Background" in t


def _same_name(written, card_name):
    written = written.strip().lower()
    return written in (card_name.lower(), card_name.split(" // ")[0].lower())


def pair_problem(a, b):
    """None if cards a and b can be commanders together; otherwise the reason they can't."""
    ta, tb = _text(a), _text(b)
    if PARTNER_RE.search(ta) and PARTNER_RE.search(tb):
        return None
    wa, wb = PARTNER_WITH_RE.search(ta), PARTNER_WITH_RE.search(tb)
    if wa and wb and _same_name(wa.group(1), b["name"]) and _same_name(wb.group(1), a["name"]):
        return None
    ga, gb = PARTNER_GROUP_RE.search(ta), PARTNER_GROUP_RE.search(tb)
    if ga and gb and ga.group(1).strip().lower() == gb.group(1).strip().lower():
        return None
    if re.search(r"^Friends forever", ta, re.M) and re.search(r"^Friends forever", tb, re.M):
        return None
    for x, y in ((a, b), (b, a)):
        if "choose a background" in _text(x).lower() and is_background(y) and can_be_commander(x):
            return None
        if "doctor's companion" in _text(x).lower() and "Time Lord Doctor" in (y.get("type_line") or ""):
            return None
    if wa or wb:
        named = wa.group(1).strip() if wa else wb.group(1).strip()
        return f"{a['name'] if wa else b['name']} can only partner with {named}."
    return (f"{a['name']} and {b['name']} can't be commanders together. Two commanders need a pairing ability: "
            "Partner, Friends forever, Choose a Background, or Doctor's companion.")


def commander_problem(commanders):
    """Check the command zone. `commanders` is a list of entry dicts. Returns None or a message."""
    copies = sum(e["quantity"] for e in commanders)
    if copies == 0:
        return "Add a legendary creature to the Commander section."
    if copies > 2 or any(e["quantity"] > 1 for e in commanders):
        return "A deck has one commander, or two that can pair up."
    if not all(e.get("details_loaded", True) for e in commanders):
        return None  # text not loaded yet; can't judge
    if copies == 1:
        e = commanders[0]
        if is_background(e):
            return f"{e['name']} is a Background. It goes with a commander that says \"Choose a Background\"."
        return None if can_be_commander(e) else f"{e['name']} can't be a commander: it isn't a legendary creature."
    a, b = commanders
    problem = pair_problem(a, b)
    if problem:
        return problem
    for e in (a, b):
        if not can_be_commander(e) and not is_background(e):
            return f"{e['name']} can't be a commander: it isn't a legendary creature."
    return None


CARD_TYPES = ["Kindred", "Tribal", "Artifact", "Battle", "Creature", "Enchantment",
              "Instant", "Land", "Planeswalker", "Sorcery"]


def card_types(type_line):
    """Card types on the front face, in printed order: 'Legendary Artifact Creature — Golem' -> ['Artifact', 'Creature']."""
    front = (type_line or "").split("//")[0].split("—")[0]
    return [word for word in front.split() if word in CARD_TYPES]


def composition(playing, deck_count):
    """How the deck splits by card type. Each card counts once, under its main type."""
    counts = Counter()
    cmc_total = Counter()
    buckets = defaultdict(lambda: [0] * len(CURVE_LABELS))  # type -> copies at CMC 0..7+
    multi = Counter()
    for e in playing:
        kind, qty = deck_type(e["type_line"]), e["quantity"]
        cmc = e.get("cmc") or 0
        counts[kind] += qty
        cmc_total[kind] += cmc * qty
        buckets[kind][min(int(cmc), 7)] += qty
        types = card_types(e["type_line"])
        if len(types) > 1:
            multi[" ".join(types)] += qty

    rows = []
    for key, label in TYPE_GROUPS:
        if counts[key]:
            rows.append({
                "key": key.lower(), "label": label, "count": counts[key],
                "pct": round(100 * counts[key] / deck_count, 1) if deck_count else 0,
                # Lands cost nothing to play, so an average CMC would only mislead.
                "avg_cmc": None if key == "Land" else round(cmc_total[key] / counts[key], 2),
                "curve": buckets[key],
            })
    top = max((r["count"] for r in rows), default=0) or 1
    for r in rows:
        r["bar"] = round(100 * r["count"] / top)

    # Type x CMC grid for spells (lands left out), with shading relative to the busiest cell.
    spell_rows = [r for r in rows if r["key"] != "land"]
    peak = max((n for r in spell_rows for n in r["curve"]), default=0) or 1
    grid = [{"label": r["label"], "key": r["key"], "total": r["count"],
             "cells": [{"count": n, "level": round(n / peak, 3)} for n in r["curve"]]}
            for r in spell_rows]
    totals = [sum(r["curve"][i] for r in spell_rows) for i in range(len(CURVE_LABELS))]

    lands = counts["Land"]
    spells = deck_count - lands
    spell_cmc = sum(v for k, v in cmc_total.items() if k != "Land")
    return {
        "rows": rows,
        "spells": spells,
        "lands": lands,
        "avg_cmc": round(spell_cmc / spells, 2) if spells else 0,
        "cmc_labels": CURVE_LABELS,
        "grid": grid,
        "grid_totals": totals,
        "multi": [{"label": k, "count": v} for k, v in sorted(multi.items(), key=lambda kv: -kv[1])],
    }


def deck_type(type_line):
    if "Battle" in (type_line or "").split("—")[0]:
        return "Battle"
    return main_type(type_line)


def pips(mana_cost):
    """Colored mana symbols in a cost: '{2}{U}{U/R}' -> Counter({'U': 2, 'R': 1})."""
    counts = Counter()
    for symbol in SYMBOL_RE.findall(mana_cost or ""):
        for c in COLORS:
            if c in symbol.upper().replace("P", ""):
                counts[c] += 1
    return counts



def _ok(label, detail=""):
    return {"ok": True, "label": label, "detail": detail}


def _bad(label, detail):
    return {"ok": False, "label": label, "detail": detail}


def _names(items, limit=6):
    items = sorted(items)
    shown = ", ".join(items[:limit])
    return shown + (f" and {len(items) - limit} more" if len(items) > limit else "")


def analyze(fmt, entries):
    rules = FORMAT_RULES.get(fmt, FORMAT_RULES["casual"])
    by_board = defaultdict(list)
    for e in entries:
        by_board[e["board"]].append(e)

    commander_count = sum(e["quantity"] for e in by_board["commander"])
    main_count = sum(e["quantity"] for e in by_board["main"])
    side_count = sum(e["quantity"] for e in by_board["side"])
    deck_count = main_count + commander_count
    playing = by_board["commander"] + by_board["main"]

    # ----- Groups for the deck list -----
    groups = []
    if by_board["commander"]:
        groups.append({"label": "Commander", "count": commander_count,
                       "entries": sorted(by_board["commander"], key=lambda e: e["name"])})
    typed = defaultdict(list)
    for e in by_board["main"]:
        typed[deck_type(e["type_line"])].append(e)
    for key, label in TYPE_GROUPS:
        if typed[key]:
            rows = sorted(typed[key], key=lambda e: (e["cmc"], e["name"]))
            groups.append({"label": label, "count": sum(e["quantity"] for e in rows), "entries": rows})
    if by_board["side"]:
        groups.append({"label": "Sideboard", "count": side_count,
                       "entries": sorted(by_board["side"], key=lambda e: e["name"])})

    # ----- Mana curve (non-land cards you play) -----
    curve = [0] * len(CURVE_LABELS)
    nonland_qty, nonland_mv = 0, 0.0
    for e in playing:
        if deck_type(e["type_line"]) == "Land":
            continue
        curve[min(int(e["cmc"]), 7)] += e["quantity"]
        nonland_qty += e["quantity"]
        nonland_mv += e["cmc"] * e["quantity"]

    # ----- Colors -----
    symbol_counts = Counter()
    identity = set()
    for e in playing:
        for c, n in pips(e["mana_cost"]).items():
            symbol_counts[c] += n * e["quantity"]
        identity.update(e["color_identity"] or "")
    land_count = sum(e["quantity"] for e in playing if deck_type(e["type_line"]) == "Land")

    # ----- Rule checks -----
    checks = []
    if fmt == "casual":
        checks.append(_ok("Casual deck", "No size, copy or legality rules are checked."))
    else:
        lo, hi = rules["min"], rules["max"]
        if hi is not None and lo == hi:
            label = f"{deck_count} of {lo} cards" + (" (including commander)" if fmt == "commander" else "")
            checks.append(_ok(label) if deck_count == lo else
                          _bad(label, f"{'Add' if deck_count < lo else 'Remove'} {abs(lo - deck_count)} card(s)."))
        else:
            label = f"{deck_count} cards (at least {lo})"
            checks.append(_ok(label) if deck_count >= lo else _bad(label, f"Add {lo - deck_count} more card(s)."))
        if rules["side"] is not None:
            if fmt == "commander":
                if side_count:
                    checks.append(_bad("No sideboard in Commander", f"Move or remove the {side_count} sideboard card(s)."))
            elif side_count > rules["side"]:
                checks.append(_bad(f"Sideboard {side_count} of {rules['side']}",
                                   f"Remove {side_count - rules['side']} sideboard card(s)."))
            else:
                checks.append(_ok(f"Sideboard {side_count} of {rules['side']}"))

        # Copy limit counts every printing of a name, main deck and sideboard together.
        per_name, limits = Counter(), {}
        for e in entries:
            per_name[e["name"]] += e["quantity"]
            limits[e["name"]] = copy_limit(fmt, e["type_line"], e["oracle_text"], e["legalities"])
        over = [f"{n} ({c} of {limits[n]})" for n, c in per_name.items()
                if limits[n] is not None and c > limits[n]]
        if fmt == "commander":
            label = "Singleton: one copy of each card"
        elif fmt == "vintage":
            label = "Up to 4 copies of each card, 1 of each restricted card"
        else:
            label = f"Up to {rules['copies']} copies of each card"
        checks.append(_bad(label, "Too many copies of " + _names(over) + ".") if over
                      else _ok(label, "Basic lands are unlimited."))

        if fmt == "commander":
            problem = commander_problem(by_board["commander"])
            names = " & ".join(e["name"] for e in by_board["commander"])
            checks.append(_bad("Valid commander", problem) if problem else _ok("Valid commander", names))
            if commander_count:
                allowed = set("".join(e["color_identity"] or "" for e in by_board["commander"]))
                outside = [e["name"] for e in by_board["main"] if set(e["color_identity"] or "") - allowed]
                words = "/".join(c for c in COLORS if c in allowed) or "colorless"
                label = f"Every card fits the commander's colors ({words})"
                verb = "uses" if len(outside) == 1 else "use"
                checks.append(_bad(label, f"{_names(outside)} {verb} colors outside your commander's.") if outside else _ok(label))

        name = fmt.capitalize()
        unknown = {e["name"] for e in entries if not e["legalities"]}
        banned = sorted({e["name"] for e in entries if (e["legalities"] or {}).get(fmt) == "banned"})
        not_legal = sorted({e["name"] for e in entries if (e["legalities"] or {}).get(fmt) == "not_legal"})
        checks.append(_bad(f"No cards banned in {name}", "Banned: " + _names(banned) + ".") if banned
                      else _ok(f"No cards banned in {name}"))
        if not_legal:
            checks.append(_bad(f"Every card is legal in {name}", _names(not_legal) + " can't be played in this format."))
        elif unknown:
            checks.append(_bad(f"Every card is legal in {name}",
                               f"Refresh prices to load legality for {len(unknown)} card(s)."))
        else:
            checks.append(_ok(f"Every card is legal in {name}"))
        restricted = sorted({e["name"] for e in entries if (e["legalities"] or {}).get(fmt) == "restricted"})
        if restricted:
            checks.append(_ok(f"Restricted cards: {len(restricted)}", _names(restricted) + " (one copy each)."))

    # Ownership: you can only build with cards in your library (basic lands excepted).
    short = [f'{e["name"]} (deck uses {e["used"]}, you own {e["owned"]})'
             for e in _usage(entries) if not e["is_basic"] and e["used"] > e["owned"]]
    checks.append(_bad("You own every card", "Not enough copies of " + _names(short) + ".") if short
                  else _ok("You own every card", "Basic lands don't need to be in your library."))

    value = sum((e["price"] or 0) * e["quantity"] for e in entries)
    top_symbol = max(symbol_counts.values(), default=0) or 1
    return {
        "deck_count": deck_count,
        "main_count": main_count,
        "side_count": side_count,
        "commander_count": commander_count,
        "target": rules["min"],
        "land_count": land_count,
        "value": round(value, 2),
        "avg_mv": round(nonland_mv / nonland_qty, 2) if nonland_qty else 0,
        "groups": groups,
        "curve": {"labels": CURVE_LABELS, "values": curve},
        "composition": composition(playing, deck_count),
        "symbols": [{"color": c, "count": symbol_counts[c], "pct": round(100 * symbol_counts[c] / top_symbol)}
                    for c in COLORS if symbol_counts[c]],
        "identity": "".join(c for c in COLORS if c in identity),
        "checks": checks,
        "issues": sum(1 for c in checks if not c["ok"]),
        "export": export_text(by_board),
    }


def _usage(entries):
    """One row per printing: how many copies the deck uses across boards vs. owned."""
    rows = {}
    for e in entries:
        row = rows.setdefault(e["card_id"], {"name": e["name"], "owned": e["owned"],
                                             "is_basic": e["is_basic"], "used": 0})
        row["used"] += e["quantity"]
    return rows.values()


def export_text(by_board):
    """Deck list in the plain-text format Arena, Moxfield and Archidekt import."""
    sections = []
    for board, title in (("commander", "Commander"), ("main", "Deck"), ("side", "Sideboard")):
        rows = sorted(by_board.get(board, []), key=lambda e: e["name"])
        if rows:
            lines = [f'{e["quantity"]} {e["name"]} ({e["set_code"].upper()}) {e["collector_number"]}' for e in rows]
            sections.append(title + "\n" + "\n".join(lines))
    return "\n\n".join(sections)
