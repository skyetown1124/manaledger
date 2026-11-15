"""The Dashboard's price ticker: the biggest price moves among cards you own or want.

Prices come from the daily price snapshots the app already saves from Scryfall each
time prices are refreshed, so this needs no outside service.
"""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

PERIODS = {"day": ("Since the last refresh", None), "week": ("Past 7 days", 7), "month": ("Past 30 days", 30)}
MIN_PRICE = 1.0  # ignore swings on cards worth under $1 both before and after (bulk noise)
LIMIT = 30
FIELD = {"nonfoil": "usd", "foil": "usd_foil", "etched": "usd_etched"}


def compute(lines, history, period="day", today=None, min_price=MIN_PRICE, limit=LIMIT):
    """Pure function: the biggest movers.

    lines:   [{card_id, name, set_code, finish, owned, wanted}] (one per card + finish)
    history: {card_id: [(date, {"usd": x, "usd_foil": y, "usd_etched": z}), ...] newest first}
    Returns movers sorted by size of percentage change, biggest first."""
    today = today or timezone.localdate()
    days = PERIODS.get(period, PERIODS["day"])[1]
    out = []
    for line in lines:
        snaps = history.get(line["card_id"]) or []
        field = FIELD.get(line["finish"], "usd")
        priced = [(d, p[field]) for d, p in snaps if p.get(field) is not None]
        if len(priced) < 2:
            continue
        now_date, now = priced[0]
        if days is None:
            then_date, then = priced[1]
        else:
            cutoff = today - timedelta(days=days)
            older = [(d, p) for d, p in priced[1:] if d <= cutoff]
            then_date, then = older[0] if older else priced[-1]  # the oldest we have, if history is short
        now, then = float(now), float(then)
        if then <= 0 or now == then or max(now, then) < min_price:
            continue
        pct = round((now - then) / then * 100, 1)
        out.append(dict(line, now=round(now, 2), then=round(then, 2), change=round(now - then, 2), pct=pct, abs_pct=abs(pct),
                        since=then_date, up=now > then))
    out.sort(key=lambda m: (-abs(m["pct"]), -abs(m["change"]), m["name"]))
    return out[:limit]


def movers(period="day", today=None):
    """The ticker for the Dashboard, from the database."""
    from .models import CollectionItem, PriceSnapshot, WantItem

    today = today or timezone.localdate()
    lines = {}
    for card_id, name, set_code, finish, qty in CollectionItem.objects.values_list(
            "card_id", "card__name", "card__set_code", "finish", "quantity"):
        line = lines.setdefault((card_id, finish), {"card_id": card_id, "name": name, "set_code": set_code,
                                                    "finish": finish, "owned": 0, "wanted": False})
        line["owned"] += qty
    for card_id, name, set_code, finish in WantItem.objects.values_list(
            "card_id", "card__name", "card__set_code", "finish"):
        finish = "nonfoil" if finish == "any" else finish
        lines.setdefault((card_id, finish), {"card_id": card_id, "name": name, "set_code": set_code,
                                             "finish": finish, "owned": 0, "wanted": True})["wanted"] = True

    # One joined query (no big IN list): snapshots from the last ~5 weeks of cards you own or want.
    history = {}
    since = today - timedelta(days=35)
    snaps = (PriceSnapshot.objects.filter(date__gte=since)
             .filter(Q(card__items__isnull=False) | Q(card__wants__isnull=False)).distinct()
             .order_by("card_id", "-date").values_list("card_id", "date", "usd", "usd_foil", "usd_etched"))
    for card_id, date, usd, foil, etched in snaps:
        history.setdefault(card_id, []).append((date, {"usd": usd, "usd_foil": foil, "usd_etched": etched}))
    return compute(list(lines.values()), history, period, today)
