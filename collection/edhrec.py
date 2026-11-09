"""EDHREC's most-played cards for a commander (https://edhrec.com).

EDHREC publishes each commander page as JSON at json.edhrec.com. There's no official
API, so this reads that page politely (one request, saved for a week) and only uses
the card lists: how many decks play each card and its synergy with the commander.
"""
import re
from datetime import timedelta

import requests
from django.utils import timezone

JSON_URL = "https://json.edhrec.com/pages/commanders/{slug}.json"
PAGE_URL = "https://edhrec.com/commanders/{slug}"
MAX_AGE = timedelta(days=7)
SORTS = {"inclusion": "Most played", "synergy": "Highest synergy"}


class EdhrecError(Exception):
    pass


def _clean(name):
    name = name.split(" // ")[0].lower()
    name = re.sub(r"['’,.!?:\"()]", "", name)
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")


def slug(commander_names):
    """EDHREC's page name: 'Atraxa, Praetors' Voice' -> 'atraxa-praetors-voice'; partners joined A-Z."""
    return "-".join(sorted(_clean(n) for n in commander_names if n))


def card_image(card, size="small"):
    """Scryfall's image for the printing EDHREC shows (EDHREC card ids are Scryfall ids)."""
    sid = card.get("scryfall_id") or ""
    if len(sid) < 2:
        return ""
    return f"https://cards.scryfall.io/{size}/front/{sid[0]}/{sid[1]}/{sid}.jpg?{card.get('image_version') or ''}"


def _get(url):
    try:
        resp = requests.get(url, timeout=20, headers={"User-Agent": "ManaLedger/1.0 (personal collection app)",
                                                      "Accept": "application/json"})
    except requests.RequestException as exc:
        raise EdhrecError(f"Couldn't reach EDHREC: {exc}") from exc
    if resp.status_code in (403, 404):
        return None
    if resp.status_code >= 400:
        raise EdhrecError(f"EDHREC returned {resp.status_code}.")
    try:
        return resp.json()
    except ValueError as exc:
        raise EdhrecError("EDHREC sent something that isn't card data.") from exc


def parse(data):
    """Card rows from an EDHREC commander page: one per card name (first list it appears in)."""
    lists = ((data or {}).get("container") or {}).get("json_dict", {}).get("cardlists") or []
    cards, seen = [], set()
    for cardlist in lists:
        header = cardlist.get("header", "")
        if cardlist.get("tag") == "newcards":
            continue  # recent printings: not enough decks yet to rank fairly
        for c in cardlist.get("cardviews") or []:
            name = c.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
            cards.append({
                "name": name, "scryfall_id": c.get("id") or "", "image_version": c.get("image_version") or "",
                "sanitized": c.get("sanitized") or _clean(name),
                "synergy": float(c.get("synergy") or 0), "num_decks": int(c.get("num_decks") or 0),
                "potential_decks": int(c.get("potential_decks") or 0), "list": header,
            })
    return cards


def page(commander_names, refresh=False):
    """The saved EdhrecPage for these commanders, fetched from EDHREC if missing or a week old.

    Raises EdhrecError if it can't be fetched and nothing is saved yet."""
    from .models import EdhrecPage

    key = slug(commander_names)
    if not key:
        raise EdhrecError("Add a commander to the deck first.")
    saved = EdhrecPage.objects.filter(slug=key).first()
    if saved and not refresh and timezone.now() - saved.fetched_at < MAX_AGE:
        return saved
    try:
        data = _get(JSON_URL.format(slug=key))
        if data and data.get("redirect"):
            data = _get("https://json.edhrec.com/pages" + data["redirect"] + ".json")
        if not data:
            raise EdhrecError(f"EDHREC has no page for {' + '.join(commander_names)} yet.")
        cards = parse(data)
        if not cards:
            raise EdhrecError("EDHREC's page for this commander has no card lists.")
    except EdhrecError:
        if saved:
            return saved  # keep showing last week's list
        raise
    decks = max((c["potential_decks"] for c in cards), default=0)
    saved, _ = EdhrecPage.objects.update_or_create(
        slug=key, defaults={"cards": cards, "num_decks": decks, "fetched_at": timezone.now()})
    return saved


def top(cards, n=20, sort="inclusion"):
    """The n highest-rated cards: most played (share of the commander's decks) or highest synergy."""
    rows = []
    for c in cards:
        row = dict(c)
        row["inclusion"] = round(100 * c["num_decks"] / c["potential_decks"]) if c["potential_decks"] else 0
        row["synergy_pct"] = round(100 * c["synergy"])
        row["url"] = f"https://edhrec.com/cards/{c['sanitized']}"
        row["image"] = card_image(c)
        rows.append(row)
    if sort == "synergy":
        rows.sort(key=lambda r: (-r["synergy"], -r["inclusion"], r["name"]))
    else:
        rows.sort(key=lambda r: (-r["inclusion"], -r["synergy"], r["name"]))
    return rows[:n]
