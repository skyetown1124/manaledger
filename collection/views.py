import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db import transaction
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import decks as deckrules
from . import importer, scryfall, search, services
from .models import Card, CollectionItem, CollectionSnapshot, Deck, DeckCard, PriceSnapshot
from .stats import build_dashboard

BASIC_LANDS = ["Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"]


def _f(value):
    return float(value) if value is not None else None


def _decimal_or_none(text):
    text = (text or "").strip().lstrip("$")
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value >= 0 else None


def _positive_int(text, default=1):
    try:
        return max(0, int(text))
    except (TypeError, ValueError):
        return default


def _back(request, default):
    """Go back to the page the form came from, but only within this site."""
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return redirect(target)
    return redirect(default)


def _rows(items):
    """CollectionItems -> plain dicts for stats.build_dashboard()."""
    # Each card's price on the most recent day before its latest snapshot.
    latest = {}
    previous = {}
    for snap in PriceSnapshot.objects.filter(card__items__isnull=False).distinct().order_by("card_id", "-date"):
        if snap.card_id not in latest:
            latest[snap.card_id] = snap
        elif snap.card_id not in previous:
            previous[snap.card_id] = snap

    rows = []
    for item in items:
        card = item.card
        prev = previous.get(card.id)
        rows.append({
            "name": card.name,
            "set_code": card.set_code,
            "set_name": card.set_name,
            "rarity": card.rarity,
            "colors": card.colors,
            "type_line": card.type_line,
            "finish": item.finish,
            "quantity": item.quantity,
            "unit_price": _f(item.unit_price),
            "prev_price": _f(prev.price_for(item.finish)) if prev else None,
            "purchase_price": _f(item.purchase_price),
            "scryfall_uri": card.scryfall_uri,
        })
    return rows


def dashboard(request):
    items = list(CollectionItem.objects.select_related("card"))
    snapshots = [(s.date, float(s.total_value)) for s in CollectionSnapshot.objects.all()]
    last = items and max((i.card.prices_updated for i in items if i.card.prices_updated), default=None)
    last_local = timezone.localtime(last) if last else None
    context = build_dashboard(_rows(items), snapshots, last_local, timezone.localdate())
    context.update({"active_tab": "dashboard", "item_count": len(items)})
    return render(request, "collection/dashboard.html", context)


SORTS = {
    "value": None,  # sorted in Python, since value depends on finish
    "name": "card__name",
    "set": "card__set_name",
    "mv": "card__cmc",
    "added": "-added_at",
}


def cards(request):
    q = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "value")
    items = CollectionItem.objects.select_related("card").prefetch_related("card__deck_entries__deck")
    found = search.parse(q)
    if q:
        items = items.filter(card__in=Card.objects.filter(found.q))
    if SORTS.get(sort):
        items = list(items.order_by(SORTS[sort]))
    else:
        sort = "value"
        items = sorted(items, key=lambda i: i.total_value, reverse=True)

    rows = []
    for item in items:
        gain = None
        if item.purchase_price is not None and item.unit_price is not None:
            gain = (item.unit_price - item.purchase_price) * item.quantity
        decks = sorted({e.deck.name for e in item.card.deck_entries.all()})
        rows.append({"item": item, "card": item.card, "unit_price": item.unit_price,
                     "total": item.total_value, "gain": gain, "decks": decks})
    total = sum((r["total"] for r in rows), Decimal("0"))
    return render(request, "collection/cards.html", {
        "active_tab": "cards", "rows": rows, "q": q, "sort": sort, "total": total,
        "copies": sum(r["item"].quantity for r in rows),
        "search": found, "show_text": bool(found.highlight),
        "missing_text": Card.objects.filter(items__isnull=False, details_loaded=False).distinct().count(),
    })


@require_POST
def item_update(request, pk):
    item = get_object_or_404(CollectionItem, pk=pk)
    qty = _positive_int(request.POST.get("quantity"), item.quantity)
    if qty == 0:
        name = str(item.card.name)
        item.delete()
        messages.success(request, f"Removed {name} from your collection.")
    else:
        item.quantity = qty
        item.purchase_price = _decimal_or_none(request.POST.get("purchase_price"))
        item.save()
        messages.success(request, f"Updated {item.card.name}.")
    services.record_collection_value()
    return _back(request, "collection:cards")


@require_POST
def item_delete(request, pk):
    item = get_object_or_404(CollectionItem, pk=pk)
    name = item.card.name
    item.delete()
    services.record_collection_value()
    messages.success(request, f"Removed {name} from your collection.")
    return _back(request, "collection:cards")


