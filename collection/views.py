import json
import re
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import decks as deckrules
from . import brackets, importer, ocr, rules, scryfall, search, services, spellbook
from .models import (Card, CollectionItem, CollectionSnapshot, Deck, DeckCard, GlossaryTerm, PriceSnapshot,
                     Rule, RulesEdition)
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


def _read_photos(request):
    """OCR the uploaded photos. Returns (list text, [PhotoResult], error or None)."""
    files = request.FILES.getlist("photos")
    if not files:
        return "", [], "Choose at least one photo."
    if len(files) > ocr.MAX_PHOTOS:
        return "", [], f"That's {len(files)} photos. Upload up to {ocr.MAX_PHOTOS} at a time."
    photos, skipped = [], []
    for f in files:
        if f.size > ocr.MAX_BYTES:
            skipped.append(ocr.PhotoResult(f.name, message="Skipped: larger than 15 MB."))
            continue
        try:
            image = ocr.Image.open(f)
            image.load()
        except Exception:
            skipped.append(ocr.PhotoResult(f.name, message="Skipped: this file isn't a photo the app can open (use JPG or PNG)."))
            continue
        photos.append((f.name, image))
    try:
        results = ocr.identify(photos, ocr.read_lines, scryfall) if photos else []
    except scryfall.ScryfallError as exc:
        return "", skipped, f"Read the photos, but couldn't reach Scryfall to identify the cards. {exc}"
    return ocr.to_list_text(results), results + skipped, None


def import_cards(request):
    """Paste a card list (or read it from photos) -> Check list (preview) -> Add to library."""
    text = request.POST.get("cards", "") if request.method == "POST" else ""
    action = request.POST.get("action", "")
    result, error, photo_results = None, None, []
    ocr_ready, ocr_message = ocr.status()

    if request.method == "POST" and action == "photos":
        if not ocr_ready:
            error = ocr_message
        else:
            found_text, photo_results, error = _read_photos(request)
            if found_text:
                text = (text.rstrip() + "\n" + found_text).strip() if text.strip() else found_text
                try:
                    result = importer.resolve(text, scryfall.lookup)
                except scryfall.ScryfallError as exc:
                    error = f"Couldn't check the list. {exc}"
            elif not error:
                error = "No cards were recognized in those photos. See the notes for each photo below."

    elif request.method == "POST" and action == "add":
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
        "ocr_ready": ocr_ready, "ocr_message": ocr_message, "photo_results": photo_results,
        "max_photos": ocr.MAX_PHOTOS,
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


class RuleError(Exception):
    """A deck change the format's rules don't allow. The message is shown to the user."""


def _rules_dict(card, qty=1):
    return {"name": card.name, "type_line": card.type_line, "oracle_text": card.oracle_text,
            "quantity": qty, "details_loaded": card.details_loaded, "color_identity": card.color_identity}


def _copy_limit(deck, card):
    return deckrules.copy_limit(deck.format, card.type_line, card.oracle_text, card.legalities)


def _limit_text(deck, card, limit):
    if (card.legalities or {}).get(deck.format) == "restricted":
        return f"{card.name} is restricted in {deck.get_format_display()}: one copy only."
    if deck.format == "commander" and limit == 1:
        return "Commander decks allow one copy of each card, except basic lands."
    return f"{deck.get_format_display()} decks allow up to {limit} copies of {card.name}."


def _color_words(letters):
    return "/".join(c for c in "WUBRG" if c in letters) or "colorless"


def check_deck_rules(deck, card, board, qty, entry=None):
    """Apply the deck format's rules to adding `qty` of `card` to `board`, or to editing `entry`.

    Returns (allowed_qty, note). Raises RuleError when the change isn't allowed at all.
    """
    others = DeckCard.objects.filter(deck=deck).select_related("card")
    if entry is not None:
        others = others.exclude(pk=entry.pk)
    others = list(others)
    note = ""

    # Banned or not legal in this format (checked when a card first goes in).
    if entry is None:
        problem = deckrules.legality_problem(deck.format, card.name, card.legalities)
        if problem:
            raise RuleError(problem)

    if deck.format == "commander":
        commanders = [o for o in others if o.board == "commander"]
        if board == "commander":
            candidate = [_rules_dict(o.card) for o in commanders] + [_rules_dict(card)]
            if len(candidate) > 2:
                raise RuleError("This deck already has two commanders. Remove one first.")
            if len(candidate) == 1 and deckrules.is_background(candidate[0]):
                note = " Now add a commander that says \"Choose a Background\"."
            else:
                problem = deckrules.commander_problem(candidate)
                if problem:
                    raise RuleError(problem)
            if qty > 1:
                qty, note = 1, " A deck has one copy of its commander."
        elif commanders:
            allowed = set("".join(o.card.color_identity for o in commanders))
            outside = set(card.color_identity) - allowed
            if outside:
                raise RuleError(
                    f"{card.name} is {_color_words(card.color_identity)}, which is outside your commander's "
                    f"colors ({_color_words(allowed)}). Commander decks can only use cards within the commander's color identity."
                )

    limit = _copy_limit(deck, card)
    if limit is not None:
        used = sum(o.quantity for o in others if o.card.name == card.name)
        room = limit - used
        if room <= 0:
            raise RuleError(f"This deck already has {used} {card.name}. {_limit_text(deck, card, limit)}")
        if qty > room:
            qty, note = room, f" {_limit_text(deck, card, limit)}"
    return qty, note


