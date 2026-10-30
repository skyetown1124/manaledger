"""Database work shared by the views and the refresh_prices command."""
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import scryfall
from .models import Card, CollectionItem, CollectionSnapshot, PriceSnapshot

CARD_FIELDS = [
    "name", "set_code", "set_name", "collector_number", "rarity", "colors",
    "type_line", "released_at", "image_url", "scryfall_uri",
    "usd", "usd_foil", "usd_etched",
    "mana_cost", "cmc", "oracle_text", "keywords", "power", "toughness",
    "loyalty", "color_identity", "legalities", "game_changer",
]


def save_card(data):
    """Create or update a Card from parsed Scryfall data, and log today's price."""
    defaults = {f: data[f] for f in CARD_FIELDS if f in data}  # missing fields keep their defaults
    defaults["prices_updated"] = timezone.now()
    defaults["details_loaded"] = True
    card, _ = Card.objects.update_or_create(scryfall_id=data["scryfall_id"], defaults=defaults)
    record_price(card)
    return card


def record_price(card):
    PriceSnapshot.objects.update_or_create(
        card=card,
        date=timezone.localdate(),
        defaults={"usd": card.usd, "usd_foil": card.usd_foil, "usd_etched": card.usd_etched},
    )


def record_collection_value():
    """Save today's total collection value (one row per day)."""
    items = CollectionItem.objects.select_related("card")
    total = sum((i.total_value for i in items), Decimal("0"))
    count = sum(i.quantity for i in items)
    CollectionSnapshot.objects.update_or_create(
        date=timezone.localdate(),
        defaults={"total_value": total, "card_count": count},
    )
    return total


@transaction.atomic
def add_to_collection(scryfall_id, finish, quantity, purchase_price=None):
    card = save_card(scryfall.get_card(scryfall_id))
    item, created = CollectionItem.objects.get_or_create(
        card=card, finish=finish,
        defaults={"quantity": quantity, "purchase_price": purchase_price},
    )
    if not created:
        item.quantity += quantity
        if purchase_price is not None:
            item.purchase_price = purchase_price
        item.save()
    record_collection_value()
    return item, created


def import_items(items):
    """Add checked import rows [{id, finish, quantity}] to the collection.

    Fetches every card again in batches of 75 (so prices are current) and
    returns {"cards": printings added, "copies": copies added, "missing": [ids]}.
    """
    wanted = {}
    for row in items:
        finish = row["finish"] if row["finish"] in dict(CollectionItem.FINISH_CHOICES) else "nonfoil"
        qty = max(1, min(999, int(row["quantity"])))
        wanted.setdefault(row["id"], []).append((finish, qty))
    fresh, missing = scryfall.get_many(list(wanted))

    copies = printings = 0
    with transaction.atomic():
        for data in fresh:
            card = save_card(data)
            for finish, qty in wanted.get(data["scryfall_id"], []):
                item, created = CollectionItem.objects.get_or_create(
                    card=card, finish=finish, defaults={"quantity": qty})
                if not created:
                    item.quantity += qty
                    item.save()
                copies += qty
                printings += 1
        record_collection_value()
    return {"cards": printings, "copies": copies, "missing": missing}


RULINGS_MAX_AGE_DAYS = 30


def load_rulings(card, force=False):
    """Fetch a card's official rulings from Scryfall if they're missing or over a month old.

    Raises scryfall.ScryfallError when Scryfall can't be reached (saved rulings stay)."""
    from datetime import timedelta

    from .models import Ruling

    fresh = card.rulings_checked_at and timezone.now() - card.rulings_checked_at < timedelta(days=RULINGS_MAX_AGE_DAYS)
    if fresh and not force:
        return False
    found = scryfall.rulings(card.scryfall_id)
    with transaction.atomic():
        card.rulings.all().delete()
        Ruling.objects.bulk_create([
            Ruling(card=card, source=r.get("source", ""), comment=r.get("comment", ""),
                   published_at=r.get("published_at") or None)
            for r in found if r.get("comment")
        ])
        card.rulings_checked_at = timezone.now()
        card.save(update_fields=["rulings_checked_at"])
    return True


def basic_land(name):
    """A Card for a basic land: a printing already saved, or Scryfall's default one."""
    card = Card.objects.filter(name=name, type_line__contains="Basic").order_by("-released_at").first()
    return card or save_card(scryfall.named(name))


def refresh_prices():
    """Pull current prices and card text for every card you own or use in a deck."""
    ids = list(
        Card.objects.filter(Q(items__isnull=False) | Q(deck_entries__isnull=False))
        .distinct().values_list("scryfall_id", flat=True)
    )
    if not ids:
        return {"updated": 0, "missing": [], "total": Decimal("0")}
    fresh, missing = scryfall.get_many(ids)
    with transaction.atomic():
        for data in fresh:
            save_card(data)
        total = record_collection_value()
    return {"updated": len(fresh), "missing": missing, "total": total}