def add_cards(request):
    q = request.GET.get("q", "").strip()
    set_code = request.GET.get("set", "").strip().lower()
    results, error = [], None
    if q:
        try:
            extra = f" set:{set_code}" if set_code else ""
            results = scryfall.search(f'!"{q}"{extra}')  # exact name first
            if not results:
                results = scryfall.search(f"{q}{extra}")
        except scryfall.ScryfallError as exc:
            error = str(exc)
    owned = {}
    for item in CollectionItem.objects.select_related("card"):
        owned.setdefault(item.card.scryfall_id, []).append(f"{item.quantity} {item.get_finish_display().lower()}")
    for r in results:
        r["owned"] = ", ".join(owned.get(r["scryfall_id"], []))
        r["finish_options"] = [
            {"value": f, "label": dict(CollectionItem.FINISH_CHOICES).get(f, f),
             "price": r.get({"nonfoil": "usd", "foil": "usd_foil", "etched": "usd_etched"}.get(f, ""))}
            for f in r["finishes"] if f in dict(CollectionItem.FINISH_CHOICES)
        ]
    return render(request, "collection/add.html", {
        "active_tab": "add", "q": q, "set_code": set_code, "results": results, "error": error,
    })


@require_POST
def add_card(request):
    finish = request.POST.get("finish", "nonfoil")
    if finish not in dict(CollectionItem.FINISH_CHOICES):
        finish = "nonfoil"
    qty = max(1, _positive_int(request.POST.get("quantity"), 1))
    try:
        item, created = services.add_to_collection(
            request.POST["scryfall_id"], finish, qty,
            _decimal_or_none(request.POST.get("purchase_price")),
        )
    except (KeyError, scryfall.ScryfallError) as exc:
        messages.error(request, f"Couldn't add that card. {exc}")
    else:
        verb = "Added" if created else "Now own"
        count = qty if created else item.quantity
        messages.success(request, f"{verb} {count} x {item.card.name} ({item.card.set_code.upper()}, {item.get_finish_display().lower()}).")
    return _back(request, "collection:add")


IMPORT_EXAMPLE = """4 Lightning Bolt (2XM) 117
1 Sheoldred, the Apocalypse (DMU) 107
1 Sol Ring (CMM) 410 *F*
2 Counterspell
"""


def import_cards(request):
    """Paste a card list -> Check list (preview) -> Add to library."""
    text = request.POST.get("cards", "") if request.method == "POST" else ""
    action = request.POST.get("action", "")
    result, error = None, None

    if request.method == "POST" and action == "add":
        try:
            items = json.loads(request.POST.get("items", "[]"))
            assert isinstance(items, list) and all({"id", "finish", "quantity"} <= set(i) for i in items)
        except (ValueError, AssertionError):
            messages.error(request, "That import couldn't be read. Check the list again and press Add.")
            return redirect("collection:import")
        try:
            summary = services.import_items(items)
        except scryfall.ScryfallError as exc:
            error = f"Nothing was added. {exc}"
        else:
            msg = f"Added {summary['copies']} cards ({summary['cards']} printings) to your library."
            if summary["missing"]:
                msg += f" Scryfall couldn't find {len(summary['missing'])} of them this time."
            messages.success(request, msg)
            return redirect("collection:cards")

    elif request.method == "POST":
        if not text.strip():
            error = "Paste at least one card, one per line."
        else:
            try:
                result = importer.resolve(text, scryfall.lookup)
            except scryfall.ScryfallError as exc:
                error = f"Couldn't check the list. {exc}"

    return render(request, "collection/import.html", {
        "active_tab": "import", "text": text, "result": result, "error": error,
        "example": IMPORT_EXAMPLE,
        "items_json": json.dumps(result.items()) if result else "[]",
    })


@require_POST
def refresh(request):
    try:
        result = services.refresh_prices()
    except scryfall.ScryfallError as exc:
        messages.error(request, f"Prices weren't updated. {exc}")
    else:
        msg = f"Updated prices for {result['updated']} cards. Collection value: ${result['total']:,.2f}."
        if result["missing"]:
            msg += f" Scryfall couldn't find {len(result['missing'])} card(s)."
        messages.success(request, msg)
    return _back(request, "collection:dashboard")


def autocomplete(request):
    try:
        names = scryfall.autocomplete(request.GET.get("q", ""))
    except scryfall.ScryfallError:
        names = []
    return JsonResponse({"names": names})


# ------------------------------------------------------------------ Decks

def _owned_counts():
    """card id -> copies you own, all finishes together."""
    return dict(
        CollectionItem.objects.values("card_id").annotate(n=Sum("quantity")).values_list("card_id", "n")
    )


