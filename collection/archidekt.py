"""Public Archidekt decks (https://archidekt.com).

Archidekt has no official API, but its site reads public JSON that anyone can fetch:

    GET https://archidekt.com/api/decks/v3/?ownerUsername=<name>&page=N   a user's public decks
        {"count", "next", "results": [{"id", "name", "size", "updatedAt", "deckFormat",
                                       "owner": {"username"}, ...}]}
    GET https://archidekt.com/api/decks/<id>/                              one deck
        {"name", "deckFormat", "categories": [{"name", "includedInDeck", "isPremier"}],
         "cards": [{"quantity", "categories": [...], "modifier": "Normal"|"Foil"|"Etched",
                    "deletedAt", "card": {"uid" (Scryfall id), "collectorNumber",
                                          "edition": {"editioncode"}, "oracleCard": {"name"}}}]}

Private decks return 403 and unlisted decks don't appear in a user's list (a direct
link to an unlisted deck still works). Because the API is unofficial, every field is
read defensively; if Archidekt changes it the app says so instead of crashing.
"""
import re

import requests

SITE = "https://archidekt.com"
API = SITE + "/api/decks/"
HEADERS = {"User-Agent": "ManaLedger/1.0 (personal collection tracker)", "Accept": "application/json"}
MAX_PAGES = 10

# Archidekt's deckFormat numbers for the formats this app checks; anything else is Casual.
FORMATS = {1: "standard", 2: "modern", 3: "commander", 4: "legacy", 5: "vintage", 6: "pauper", 15: "pioneer"}
FORMAT_NAMES = {
    1: "Standard", 2: "Modern", 3: "Commander", 4: "Legacy", 5: "Vintage", 6: "Pauper", 7: "Custom",
    8: "Frontier", 9: "Future Standard", 10: "Penny Dreadful", 11: "1v1 Commander", 12: "Duel Commander",
    13: "Brawl", 14: "Oathbreaker", 15: "Pioneer", 16: "Historic", 17: "Pauper EDH", 18: "Alchemy",
    19: "Explorer", 20: "Historic Brawl", 21: "Gladiator", 22: "Premodern", 23: "PreDH", 24: "Timeless",
    25: "Canadian Highlander",
}

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,60}$")
DECK_URL_RE = re.compile(r"archidekt\.com/(?:api/)?decks/(\d+)", re.I)
PROFILE_URL_RE = re.compile(r"archidekt\.com/(?:u|user)/([^/?#\s]+)", re.I)


class ArchidektError(Exception):
    pass


def clean_username(text):
    """'skye', '@skye' or 'https://archidekt.com/u/skye' -> 'skye'. Raises ArchidektError."""
    text = (text or "").strip()
    match = PROFILE_URL_RE.search(text)
    name = requests.utils.unquote(match.group(1)) if match else text.lstrip("@")
    if not USERNAME_RE.match(name):
        raise ArchidektError("Enter your Archidekt username or profile link, like archidekt.com/u/yourname.")
    return name


def deck_id(text):
    """'https://archidekt.com/decks/123456/my_deck' or '123456' -> 123456, else None."""
    text = (text or "").strip()
    match = DECK_URL_RE.search(text)
    if match:
        return int(match.group(1))
    return int(text) if text.isdigit() else None


def _get_json(url, params=None):
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
    except requests.RequestException as exc:
        raise ArchidektError(f"Couldn't reach Archidekt: {exc}") from exc
    if resp.status_code in (401, 403):
        raise ArchidektError("That deck is private on Archidekt. Make it public or unlisted there, then try again.")
    if resp.status_code == 404:
        raise ArchidektError("Archidekt doesn't have that deck (it may have been deleted).")
    if resp.status_code == 429:
        raise ArchidektError("Archidekt asked the app to slow down. Wait a minute and try again.")
    if resp.status_code >= 400:
        raise ArchidektError(f"Archidekt returned {resp.status_code}.")
    try:
        return resp.json()
    except ValueError as exc:
        raise ArchidektError("Archidekt sent something the app couldn't read.") from exc


def _format_id(data):
    value = data.get("deckFormat", data.get("format"))
    if isinstance(value, dict):
        value = value.get("id")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def user_decks(username):
    """A user's public decks, newest first: [{id, name, size, updated, format_id, format_name, url}]."""
    decks, url = [], API + "v3/"
    params = {"ownerUsername": username, "orderBy": "-updatedAt", "pageSize": 50}
    for _ in range(MAX_PAGES):
        data = _get_json(url, params)
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ArchidektError("Archidekt's deck list has changed shape; the app can't read it right now.")
        for d in data["results"]:
            owner = (d.get("owner") or {}).get("username", "")
            if owner and owner.lower() != username.lower():
                continue  # the search can include other people's decks; keep only this user's
            if not d.get("id"):
                continue
            fid = _format_id(d)
            decks.append({
                "id": int(d["id"]), "name": d.get("name") or f"Deck {d['id']}",
                "size": d.get("size"), "updated": (d.get("updatedAt") or "")[:10],
                "format_id": fid, "format_name": FORMAT_NAMES.get(fid, "Unknown format"),
                "url": f"{SITE}/decks/{d['id']}",
            })
        url, params = data.get("next"), None
        if not url:
            break
    return decks


def fetch_deck(number):
    """One deck: {"id", "name", "format", "url", "rows": [{scryfall_id, name, set, number, quantity, board, finish}]}.

    Maybeboard-style categories (not included in the deck) and deleted rows are skipped.
    The premier category (usually "Commander") goes to the command zone; "Sideboard" to the sideboard."""
    data = _get_json(f"{API}{int(number)}/")
    if not isinstance(data, dict) or not isinstance(data.get("cards"), list):
        raise ArchidektError("Archidekt's deck data has changed shape; the app can't read it right now.")
    cats = {c.get("name", ""): c for c in data.get("categories") or [] if isinstance(c, dict)}
    rows = []
    for entry in data["cards"]:
        if not isinstance(entry, dict) or entry.get("deletedAt"):
            continue
        names = entry.get("categories") or []
        first = cats.get(names[0], {}) if names else {}
        if names and first.get("includedInDeck") is False:
            continue  # Maybeboard, Considering, etc.
        if first.get("isPremier") or (names and names[0].lower() in {"commander", "commanders"}):
            board = "commander"
        elif names and names[0].lower() in {"sideboard", "companion"}:
            board = "side"
        else:
            board = "main"
        card = entry.get("card") or {}
        modifier = (entry.get("modifier") or "").lower()
        rows.append({
            "scryfall_id": card.get("uid") or "",
            "name": (card.get("oracleCard") or {}).get("name") or card.get("name") or "",
            "set": ((card.get("edition") or {}).get("editioncode") or "").lower(),
            "number": str(card.get("collectorNumber") or ""),
            "quantity": max(1, int(entry.get("quantity") or 1)),
            "board": board,
            "finish": "foil" if modifier == "foil" else "etched" if modifier == "etched" else "nonfoil",
        })
    if not rows:
        raise ArchidektError("That Archidekt deck has no cards in it.")
    fid = _format_id(data)
    return {"id": int(number), "name": data.get("name") or f"Archidekt deck {number}",
            "format": FORMATS.get(fid, "casual"), "format_name": FORMAT_NAMES.get(fid, ""),
            "url": f"{SITE}/decks/{int(number)}", "rows": rows}
