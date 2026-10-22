"""Dashboard numbers, computed from plain Python rows (no database calls here).

views.dashboard() turns your collection into a list of dicts and passes it in,
which keeps this file easy to test on its own.
"""
from collections import defaultdict

COLOR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}
COLOR_ORDER = ["White", "Blue", "Black", "Red", "Green", "Multicolor", "Colorless"]
# Chart colors that echo each Magic color (gold is Magic's own multicolor frame).
COLOR_SWATCH = {
    "White": "#e8dcb5", "Blue": "#2f7ed8", "Black": "#7d8590", "Red": "#e5484d",
    "Green": "#1fb57b", "Multicolor": "#f0a500", "Colorless": "#a5b4c7",
}
TYPE_ORDER = ["Creature", "Instant", "Sorcery", "Artifact", "Enchantment", "Planeswalker", "Land", "Other"]
RARITY_ORDER = ["mythic", "rare", "uncommon", "common", "special", "bonus"]


def color_group(colors):
    if not colors:
        return "Colorless"
    if len(colors) > 1:
        return "Multicolor"
    return COLOR_NAMES.get(colors, "Colorless")


def main_type(type_line):
    """'Legendary Artifact Creature — Golem' -> 'Creature'."""
    front = (type_line or "").split("//")[0].split("—")[0]
    for t in ["Planeswalker", "Creature", "Land", "Artifact", "Enchantment", "Instant", "Sorcery"]:
        if t in front:
            return t
    return "Other"


def _pct(part, whole):
    return round(100 * part / whole, 1) if whole else 0.0


def _trend(values):
    """Straight-line least-squares fit, one point per value."""
    n = len(values)
    if n < 2:
        return list(values)
    xs = range(n)
    mx, my = (n - 1) / 2, sum(values) / n
    denom = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, values)) / denom
    return [round(my + slope * (x - mx), 2) for x in xs]


def _group(rows, key_fn, order=None, limit=None):
    value, copies = defaultdict(float), defaultdict(int)
    for r in rows:
        k = key_fn(r)
        value[k] += r["total"]
        copies[k] += r["quantity"]
    total = sum(value.values())
    keys = order if order else sorted(value, key=value.get, reverse=True)
    keys = [k for k in keys if k in value]
    if limit:
        keys = keys[:limit]
    return [
        {"label": k, "value": round(value[k], 2), "copies": copies[k], "pct": _pct(value[k], total)}
        for k in keys
    ]