def _library_status(deck, card, name_counts, commander_colors, has_commander):
    """For the library panel: (reason the card can't be added or None, most copies the rules allow or None)."""
    status = (card.legalities or {}).get(deck.format)
    if deck.format != "casual" and status in ("banned", "not_legal"):
        return ("Banned in " if status == "banned" else "Not legal in ") + deck.get_format_display(), 0
    if deck.format == "commander" and has_commander and set(card.color_identity) - set(commander_colors):
        return "Outside your commander's colors", 0
    limit = _copy_limit(deck, card)
    if limit is None:
        return None, None
    room = limit - name_counts.get(card.name, 0)
    if room <= 0:
        return ("In deck · one copy allowed" if limit == 1 else f"{limit} of {limit} allowed in deck"), 0
    return None, room


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
            "limit": _copy_limit(deck, c),
            "game_changer": c.game_changer,
        })
    return rows


def _bracket(deck, entries, stats=None):
    """Bracket estimate for Commander decks (None for other formats).

    If the deck plays above its intended bracket, the format checklist gets a warning too."""
    if deck.format != "commander":
        return None
    result = brackets.estimate(entries, intended=deck.target_bracket, spellbook=deck.spellbook_result)
    if stats is not None and result["plays_above"]:
        stats["checks"].append({
            "ok": False,
            "label": f"Fits Bracket {result['intended']} ({brackets.BRACKET_NAMES[result['intended']]})",
            "detail": f"Plays like Bracket {result['computed']}: " + "; ".join(result["reasons"]) + ".",
        })
        stats["issues"] += 1
    return result


def deck_list(request):
    owned = _owned_counts()
    summaries = []
    for deck in Deck.objects.prefetch_related("entries__card"):
        entries = _deck_entries(deck, owned)
        stats = deckrules.analyze(deck.format, entries)
        summaries.append({"deck": deck, "stats": stats, "bracket": _bracket(deck, entries, stats)})
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
    bracket = _bracket(deck, entries, stats)

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
    name_counts = {}
    for e in entries:
        name_counts[e["name"]] = name_counts.get(e["name"], 0) + e["quantity"]
    results = []
    for card in library.order_by("name", "set_code")[:60]:
        have = owned.get(card.id, 0)
        used = in_deck.get(card.id, 0)
        free = max(0, have - used)
        blocked, room = _library_status(deck, card, name_counts, commander_colors, has_commander)
        most = room if card.is_basic_land else (free if room is None else min(free, room))
        results.append({
            "card": card, "owned": have, "used": used, "free": free,
            "blocked": blocked, "max_add": most, "limit": _copy_limit(deck, card),
        })

    boards = _boards(deck)
    return render(request, "collection/deck.html", {
        "active_tab": "decks", "deck": deck, "stats": stats, "boards": boards,
        "default_board": "commander" if deck.format == "commander" and not has_commander else "main",
        "formats": Deck.FORMAT_CHOICES, "q": q, "search": found, "results": results,
        "matches": matches, "fit": fit, "can_fit": deck.format == "commander" and has_commander,
        "commander_colors": commander_colors, "basic_lands": BASIC_LANDS,
        "chart_data": {"curve": stats["curve"], "symbols": stats["symbols"]},
        "bracket": bracket, "bracket_choices": Deck.BRACKET_CHOICES,
        "bracket_names": brackets.BRACKET_NAMES,
        "spellbook_tag_name": brackets.SPELLBOOK_TAG_NAMES.get((deck.spellbook_result or {}).get("bracketTag"), ""),
        "spellbook_bracket": brackets.SPELLBOOK_TAGS.get((deck.spellbook_result or {}).get("bracketTag")),
    })


@require_POST
def deck_bracket(request, pk):
    """Save the bracket the player means this deck for."""
    deck = get_object_or_404(Deck, pk=pk)
    value = request.POST.get("target_bracket", "")
    deck.target_bracket = int(value) if value in {"1", "2", "3", "4", "5"} else None
    deck.save(update_fields=["target_bracket", "updated_at"])
    if deck.target_bracket:
        messages.success(request, f"{deck.name} is meant for Bracket {deck.target_bracket} "
                                  f"({brackets.BRACKET_NAMES[deck.target_bracket]}).")
    else:
        messages.success(request, f"Cleared the intended bracket for {deck.name}.")
    return redirect(reverse("collection:deck", args=[deck.pk]) + "#bracket")


