"""Back up and restore your own data (collection, decks, price history) as a JSON file.

The file is the same format as `python manage.py dumpdata`, so `loaddata` can read it
too. Card text, rulings, the Comprehensive Rules and the precon list aren't included:
the app downloads those again when needed.
"""
import json

from django.core import serializers
from django.core.management.color import no_style
from django.db import connection, transaction
from django.utils import timezone

from .models import Card, CollectionItem, CollectionSnapshot, Deck, DeckCard, PriceSnapshot

# Order matters on restore: cards before anything that points at them.
MODELS = [Card, CollectionItem, PriceSnapshot, CollectionSnapshot, Deck, DeckCard]
LABELS = {m._meta.label_lower: m for m in MODELS}


class BackupError(Exception):
    pass


def counts():
    return {
        "cards owned": sum(CollectionItem.objects.values_list("quantity", flat=True)),
        "decks": Deck.objects.count(),
        "printings saved": Card.objects.count(),
        "price history entries": PriceSnapshot.objects.count(),
    }


def dump():
    """JSON text of every object to back up."""
    objects = []
    for model in MODELS:
        objects.extend(json.loads(serializers.serialize("json", model.objects.order_by("pk"))))
    return json.dumps(objects, indent=1)


def filename():
    return f"manaledger-backup-{timezone.localdate():%Y-%m-%d}.json"


def restore(raw):
    """Replace your collection, decks and price history with the backup's. All or nothing."""
    try:
        data = json.loads(raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise BackupError("That file isn't a Mana Ledger backup (it isn't JSON).") from exc
    if not isinstance(data, list) or not all(isinstance(o, dict) and "model" in o for o in data):
        raise BackupError("That file isn't a Mana Ledger backup.")
    unknown = {o["model"] for o in data} - set(LABELS)
    data = [o for o in data if o["model"] in LABELS]  # skip rules etc. from a full dumpdata
    if not data:
        raise BackupError("That file has no cards or decks in it.")
    rank = {label: i for i, label in enumerate(LABELS)}
    data.sort(key=lambda o: rank[o["model"]])
    try:
        with transaction.atomic():
            Deck.objects.all().delete()          # deck cards go with them
            CollectionSnapshot.objects.all().delete()
            Card.objects.all().delete()          # owned copies and price history go with them
            for obj in serializers.deserialize("json", json.dumps(data)):
                obj.save()
            _reset_sequences()
    except Exception as exc:  # bad rows: nothing was changed
        raise BackupError(f"The backup couldn't be restored, so nothing was changed. ({exc})") from exc
    return {"objects": len(data), "skipped_kinds": sorted(unknown)}


def _reset_sequences():
    """Postgres: move id counters past the restored ids so new rows don't collide."""
    sql = connection.ops.sequence_reset_sql(no_style(), MODELS)
    if sql:
        with connection.cursor() as cursor:
            for statement in sql:
                cursor.execute(statement)
