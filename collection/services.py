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


def import_to_deck(deck, items, replace=False, add_to_collection=False):
    """Add checked rows [{id, board, quantity, finish?}] to a deck. Cards don't have to be owned.

    replace: clear the deck list first. add_to_collection: also add these copies to your
    collection (for a precon you bought). Fetches cards from Scryfall 75 at a time.
    Returns {"cards": entries added, "copies": copies, "missing": [ids]}."""
    from .models import DeckCard

    boards = {"main", "side", "commander"}
    wanted = {}
    for row in items:
        board = row.get("board") if row.get("board") in boards else "main"
        qty = max(1, min(99, int(row["quantity"])))
        wanted.setdefault(row["id"], []).append((board, qty, row.get("finish", "nonfoil")))
    fresh, missing = scryfall.get_many(list(wanted))

    copies = entries = 0
    with transaction.atomic():
        if replace:
            deck.entries.all().delete()
        for data in fresh:
            card = save_card(data)
            for board, qty, finish in wanted.get(data["scryfall_id"], []):
                entry, created = DeckCard.objects.get_or_create(deck=deck, card=card, board=board,
                                                                defaults={"quantity": qty})
                if not created:
                    entry.quantity += qty
                    entry.save()
                entries += 1
                copies += qty
                if add_to_collection:
                    finish = finish if finish in dict(CollectionItem.FINISH_CHOICES) else "nonfoil"
                    item, made = CollectionItem.objects.get_or_create(card=card, finish=finish,
                                                                      defaults={"quantity": qty})
                    if not made:
                        item.quantity += qty
                        item.save()
        deck.save(update_fields=["updated_at"])
        if add_to_collection:
            record_collection_value()
    return {"cards": entries, "copies": copies, "missing": missing}


def rows_to_items(rows):
    """Turn deck rows from another site [{scryfall_id, name, set, number, quantity, board, finish}]
    into import_to_deck items. Rows without a Scryfall id are looked up by set and number,
    then by name. Returns (items, names_not_found)."""
    items, by_set, by_name = [], [], []
    for row in rows:
        if row.get("scryfall_id"):
            items.append({"id": row["scryfall_id"], "board": row["board"],
                          "quantity": row["quantity"], "finish": row["finish"]})
        elif row.get("set") and row.get("number"):
            by_set.append(row)
        elif row.get("name"):
            by_name.append(row)
    if by_set:
        found, _ = scryfall.lookup([{"set": r["set"], "collector_number": r["number"]} for r in by_set])
        ids = {(c["set_code"], c["collector_number"]): c["scryfall_id"] for c in found}
        for r in by_set:
            if (r["set"], r["number"]) in ids:
                items.append({"id": ids[(r["set"], r["number"])], "board": r["board"],
                              "quantity": r["quantity"], "finish": r["finish"]})
            elif r.get("name"):
                by_name.append(r)
    missing = []
    if by_name:
        found, _ = scryfall.lookup([{"name": r["name"]} for r in by_name])
        ids = {c["name"].lower(): c["scryfall_id"] for c in found}
        for c in found:  # "Delver of Secrets" matches "Delver of Secrets // Insectile Aberration"
            ids.setdefault(c["name"].split(" // ")[0].lower(), c["scryfall_id"])
        for r in by_name:
            hit = ids.get(r["name"].lower())
            if hit:
                items.append({"id": hit, "board": r["board"], "quantity": r["quantity"], "finish": r["finish"]})
            else:
                missing.append(r["name"])
    return items, missing


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
    """Pull current prices and card text for every card you own, use in a deck or want."""
    ids = list(
        Card.objects.filter(Q(items__isnull=False) | Q(deck_entries__isnull=False) | Q(wants__isnull=False))
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


# ------------------------------------------------------------------ Want list

def add_want(card, quantity=1, any_printing=True, finish="any", max_price=None, priority=2, deck=None, note=""):
    """Add a card to the want list. The same card (any printing, or the same printing),
    finish and deck already on the list just gets more copies. Returns (item, created)."""
    from .models import WantItem

    match = WantItem.objects.filter(finish=finish, deck=deck, any_printing=any_printing)
    match = match.filter(card__name=card.name) if any_printing else match.filter(card=card)
    item = match.first()
    if item:
        item.quantity += quantity
        if max_price is not None:
            item.max_price = max_price
        if priority < item.priority:
            item.priority = priority
        item.save()
        return item, False
    return WantItem.objects.create(card=card, quantity=quantity, any_printing=any_printing, finish=finish,
                                   max_price=max_price, priority=priority, deck=deck, note=note[:200]), True


@transaction.atomic
def got_want(item, quantity, finish=None, price_paid=None):
    """You bought `quantity` copies: add them to the collection and take them off the want list."""
    finish = finish if finish in dict(CollectionItem.FINISH_CHOICES) else (
        item.finish if item.finish in dict(CollectionItem.FINISH_CHOICES) else "nonfoil")
    quantity = max(1, min(quantity, 999))
    owned, created = CollectionItem.objects.get_or_create(
        card=item.card, finish=finish, defaults={"quantity": quantity, "purchase_price": price_paid})
    if not created:
        owned.quantity += quantity
        if price_paid is not None:
            owned.purchase_price = price_paid
        owned.save()
    if quantity >= item.quantity:
        item.delete()
    else:
        item.quantity -= quantity
        item.save()
    record_collection_value()
    return owned



def fill_wants(card, quantity, finish="nonfoil"):
    """You got `quantity` copies of `card` in `finish`: take them off matching want-list
    entries (this printing, or any printing of the card). Returns how many were taken off."""
    from django.db.models import Q

    from .models import WantItem

    left, filled = quantity, 0
    wants = (WantItem.objects.filter(Q(card=card) | Q(any_printing=True, card__name=card.name))
             .filter(Q(finish="any") | Q(finish=finish)).order_by("priority", "added_at"))
    for want in wants:
        if left <= 0:
            break
        take = min(left, want.quantity)
        if take >= want.quantity:
            want.delete()
        else:
            want.quantity -= take
            want.save(update_fields=["quantity"])
        left -= take
        filled += take
    return filled
