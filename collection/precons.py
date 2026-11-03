"""Official preconstructed decks, from MTGJSON (https://mtgjson.com, free and open data).

    https://mtgjson.com/api/v5/DeckList.json         every official deck: code, fileName, name, releaseDate, type
    https://mtgjson.com/api/v5/decks/<fileName>.json  one deck: commander, mainBoard, sideBoard
        each card: name, count, setCode, number, isFoil, identifiers.scryfallId

The catalog is cached in the database (Precon rows) and refreshed when it's over a
week old. A deck is fetched only when you build it.
"""
from datetime import timedelta

import requests
from django.db import transaction
from django.utils import timezone

API = "https://mtgjson.com/api/v5/"
CATALOG_MAX_AGE = timedelta(days=7)
HEADERS = {"User-Agent": "ManaLedger/1.0 (personal collection tracker)", "Accept": "application/json"}


class PreconError(Exception):
    pass


def _get_json(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=60)
    except requests.RequestException as exc:
        raise PreconError(f"Couldn't reach MTGJSON: {exc}") from exc
    if resp.status_code == 404:
        raise PreconError("MTGJSON doesn't have that deck any more. Refresh the precon list and try again.")
    if resp.status_code >= 400:
        raise PreconError(f"MTGJSON returned {resp.status_code}.")
    try:
        return resp.json()
    except ValueError as exc:
        raise PreconError("MTGJSON sent something the app couldn't read.") from exc


def refresh_catalog(force=False):
    """Download the deck list if it's missing or stale. Returns the number of decks stored."""
    from .models import Precon

    newest = Precon.objects.order_by("-fetched_at").values_list("fetched_at", flat=True).first()
    if not force and newest and timezone.now() - newest < CATALOG_MAX_AGE:
        return Precon.objects.count()
    data = _get_json(API + "DeckList.json").get("data", [])
    now = timezone.now()
    rows = [
        Precon(file_name=d["fileName"], code=d.get("code", ""), name=d.get("name", d["fileName"]),
               release_date=d.get("releaseDate") or None, deck_type=d.get("type", ""), fetched_at=now)
        for d in data if d.get("fileName")
    ]
    if not rows:
        raise PreconError("MTGJSON's deck list came back empty.")
    with transaction.atomic():
        Precon.objects.all().delete()
        Precon.objects.bulk_create(rows, batch_size=500)
    return len(rows)


def fetch_deck(file_name):
    """Download one deck. Returns {"name", "code", "type", "rows": [{scryfall_id, name, set, number, quantity, board, finish}]}."""
    data = _get_json(f"{API}decks/{requests.utils.quote(file_name)}.json").get("data", {})
    rows = []
    for key, board in (("commander", "commander"), ("mainBoard", "main"), ("sideBoard", "side")):
        for card in data.get(key) or []:
            rows.append({
                "scryfall_id": (card.get("identifiers") or {}).get("scryfallId", ""),
                "name": card.get("name", ""),
                "set": (card.get("setCode") or "").lower(),
                "number": card.get("number", ""),
                "quantity": int(card.get("count") or 1),
                "board": board,
                "finish": "foil" if card.get("isFoil") else "nonfoil",
            })
    if not rows:
        raise PreconError("That deck has no cards in MTGJSON's data.")
    return {"name": data.get("name", file_name), "code": data.get("code", ""),
            "type": data.get("type", ""), "rows": rows}


def deck_format(deck_type):
    """Commander precons become Commander decks; everything else is casual."""
    return "commander" if "commander" in (deck_type or "").lower() else "casual"