def _sum_quantity(queryset):
    return queryset.aggregate(n=Sum("quantity"))["n"] or 0


def _boards(deck):
    """Deck sections this format uses, plus any section that already has cards."""
    wanted = ["commander", "main"] if deck.format == "commander" else ["main", "side"]
    used = set(deck.entries.values_list("board", flat=True))
    return [(k, label) for k, label in DeckCard.BOARD_CHOICES if k in wanted or k in used]


def _board_from(deck, value):
    allowed = [k for k, _ in _boards(deck)]
    return value if value in allowed else "main"


def _deck_entries(deck, owned):
    rows = []
    for entry in deck.entries.all():
        c = entry.card
        rows.append({
            "id": entry.pk, "card_id": c.id, "name": c.name, "set_code": c.set_code,
            "collector_number": c.collector_number, "board": entry.board, "quantity": entry.quantity,
            "type_line": c.type_line, "mana_cost": c.mana_cost, "cmc": c.cmc, "colors": c.colors,
            "color_identity": c.color_identity, "oracle_text": c.oracle_text,
            "legalities": c.legalities or {}, "details_loaded": c.details_loaded,
            "price": _f(c.best_price), "owned": owned.get(c.id, 0), "is_basic": c.is_basic_land,
            "scryfall_uri": c.scryfall_uri, "image_url": c.image_url,
        })
    return rows


def deck_list(request):
    owned = _owned_counts()
    summaries = [
        {"deck": deck, "stats": deckrules.analyze(deck.format, _deck_entries(deck, owned))}
        for deck in Deck.objects.prefetch_related("entries__card")
    ]
    return render(request, "collection/decks.html", {
        "active_tab": "decks", "summaries": summaries, "formats": Deck.FORMAT_CHOICES,
        "library_size": len(owned),
    })


@require_POST
def deck_create(request):
    name = request.POST.get("name", "").strip()[:100]
    fmt = request.POST.get("format", "commander")
    if fmt not in dict(Deck.FORMAT_CHOICES):
        fmt = "commander"
    if not name:
        messages.error(request, "Give the deck a name.")
        return redirect("collection:decks")
    deck = Deck.objects.create(name=name, format=fmt, description=request.POST.get("description", "").strip())
    messages.success(request, f"Created {deck.name}. Add cards from your library on the right.")
    return redirect("collection:deck", pk=deck.pk)


def deck_detail(request, pk):
    deck = get_object_or_404(Deck.objects.prefetch_related("entries__card"), pk=pk)
    owned = _owned_counts()
    entries = _deck_entries(deck, owned)
    stats = deckrules.analyze(deck.format, entries)

    # Other decks that use the same cards (you can share a card between decks).
    elsewhere = {}
    for card_id, deck_name in DeckCard.objects.exclude(deck=deck).values_list("card_id", "deck__name"):
        elsewhere.setdefault(card_id, set()).add(deck_name)
    for e in entries:
        e["elsewhere"] = sorted(elsewhere.get(e["card_id"], []))

    # Library panel: cards you own, with how many copies are still free for this deck.
    q = request.GET.get("q", "").strip()
    found = search.parse(q)
    commander_colors = "".join(sorted(
        {c for e in entries if e["board"] == "commander" for c in (e["color_identity"] or "")},
        key="WUBRG".index,
    ))
    has_commander = any(e["board"] == "commander" for e in entries)
    fit = deck.format == "commander" and has_commander and request.GET.get("fit", "1") != "0"

    library = Card.objects.filter(items__isnull=False).distinct()
    if q:
        library = library.filter(found.q)
    if fit:
        library = library.filter(search.identity_q(commander_colors))
    in_deck = {}
    for e in entries:
        in_deck[e["card_id"]] = in_deck.get(e["card_id"], 0) + e["quantity"]
    matches = library.count()
    results = []
    for card in library.order_by("name", "set_code")[:60]:
        have = owned.get(card.id, 0)
        used = in_deck.get(card.id, 0)
        results.append({"card": card, "owned": have, "used": used, "free": max(0, have - used)})

    boards = _boards(deck)
    return render(request, "collection/deck.html", {
        "active_tab": "decks", "deck": deck, "stats": stats, "boards": boards,
        "default_board": "commander" if deck.format == "commander" and not has_commander else "main",
        "formats": Deck.FORMAT_CHOICES, "q": q, "search": found, "results": results,
        "matches": matches, "fit": fit, "can_fit": deck.format == "commander" and has_commander,
        "commander_colors": commander_colors, "basic_lands": BASIC_LANDS,
        "chart_data": {"curve": stats["curve"], "symbols": stats["symbols"]},
    })