@require_POST
def deck_spellbook(request, pk):
    """Ask Commander Spellbook for its bracket estimate and the combos in this deck, and save it."""
    deck = get_object_or_404(Deck.objects.prefetch_related("entries__card"), pk=pk)
    commanders = [e.card.name for e in deck.entries.all() if e.board == "commander"]
    main = [(e.card.name, e.quantity) for e in deck.entries.all() if e.board == "main"]
    if not commanders and not main:
        messages.error(request, "Add cards to the deck first.")
        return redirect("collection:deck", pk=deck.pk)
    try:
        result = spellbook.estimate(commanders, main)
    except spellbook.SpellbookError as exc:
        messages.error(request, f"Combos weren't checked. {exc}")
    else:
        deck.spellbook_result = result
        deck.spellbook_checked_at = timezone.now()
        deck.save(update_fields=["spellbook_result", "spellbook_checked_at", "updated_at"])
        found = len(result["combos"])
        messages.success(request, f"Commander Spellbook found {found} combo{'s' if found != 1 else ''} in this deck.")
    return redirect(reverse("collection:deck", args=[deck.pk]) + "#bracket")


def deck_playtest(request, pk):
    """Goldfish the deck: shuffle, draw, mulligan and play turns in the browser."""
    deck = get_object_or_404(Deck.objects.prefetch_related("entries__card"), pk=pk)
    cards, commanders = [], []
    for entry in deck.entries.all():
        if entry.board == "side":
            continue
        c = entry.card
        data = {
            "id": c.id, "name": c.name, "mana_cost": c.mana_cost, "cmc": c.cmc,
            "type_line": c.type_line, "oracle_text": c.oracle_text, "image_url": c.image_url,
            "power": c.power, "toughness": c.toughness, "loyalty": c.loyalty,
        }
        if entry.board == "commander":
            commanders.append(dict(data, commander=True))
        else:
            cards.extend(dict(data) for _ in range(entry.quantity))
    commander_format = deck.format == "commander"
    return render(request, "collection/playtest.html", {
        "active_tab": "decks", "deck": deck, "card_count": len(cards),
        "playtest": {
            "deckName": deck.name, "cards": cards, "commanders": commanders,
            "life": 40 if commander_format else 20, "freeMulligan": commander_format,
        },
    })


@require_POST
def deck_add(request, pk):
    deck = get_object_or_404(Deck, pk=pk)
    card = get_object_or_404(Card, pk=request.POST.get("card_id"))
    board = _board_from(deck, request.POST.get("board"))
    qty = max(1, _positive_int(request.POST.get("quantity"), 1))
    back = reverse("collection:deck", args=[deck.pk])

    try:
        qty, note = check_deck_rules(deck, card, board, qty)
    except RuleError as exc:
        messages.error(request, str(exc))
        return _back(request, back)

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

    try:
        qty, note = check_deck_rules(deck, card, board, qty, entry=entry)
    except RuleError as exc:
        messages.error(request, str(exc))
        return _back(request, back)

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
            target.quantity += qty  # check_deck_rules already counted target's copies
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
            check_deck_rules(deck, card, "main", n)  # e.g. Islands in a mono-black Commander deck
        except RuleError as exc:
            messages.error(request, str(exc))
            continue
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


# ------------------------------------------------------------------ Rules and rulings

def _search_words(q):
    """Words and "quoted phrases" from a search box."""
    return [a or b for a, b in re.findall(r'"([^"]+)"|(\S+)', q) if (a or b).strip()]


def rules_page(request):
    """Comprehensive Rules: contents, plus search across rules and the glossary."""
    q = request.GET.get("q", "").strip()
    edition = RulesEdition.objects.first()
    m = rules.NUMBER_QUERY_RE.match(q) if q else None
    if m and edition and Rule.objects.filter(number=m.group(1).lower()).exists():
        return redirect("collection:rule", number=m.group(1).lower())

    words = _search_words(q)
    hits, terms, total = [], [], 0
    if words and edition:
        match = Q()
        for w in words:
            match &= Q(text__icontains=w) | Q(examples__icontains=w)
        found = Rule.objects.filter(match).exclude(kind="chapter")
        total = found.count()
        titles = dict(Rule.objects.filter(kind="section").values_list("number", "text"))
        for rule in found[:80]:
            hits.append({"rule": rule, "section_title": titles.get(rule.section, "")})
        term_match = Q()
        for w in words:
            term_match &= Q(term__icontains=w) | Q(definition__icontains=w)
        named = Q(term__iexact=q)
        for w in words:
            named |= Q(term__iexact=w)
        exact = list(GlossaryTerm.objects.filter(named))   # a term you typed by name always shows
        terms = exact + [t for t in GlossaryTerm.objects.filter(term_match)[:12] if t not in exact]

    chapters = []
    if edition and not q:
        sections = {}
        for s in Rule.objects.filter(kind="section"):
            sections.setdefault(s.number[0], []).append(s)
        chapters = [{"chapter": c, "sections": sections.get(c.number, [])} for c in Rule.objects.filter(kind="chapter")]

    return render(request, "collection/rules.html", {
        "active_tab": "rules", "q": q, "edition": edition, "hits": hits, "total": total,
        "terms": terms, "words": words, "chapters": chapters,
    })


