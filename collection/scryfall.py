"""A small client for the Scryfall API (https://scryfall.com/docs/api).

Scryfall is free and needs no API key, but it asks every client to:
  * send a User-Agent and an Accept header, and
  * wait 50-100 ms between requests.
This module does both.
"""
import time
from datetime import date
from decimal import Decimal, InvalidOperation

import requests

BASE_URL = "https://api.scryfall.com"
COLLECTION_BATCH = 75  # Scryfall's limit for POST /cards/collection
_MIN_GAP = 0.1  # seconds between requests
_last_request = 0.0
_session = requests.Session()


class ScryfallError(Exception):
    """Raised when Scryfall can't be reached or returns an error."""


def _user_agent():
    try:
        from django.conf import settings
        return getattr(settings, "SCRYFALL_USER_AGENT", "ManaLedger/1.0")
    except Exception:
        return "ManaLedger/1.0"


def _request(method, path, **kwargs):
    global _last_request
    wait = _MIN_GAP - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    headers = {"User-Agent": _user_agent(), "Accept": "application/json"}
    try:
        resp = _session.request(method, BASE_URL + path, headers=headers, timeout=20, **kwargs)
    except requests.RequestException as exc:
        raise ScryfallError(f"Couldn't reach Scryfall: {exc}") from exc
    finally:
        _last_request = time.monotonic()

    if resp.status_code == 404:
        return None  # "no cards found" is a normal answer, not a failure
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("details", resp.text)
        except ValueError:
            detail = resp.text
        raise ScryfallError(f"Scryfall returned {resp.status_code}: {detail}")
    return resp.json()


def search(query, unique="prints", limit=60):
    """Search with Scryfall syntax. unique='prints' lists every printing."""
    data = _request("GET", "/cards/search", params={
        "q": query, "unique": unique, "order": "released", "dir": "desc",
    })
    if not data:
        return []
    return [parse_card(c) for c in data.get("data", [])[:limit]]


def autocomplete(text):
    """Up to 20 card names that start with or contain `text`."""
    if len(text.strip()) < 2:
        return []
    data = _request("GET", "/cards/autocomplete", params={"q": text})
    return data.get("data", []) if data else []


def get_card(scryfall_id):
    data = _request("GET", f"/cards/{scryfall_id}")
    if not data:
        raise ScryfallError("Scryfall has no card with that id.")
    return parse_card(data)


_sets_cache = None


def sets():
    """Every set: [{"code", "name", "size"}]. Fetched once per run of the app."""
    global _sets_cache
    if _sets_cache is None:
        data = _request("GET", "/sets") or {}
        _sets_cache = [
            {"code": s["code"], "name": s.get("name", ""), "size": s.get("printed_size") or s.get("card_count") or 0}
            for s in data.get("data", [])
        ]
    return _sets_cache


def fuzzy(name):
    """Best match for a possibly misspelled name (e.g. from OCR), or None."""
    if len(name.strip()) < 3:
        return None
    data = _request("GET", "/cards/named", params={"fuzzy": name})
    return parse_card(data) if data else None


def named(name):
    """The default printing of a card, by exact name (used for basic lands)."""
    data = _request("GET", "/cards/named", params={"exact": name})
    if not data:
        raise ScryfallError(f'Scryfall has no card named "{name}".')
    return parse_card(data)


def lookup(identifiers):
    """Fetch cards for Scryfall identifiers, 75 per request.

    Each identifier is a dict such as {"id": ...}, {"name": ...},
    {"name": ..., "set": ...} or {"set": ..., "collector_number": ...}.
    Returns (cards, not_found_identifiers).
    """
    cards, missing = [], []
    identifiers = list(identifiers)
    for start in range(0, len(identifiers), COLLECTION_BATCH):
        batch = identifiers[start:start + COLLECTION_BATCH]
        data = _request("POST", "/cards/collection", json={"identifiers": batch}) or {}
        cards += [parse_card(c) for c in data.get("data", [])]
        missing += data.get("not_found", [])
    return cards, missing


def get_many(scryfall_ids):
    """Fetch many cards by id. Returns (cards, missing_ids)."""
    cards, missing = lookup([{"id": i} for i in scryfall_ids])
    return cards, [m.get("id") for m in missing]


def _price(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def parse_card(raw):
    """Turn a Scryfall card object into the fields our Card model stores."""
    faces = raw.get("card_faces") or []
    image = (raw.get("image_uris") or {}).get("normal")
    if not image and faces:
        image = (faces[0].get("image_uris") or {}).get("normal", "")

    colors = raw.get("colors")
    if colors is None:  # double-faced cards keep colors on each face
        colors = sorted({c for f in faces for c in f.get("colors", [])})

    released = raw.get("released_at")
    prices = raw.get("prices") or {}

    def from_faces(key, joiner):
        """Top-level value, or each face's value joined (double-faced and split cards)."""
        if raw.get(key):
            return raw[key]
        parts = [f.get(key, "") for f in faces if f.get(key)]
        return joiner.join(parts)

    def first_face(key):
        return raw.get(key) or next((f.get(key) for f in faces if f.get(key)), "") or ""

    return {
        "mana_cost": from_faces("mana_cost", " // "),
        "cmc": float(raw.get("cmc") or 0),
        "oracle_text": from_faces("oracle_text", "\n//\n"),
        "keywords": ", ".join(raw.get("keywords") or []),
        "power": first_face("power"),
        "toughness": first_face("toughness"),
        "loyalty": first_face("loyalty"),
        "color_identity": "".join(c for c in "WUBRG" if c in (raw.get("color_identity") or [])),
        "legalities": raw.get("legalities") or {},
        "scryfall_id": raw["id"],
        "name": raw.get("name", ""),
        "set_code": raw.get("set", ""),
        "set_name": raw.get("set_name", ""),
        "collector_number": raw.get("collector_number", ""),
        "rarity": raw.get("rarity", ""),
        "colors": "".join(c for c in "WUBRG" if c in colors),
        "type_line": raw.get("type_line", "") or (faces[0].get("type_line", "") if faces else ""),
        "released_at": date.fromisoformat(released) if released else None,
        "image_url": image or "",
        "scryfall_uri": raw.get("scryfall_uri", ""),
        "usd": _price(prices.get("usd")),
        "usd_foil": _price(prices.get("usd_foil")),
        "usd_etched": _price(prices.get("usd_etched")),
        "finishes": raw.get("finishes", ["nonfoil"]),
    }