@require_POST
def deck_add(request, pk):
    deck = get_object_or_404(Deck, pk=pk)
    card = get_object_or_404(Card, pk=request.POST.get("card_id"))
    board = _board_from(deck, request.POST.get("board"))
    qty = max(1, _positive_int(request.POST.get("quantity"), 1))
    back = reverse("collection:deck", args=[deck.pk])

    note = ""
    if not card.is_basic_land:
        owned = _sum_quantity(CollectionItem.objects.filter(card=card))
        free = owned - _sum_quantity(DeckCard.objects.filter(deck=deck, card=card))
        if owned == 0:
            messages.error(request, f"{card.name} isn't in your library. Add it on the Add Cards tab first.")
            return _back(request, back)
        if free <= 0:
            messages.error(request, f"All {owned} of your copies of {card.name} are already in this deck.")
            return _back(request, back)
        if qty > free:
            qty, note = free, f" You own {owned}, so that's all of them."

    entry, created = DeckCard.objects.get_or_create(deck=deck, card=card, board=board, defaults={"quantity": qty})
    if not created:
        entry.quantity += qty
        entry.save()
    deck.save(update_fields=["updated_at"])
    messages.success(request, f"Added {qty} x {card.name} to {entry.get_board_display().lower()}.{note}")
    return _back(request, back)


@require_POST
def deck_entry_update(request, pk):
    entry = get_object_or_404(DeckCard.objects.select_related("deck", "card"), pk=pk)
    deck, card = entry.deck, entry.card
    back = reverse("collection:deck", args=[deck.pk])
    qty = _positive_int(request.POST.get("quantity"), entry.quantity)
    board = _board_from(deck, request.POST.get("board", entry.board))

    if qty == 0:
        entry.delete()
        messages.success(request, f"Removed {card.name} from {deck.name}.")
        deck.save(update_fields=["updated_at"])
        return _back(request, back)

    note = ""
    if not card.is_basic_land:
        owned = _sum_quantity(CollectionItem.objects.filter(card=card))
        others = _sum_quantity(DeckCard.objects.filter(deck=deck, card=card).exclude(pk=entry.pk))
        if qty > owned - others:
            qty = max(0, owned - others)
            note = f" You own {owned}, so it's capped at {qty}."
            if qty == 0:
                entry.delete()
                messages.error(request, f"All your copies of {card.name} are already used elsewhere in this deck.")
                return _back(request, back)

    with transaction.atomic():
        target = DeckCard.objects.filter(deck=deck, card=card, board=board).exclude(pk=entry.pk).first()
        if target:  # moving onto a section that already has this card: merge them
            target.quantity += qty
            target.save()
            entry.delete()
        else:
            entry.quantity, entry.board = qty, board
            entry.save()
        deck.save(update_fields=["updated_at"])
    messages.success(request, f"Updated {card.name}.{note}")
    return _back(request, back)


@require_POST
def deck_add_basics(request, pk):
    deck = get_object_or_404(Deck, pk=pk)
    added = []
    for name in BASIC_LANDS:
        n = _positive_int(request.POST.get(name.lower()), 0)
        if not n:
            continue
        try:
            card = services.basic_land(name)
        except scryfall.ScryfallError as exc:
            messages.error(request, f"Couldn't get {name} from Scryfall. {exc}")
            continue
        entry, created = DeckCard.objects.get_or_create(deck=deck, card=card, board="main", defaults={"quantity": n})
        if not created:
            entry.quantity += n
            entry.save()
        added.append(f"{n} {name}")
    if added:
        deck.save(update_fields=["updated_at"])
        messages.success(request, "Added " + ", ".join(added) + ".")
    return redirect("collection:deck", pk=deck.pk)


@require_POST
def deck_update(request, pk):
    deck = get_object_or_404(Deck, pk=pk)
    name = request.POST.get("name", "").strip()[:100]
    fmt = request.POST.get("format", deck.format)
    if name:
        deck.name = name
    if fmt in dict(Deck.FORMAT_CHOICES):
        deck.format = fmt
    deck.description = request.POST.get("description", "").strip()
    deck.save()
    messages.success(request, f"Saved {deck.name}.")
    return redirect("collection:deck", pk=deck.pk)


@require_POST
def deck_delete(request, pk):
    deck = get_object_or_404(Deck, pk=pk)
    if request.POST.get("confirm") != "yes":
        messages.error(request, "Tick the box to confirm, then press Delete deck.")
        return redirect("collection:deck", pk=deck.pk)
    name = deck.name
    deck.delete()
    messages.success(request, f"Deleted {name}. The cards are still in your library.")
    return redirect("collection:decks")