def build_dashboard(rows, snapshots, last_updated=None, today=None):
    """
    rows: one dict per CollectionItem with keys name, set_code, set_name, rarity,
          colors, type_line, finish, quantity, unit_price, prev_price,
          purchase_price, scryfall_uri.
    snapshots: list of (date, total_value) for the collection, oldest first.
    """
    for r in rows:
        r["total"] = round((r["unit_price"] or 0) * r["quantity"], 2)

    total_value = round(sum(r["total"] for r in rows), 2)
    copies = sum(r["quantity"] for r in rows)
    unpriced = sum(1 for r in rows if r["unit_price"] is None)
    ranked = sorted(rows, key=lambda r: r["total"], reverse=True)
    priciest = max(rows, key=lambda r: r["unit_price"] or 0, default=None)

    # Change since the previous saved day.
    prev_total = snapshots[-2][1] if len(snapshots) >= 2 else None
    change = round(total_value - prev_total, 2) if prev_total is not None else None
    change_pct = _pct(change, prev_total) if prev_total else None

    paid_rows = [r for r in rows if r["purchase_price"] is not None]
    paid = round(sum(r["purchase_price"] * r["quantity"] for r in paid_rows), 2)
    paid_now = round(sum(r["total"] for r in paid_rows), 2)

    movers = []
    for r in rows:
        if r["unit_price"] is not None and r["prev_price"] is not None:
            delta = round((r["unit_price"] - r["prev_price"]) * r["quantity"], 2)
            if delta:
                movers.append({
                    "name": r["name"], "set_code": r["set_code"].upper(), "finish": r["finish"],
                    "was": r["prev_price"], "now": r["unit_price"], "quantity": r["quantity"],
                    "delta": delta, "pct": _pct(r["unit_price"] - r["prev_price"], r["prev_price"]),
                })
    gainers = sorted([m for m in movers if m["delta"] > 0], key=lambda m: -m["delta"])[:5]
    losers = sorted([m for m in movers if m["delta"] < 0], key=lambda m: m["delta"])[:5]

    by_color = _group(rows, lambda r: color_group(r["colors"]), COLOR_ORDER)
    for g in by_color:
        g["swatch"] = COLOR_SWATCH[g["label"]]

    by_rarity = _group(rows, lambda r: r["rarity"], RARITY_ORDER)
    for g in by_rarity:
        g["label"] = g["label"].capitalize()

    # Heat map: color (rows) x card type (columns).
    cells = defaultdict(float)
    for r in rows:
        cells[(color_group(r["colors"]), main_type(r["type_line"]))] += r["total"]
    used_types = [t for t in TYPE_ORDER if any(k[1] == t for k in cells)]
    used_colors = [c for c in COLOR_ORDER if any(k[0] == c for k in cells)]
    peak = max(cells.values(), default=0) or 1
    heatmap = {
        "types": used_types,
        "rows": [
            {"color": c, "cells": [
                {"value": round(cells.get((c, t), 0), 2), "level": round(cells.get((c, t), 0) / peak, 3)}
                for t in used_types
            ]}
            for c in used_colors
        ],
    }
    heat_lines = []
    if cells and total_value:
        (top_c, top_t), top_v = max(cells.items(), key=lambda kv: kv[1])
        heat_lines.append(f"{top_c} {top_t.lower()}s hold the most value: ${top_v:,.2f} "
                          f"({_pct(top_v, total_value)}% of your collection).")
        top3 = sum(r["total"] for r in ranked[:3])
        heat_lines.append(f"Your 3 most valuable cards make up {_pct(top3, total_value)}% of the total.")

    dates = [d.isoformat() for d, _ in snapshots]
    values = [round(v, 2) for _, v in snapshots]

    days_ago = (today - last_updated.date()).days if (today and last_updated) else None
    return {
        "kpi": {
            "total_value": total_value,
            "change": change,
            "change_pct": change_pct,
            "copies": copies,
            "unique": len(rows),
            "unpriced": unpriced,
            "priciest_name": priciest["name"] if priciest else None,
            "priciest_price": priciest["unit_price"] if priciest else None,
            "priciest_set": priciest["set_code"].upper() if priciest else None,
            "last_updated": last_updated,
            "days_ago": days_ago,
            "paid": paid,
            "paid_now": paid_now,
            "paid_gain": round(paid_now - paid, 2),
            "has_paid": bool(paid_rows),
        },
        "gainers": gainers,
        "losers": losers,
        "has_history": len(snapshots) >= 2,
        "heatmap": heatmap,
        "heat_lines": heat_lines,
        "top_table": [
            {"rank": i + 1, "name": r["name"], "set_code": r["set_code"].upper(), "finish": r["finish"],
             "quantity": r["quantity"], "unit_price": r["unit_price"], "total": r["total"],
             "pct": _pct(r["total"], total_value), "scryfall_uri": r["scryfall_uri"]}
            for i, r in enumerate(ranked[:10])
        ],
        "chart_data": {
            "over_time": {"dates": dates, "values": values, "trend": _trend(values)},
            "by_set": _group(rows, lambda r: f'{r["set_name"]} ({r["set_code"].upper()})', limit=10),
            "by_color": by_color,
            "by_rarity": by_rarity,
            "by_type": _group(rows, lambda r: main_type(r["type_line"]), TYPE_ORDER),
            "top_cards": [
                {"label": f'{r["name"]} ({r["set_code"].upper()}{", foil" if r["finish"] != "nonfoil" else ""})',
                 "value": r["total"], "pct": _pct(r["total"], total_value)}
                for r in ranked if r["total"] > 0
            ][:25],
        },
    }