def rule_detail(request, number):
    number = number.lower().rstrip(".")
    rule = Rule.objects.filter(number=number).first()
    if rule is None:
        messages.error(request, f"There's no rule {number} in the loaded rules. Try searching instead.")
        return redirect(reverse("collection:rules") + f"?q={number}")
    if rule.kind == "subrule":
        return redirect(reverse("collection:rule", args=[rule.parent]) + f"#r{rule.number}")

    chapter = Rule.objects.filter(kind="chapter", number=rule.number[0]).first()
    section = Rule.objects.filter(kind="section", number=rule.section).first() if rule.kind != "chapter" else None
    if rule.kind == "chapter":
        children = list(Rule.objects.filter(kind="section", parent=rule.number))
    elif rule.kind == "section":
        children = list(Rule.objects.filter(section=rule.number, kind__in=["rule", "subrule"]))
    else:
        children = [rule] + list(Rule.objects.filter(parent=rule.number, kind="subrule"))

    siblings = list(Rule.objects.filter(kind=rule.kind, parent=rule.parent).values_list("number", flat=True)) \
        if rule.kind in ("section", "rule") else []
    i = siblings.index(rule.number) if rule.number in siblings else -1
    return render(request, "collection/rule.html", {
        "active_tab": "rules", "rule": rule, "chapter": chapter, "section": section, "children": children,
        "edition": RulesEdition.objects.first(),
        "previous": siblings[i - 1] if i > 0 else None,
        "next": siblings[i + 1] if 0 <= i < len(siblings) - 1 else None,
    })


def glossary(request):
    terms = list(GlossaryTerm.objects.all())
    letters = sorted({t.term[0].upper() for t in terms if t.term and t.term[0].isalpha()})
    return render(request, "collection/glossary.html", {
        "active_tab": "rules", "terms": terms, "letters": letters, "edition": RulesEdition.objects.first(),
    })


@require_POST
def rules_load(request):
    try:
        edition = rules.load_latest()
    except rules.RulesError as exc:
        messages.error(request, f"The rules weren't loaded. {exc}")
    else:
        messages.success(request, f"Loaded the Comprehensive Rules effective {edition.effective}: "
                                  f"{edition.rule_count:,} rules and {edition.term_count} glossary terms.")
    return redirect("collection:rules")


def card_detail(request, pk):
    """One card: its text, rulings, the rules for its keywords, and where you use it."""
    card = get_object_or_404(Card, pk=pk)
    rulings_error = None
    try:
        services.load_rulings(card, force=request.GET.get("refresh") == "1")
    except scryfall.ScryfallError as exc:
        rulings_error = str(exc)

    keyword_rules = []
    if card.keywords and RulesEdition.objects.exists():
        index = rules.keyword_index()
        for kw in [k.strip() for k in card.keywords.split(",") if k.strip()]:
            number = index.get(kw.lower())
            if number:
                parts = list(Rule.objects.filter(parent=number, kind="subrule")[:2])
                keyword_rules.append({"keyword": kw, "number": number, "parts": parts})

    owned = CollectionItem.objects.filter(card__name=card.name).aggregate(n=Sum("quantity"))["n"] or 0
    decks = sorted({e.deck for e in DeckCard.objects.filter(card__name=card.name).select_related("deck")},
                   key=lambda d: d.name)
    legal = sorted(((fmt, status) for fmt, status in (card.legalities or {}).items()
                    if fmt in dict(Deck.FORMAT_CHOICES)), key=lambda kv: list(dict(Deck.FORMAT_CHOICES)).index(kv[0]))
    return render(request, "collection/card.html", {
        "active_tab": "cards", "card": card, "rulings": list(card.rulings.all()),
        "rulings_error": rulings_error, "keyword_rules": keyword_rules, "owned": owned, "decks": decks,
        "legal": legal, "rules_loaded": RulesEdition.objects.exists(),
    })
