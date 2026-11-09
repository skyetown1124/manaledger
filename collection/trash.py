"""Deleting decks, with a "Recently deleted" list to put them back from.

A deleted deck's settings, cards, saved versions and want-list links are kept as JSON
for KEEP_DAYS days, then cleared. Your cards stay in your library either way.
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import versions

KEEP_DAYS = 30
FIELDS = ["description", "target_bracket", "spellbook_result", "source", "source_id", "source_url"]


def _iso(dt):
    return dt.isoformat() if dt else None


def delete_deck(deck):
    """Delete a deck, keeping a copy in Recently deleted. Returns the DeletedDeck."""
    from .models import DeletedDeck

    cards = versions.current_cards(deck)
    data = {f: getattr(deck, f) for f in FIELDS}
    data.update({
        "cover_card_id": deck.cover_card_id,
        "synced_at": _iso(deck.synced_at),
        "spellbook_checked_at": _iso(deck.spellbook_checked_at),
        "created_at": _iso(deck.created_at),
        "cards": cards,
        "versions": [{"created_at": _iso(v.created_at), "label": v.label, "cards": v.cards, "size": v.size}
                     for v in deck.versions.all()],
        "wants": list(deck.wants.values_list("pk", flat=True)),
    })
    with transaction.atomic():
        gone = DeletedDeck.objects.create(
            name=deck.name, format=deck.format, data=data,
            size=sum(c["quantity"] for c in cards if c["board"] != "side"))
        deck.delete()
    purge()
    return gone


def restore(gone):
    """Put a deleted deck back. Returns (deck, cards_skipped)."""
    from .models import Card, Deck, DeckCard, DeckVersion, WantItem

    d = gone.data
    known = set(Card.objects.filter(pk__in=[c["card_id"] for c in d.get("cards", [])]
                                    + ([d["cover_card_id"]] if d.get("cover_card_id") else []))
                .values_list("pk", flat=True))
    skipped = 0
    with transaction.atomic():
        deck = Deck.objects.create(
            name=gone.name, format=gone.format,
            cover_card_id=d.get("cover_card_id") if d.get("cover_card_id") in known else None,
            synced_at=parse_datetime(d["synced_at"]) if d.get("synced_at") else None,
            spellbook_checked_at=parse_datetime(d["spellbook_checked_at"]) if d.get("spellbook_checked_at") else None,
            **{f: d.get(f) if d.get(f) is not None else Deck._meta.get_field(f).get_default() for f in FIELDS})
        for c in d.get("cards", []):
            if c["card_id"] not in known:
                skipped += 1
                continue
            DeckCard.objects.create(deck=deck, card_id=c["card_id"], board=c["board"], quantity=c["quantity"],
                                    proxies=min(c.get("proxies", 0), c["quantity"]))
        for v in reversed(d.get("versions", [])):  # oldest first, so ordering stays right
            ver = DeckVersion.objects.create(deck=deck, label=v.get("label", ""), cards=v.get("cards", []),
                                             size=v.get("size", 0))
            if v.get("created_at"):
                DeckVersion.objects.filter(pk=ver.pk).update(created_at=parse_datetime(v["created_at"]))
        WantItem.objects.filter(pk__in=d.get("wants", []), deck__isnull=True).update(deck=deck)
        if d.get("created_at"):
            Deck.objects.filter(pk=deck.pk).update(created_at=parse_datetime(d["created_at"]))
        gone.delete()
    return deck, skipped


def purge():
    from .models import DeletedDeck

    DeletedDeck.objects.filter(deleted_at__lt=timezone.now() - timedelta(days=KEEP_DAYS)).delete()
