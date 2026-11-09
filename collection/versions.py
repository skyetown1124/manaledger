"""Deck versions: saved copies of a deck's list, and what changed since one.

A version is saved:
  - automatically before a change, when the newest version is over 30 minutes old
    (so each editing session has a "before" to compare with),
  - before a list import or an Archidekt update replaces the deck,
  - when you press "Save version",
  - the first time a deck page is opened, if the deck has no versions yet.
"""
from datetime import timedelta

from django.utils import timezone

SESSION_GAP = timedelta(minutes=30)
KEEP = 60  # versions kept per deck (oldest named ones are kept too)


def current_cards(deck):
    return [{"card_id": e.card_id, "name": e.card.name, "board": e.board,
             "quantity": e.quantity, "proxies": e.proxies}
            for e in deck.entries.select_related("card").order_by("board", "card__name")]


def _key_counts(cards):
    counts = {}
    for c in cards:
        counts[(c["name"], c["board"])] = counts.get((c["name"], c["board"]), 0) + c["quantity"]
    return counts


def same_list(a, b):
    return _key_counts(a) == _key_counts(b)


def snapshot(deck, label=""):
    """Save the deck's list as it is now. Skips (returns the latest) if nothing changed and no label."""
    from .models import DeckVersion

    cards = current_cards(deck)
    latest = deck.versions.first()
    if latest and not label and same_list(latest.cards, cards):
        return latest
    version = DeckVersion.objects.create(
        deck=deck, label=label[:100], cards=cards,
        size=sum(c["quantity"] for c in cards if c["board"] != "side"))
    _prune(deck)
    return version


def checkpoint(deck, reason=""):
    """Call BEFORE changing a deck: saves the current list if the last save is old (or for a reason)."""
    latest = deck.versions.first()
    if reason or latest is None or timezone.now() - latest.created_at > SESSION_GAP:
        return snapshot(deck, label=reason)
    return latest


def ensure_baseline(deck):
    if not deck.versions.exists() and deck.entries.exists():
        snapshot(deck, label="First saved version")


def _prune(deck):
    ids = list(deck.versions.filter(label="").values_list("id", flat=True)[KEEP:])
    if ids:
        deck.versions.filter(id__in=ids).delete()


def previous_version(deck, cards=None):
    """The newest version whose list differs from the deck now (None if there isn't one)."""
    cards = current_cards(deck) if cards is None else cards
    for v in deck.versions.all()[:KEEP]:
        if not same_list(v.cards, cards):
            return v
    return None


def diff(entries, version):
    """Compare the deck now (deck entries from views._deck_entries) with a version.

    Returns {"by_entry": {entry_id: {"status": "new"|"more"|"fewer"|"moved", "delta": n, "was": qty, "from": board}},
             "removed": [{card_id, name, board, quantity}], "added": n, "removed_count": n, "changed": n}."""
    old = {}
    for c in version.cards:
        key = (c["name"], c["board"])
        old.setdefault(key, {"quantity": 0, "card_id": c["card_id"], "name": c["name"], "board": c["board"]})
        old[key]["quantity"] += c["quantity"]
    old_by_name = {}
    for (name, board), c in old.items():
        old_by_name.setdefault(name, {})[board] = c["quantity"]
    now_names = {e["name"] for e in entries}

    # Compare by card name and section, not by printing: two printings of the same card
    # count together. A change is shown once, on the first row for that card.
    now = {}
    first_entry = {}
    for e in entries:
        key = (e["name"], e["board"])
        now[key] = now.get(key, 0) + e["quantity"]
        first_entry.setdefault(key, e)
    present = set(now)

    by_entry, added, changed = {}, 0, 0
    seen = set(present)
    for key, qty in now.items():
        e = first_entry[key]
        before = old.get(key, {}).get("quantity", 0)
        if before == qty:
            continue
        if before == 0:
            moved_from = next((b for b in old_by_name.get(e["name"], {}) if b != e["board"] and (e["name"], b) not in present), None)
            if moved_from:
                by_entry[e["id"]] = {"status": "moved", "delta": qty, "was": 0, "from": moved_from}
                changed += 1
            else:
                by_entry[e["id"]] = {"status": "new", "delta": qty, "was": 0}
                added += 1
        else:
            by_entry[e["id"]] = {"status": "more" if qty > before else "fewer", "delta": qty - before, "was": before}
            changed += 1
    removed = []
    for key, c in old.items():
        if key in seen:
            continue
        name, board = key
        if name in now_names and any(d.get("status") == "moved" and d.get("from") == board
                                     for d in by_entry.values()):
            continue  # it moved to another part of the deck; shown there
        removed.append(dict(c))
    removed.sort(key=lambda c: c["name"])
    return {"by_entry": by_entry, "removed": removed, "added": added,
            "removed_count": len(removed), "changed": changed}
