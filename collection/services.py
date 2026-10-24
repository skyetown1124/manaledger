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
    "loyalty", "color_identity", "legalities",
]


def save_card(data):
    """Create or update a Card from parsed Scryfall data, and log today's price."""
    defaults = {f: data[f] for f in CARD_FIELDS}
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
