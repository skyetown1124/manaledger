"""Commander Spellbook bracket estimate (https://commanderspellbook.com, open source).

POST https://backend.commanderspellbook.com/estimate-bracket
  {"commanders": [{"card": "Name", "quantity": 1}], "main": [{"card": "Name", "quantity": 1}, ...]}
returns its bracket tag for the deck, every combo the deck contains, and which
cards it counts as Game Changers, mass land denial or extra turns.

The app calls this only when you press the button on a deck page, and saves a
short summary on the deck, so normal page views never wait on another website.
"""
import requests

from django.conf import settings

URL = "https://backend.commanderspellbook.com/estimate-bracket"


class SpellbookError(Exception):
    pass


def _names(cards, flag):
    return sorted({c.get("card", {}).get("name", "") for c in cards if c.get(flag)} - {""})


def summarize(data):
    """Keep the parts of Spellbook's answer the deck page shows."""
    combos = []
    for item in data.get("combos", []):
        combo = item.get("combo", {})
        cards = [u.get("card", {}).get("name", "") for u in combo.get("uses", [])]
        results = [p.get("feature", {}).get("name", "") for p in combo.get("produces", [])]
        combos.append({
            "id": combo.get("id", ""),
            "cards": [c for c in cards if c],
            "results": [r for r in results if r][:4],
            "two_card": bool(item.get("definitelyTwoCard")),
            "speed": item.get("speed", 0),
            "relevant": bool(item.get("relevant")),
            "url": f"https://commanderspellbook.com/combo/{combo.get('id')}/" if combo.get("id") else "",
        })
    combos.sort(key=lambda c: (not c["two_card"], -c["speed"], len(c["cards"])))
    cards = data.get("cards", [])
    return {
        "bracketTag": data.get("bracketTag", ""),
        "combos": combos,
        "gameChangers": _names(cards, "gameChanger"),
        "massLandDenial": _names(cards, "massLandDenial"),
        "extraTurns": _names(cards, "extraTurn"),
        "banned": _names(cards, "banned"),
    }


def estimate(commanders, main):
    """commanders: [name]; main: [(name, quantity)]. Returns summarize(...) of the answer."""
    body = {
        "commanders": [{"card": n, "quantity": 1} for n in commanders],
        "main": [{"card": n, "quantity": q} for n, q in main],
    }
    headers = {"User-Agent": getattr(settings, "SCRYFALL_USER_AGENT", "ManaLedger/1.0"),
               "Accept": "application/json"}
    try:
        resp = requests.post(URL, json=body, headers=headers, timeout=30)
    except requests.RequestException as exc:
        raise SpellbookError(f"Couldn't reach Commander Spellbook: {exc}") from exc
    if resp.status_code >= 400:
        raise SpellbookError(f"Commander Spellbook returned {resp.status_code}.")
    try:
        return summarize(resp.json())
    except ValueError as exc:
        raise SpellbookError("Commander Spellbook sent an answer the app couldn't read.") from exc
