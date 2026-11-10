import json
import re
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Sum
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import decks as deckrules
from . import archidekt, backup, brackets, edhrec, importer, suggest, versions, ocr, precons, rules, scryfall, search, services, spellbook, trash
from .models import (Card, CollectionItem, CollectionSnapshot, Deck, DeckCard, DeckVersion, DeletedDeck, GlossaryTerm, LinkedProfile, Precon, PriceSnapshot, WantItem,
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


def _owned_by_name():
    """card name -> copies you own of any printing, any finish."""
    return dict(CollectionItem.objects.values("card__name").annotate(n=Sum("quantity"))
                .values_list("card__name", "n"))


def _deck_entries(deck, owned=None, by_name=None):
    if by_name is None:
        by_name = _owned_by_name()
    rows = []
    for entry in deck.entries.all():
        c = entry.card
        rows.append({
            "id": entry.pk, "card_id": c.id, "name": c.name, "set_code": c.set_code,
            "collector_number": c.collector_number, "board": entry.board, "quantity": entry.quantity,
            "type_line": c.type_line, "mana_cost": c.mana_cost, "cmc": c.cmc, "colors": c.colors,
            "color_identity": c.color_identity, "oracle_text": c.oracle_text,
            "legalities": c.legalities or {}, "details_loaded": c.details_loaded,
            "price": _f(c.best_price), "owned": by_name.get(c.name, 0), "is_basic": c.is_basic_land,
            "scryfall_uri": c.scryfall_uri, "image_url": c.image_url,
            "limit": _copy_limit(deck, c),
            "game_changer": c.game_changer, "proxies": min(entry.proxies, entry.quantity),
            "power": c.power, "toughness": c.toughness, "loyalty": c.loyalty,
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
    by_name = _owned_by_name()
    summaries = []
    for deck in Deck.objects.prefetch_related("entries__card"):
        entries = _deck_entries(deck, owned, by_name)
        stats = deckrules.analyze(deck.format, entries)
        summaries.append({"deck": deck, "stats": stats, "bracket": _bracket(deck, entries, stats),
                          "cover": _deck_cover(deck, entries)})
    return render(request, "collection/decks.html", {
        "active_tab": "decks", "summaries": summaries, "formats": Deck.FORMAT_CHOICES,
        "library_size": len(owned), "deleted": DeletedDeck.objects.all(), "keep_days": trash.KEEP_DAYS,
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
    cards = request.POST.get("cards", "").strip()
    if cards:
        # A pasted list: check it now and show the preview for this new deck.
        return _deck_import_preview(request, deck, cards)
    messages.success(request, f"Created {deck.name}. Add cards from your library on the right.")
    return redirect("collection:deck", pk=deck.pk)


DECK_VIEWS = [("stacks", "Stacks"), ("text", "Text"), ("table", "Table")]


def _deck_cover(deck, entries):
    """The card shown as the deck's cover: the one you picked, else the commander,
    else the most valuable nonland card. Returns an entry-like dict or None."""
    by_id = {e["card_id"]: e for e in entries}
    if deck.cover_card_id and deck.cover_card_id in by_id:
        return by_id[deck.cover_card_id]
    if deck.cover_card_id:  # picked a card that's no longer in the deck: still show it
        c = deck.cover_card
        return {"card_id": c.id, "name": c.name, "image_url": c.image_url}
    commanders = [e for e in entries if e["board"] == "commander" and e.get("image_url")]
    if commanders:
        return commanders[0]
    pool = [e for e in entries if e.get("image_url") and "Land" not in (e["type_line"] or "")]
    return max(pool, key=lambda e: (e["price"] or 0), default=None)


def deck_detail(request, pk):
    deck = get_object_or_404(Deck.objects.select_related("cover_card").prefetch_related("entries__card"), pk=pk)
    owned = _owned_counts()
    entries = _deck_entries(deck, owned, _owned_by_name())
    stats = deckrules.analyze(deck.format, entries)
    bracket = _bracket(deck, entries, stats)
    view = request.GET.get("view")
    if view in dict(DECK_VIEWS):
        request.session["deck_view"] = view
    else:
        view = request.session.get("deck_view", "stacks")
    versions.ensure_baseline(deck)
    deck_versions = list(deck.versions.all()[:versions.KEEP])
    show_changes = request.GET.get("changes") == "1"
    since, changes = None, None
    if show_changes:
        wanted = request.GET.get("since", "")
        since = next((v for v in deck_versions if str(v.pk) == wanted), None) or versions.previous_version(deck)
        if since:
            changes = versions.diff(entries, since)
            for e in entries:
                e["change"] = changes["by_entry"].get(e["id"])

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

    # "All cards" mode: search Scryfall for cards you don't own yet, to add to your collection.
    source = "all" if request.GET.get("src") == "all" else "lib"
    all_results, all_error = [], None
    if source == "all" and len(q) >= 2:
        all_results, all_error = _search_all_cards(q, commander_colors if fit else "")

    boards = _boards(deck)
    # Where the library's Add buttons come back to: this page (without the live-search
    # flag), and the browser restores the scroll position.
    params = request.GET.copy()
    params.pop("partial", None)
    library_back = request.path + (f"?{params.urlencode()}" if params else "")
    template = "collection/_library_results.html" if request.GET.get("partial") == "library" else "collection/deck.html"
    return render(request, template, {
        "library_back": library_back, "source": source, "all_results": all_results, "all_error": all_error,
        "active_tab": "decks", "deck": deck, "stats": stats, "boards": boards,
        "default_board": "commander" if deck.format == "commander" and not has_commander else "main",
        "formats": Deck.FORMAT_CHOICES, "q": q, "search": found, "results": results,
        "matches": matches, "fit": fit, "can_fit": deck.format == "commander" and has_commander,
        "commander_colors": commander_colors, "basic_lands": BASIC_LANDS,
        "chart_data": {"curve": stats["curve"], "symbols": stats["symbols"]},
        "bracket": bracket, "bracket_choices": Deck.BRACKET_CHOICES,
        "bracket_names": brackets.BRACKET_NAMES,
        "buy_list": deckrules.buy_list_text(stats["needs"]),
        "view": view, "deck_views": DECK_VIEWS, "cover": _deck_cover(deck, entries),
        "deck_versions": deck_versions, "show_changes": show_changes, "since": since, "changes": changes,
        "cover_choices": sorted({(e["card_id"], e["name"]) for e in entries if e.get("image_url")}, key=lambda x: x[1]),
        "spellbook_tag_name": brackets.SPELLBOOK_TAG_NAMES.get((deck.spellbook_result or {}).get("bracketTag"), ""),
        "spellbook_bracket": brackets.SPELLBOOK_TAGS.get((deck.spellbook_result or {}).get("bracketTag")),
    })


FINISH_PRICE = {"nonfoil": "usd", "foil": "usd_foil", "etched": "usd_etched"}


def _search_all_cards(q, identity="", limit=24):
    """Scryfall search for the deck page's "All cards" mode. An exact name lists its printings;
    anything else lists one printing per card. Returns (results, error)."""
    fit = f" id<={identity}" if identity else ""
    try:
        results = scryfall.search(f'!"{q}"{fit}', limit=limit)
        if not results:
            results = scryfall.search(f"{q}{fit}", unique="cards", limit=limit)
    except scryfall.ScryfallError as exc:
        return [], str(exc)
    held = {}
    for sid, finish, qty in CollectionItem.objects.values_list("card__scryfall_id", "finish", "quantity"):
        held[sid] = held.get(sid, 0) + qty
    names = {}
    for name, qty in CollectionItem.objects.values_list("card__name", "quantity"):
        names[name] = names.get(name, 0) + qty
    for r in results:
        r["owned_printing"] = held.get(r["scryfall_id"], 0)
        r["owned_any"] = names.get(r["name"], 0)
        r["finish_options"] = [
            {"value": f, "label": dict(CollectionItem.FINISH_CHOICES)[f], "price": r.get(FINISH_PRICE[f])}
            for f in r.get("finishes", []) if f in dict(CollectionItem.FINISH_CHOICES)
        ] or [{"value": "nonfoil", "label": "Non-foil", "price": r.get("usd")}]
    return results, None


@require_POST
def deck_library_collect(request, pk):
    """From the deck page's library panel: add a card to your collection, and to the deck if asked."""
    deck = get_object_or_404(Deck, pk=pk)
    back = reverse("collection:deck", args=[deck.pk]) + "#library"
    finish = request.POST.get("finish", "nonfoil")
    finish = finish if finish in dict(CollectionItem.FINISH_CHOICES) else "nonfoil"
    qty = max(1, min(999, _positive_int(request.POST.get("quantity"), 1)))
    try:
        item, _created = services.add_to_collection(request.POST.get("scryfall_id", ""), finish, qty)
    except scryfall.ScryfallError as exc:
        messages.error(request, f"Couldn't add that card. {exc}")
        return _back(request, back)
    card = item.card
    filled = services.fill_wants(card, qty, finish)
    total = _sum_quantity(CollectionItem.objects.filter(card__name=card.name))
    msg = f"Added {qty} {card.name} ({card.set_code.upper()}, {item.get_finish_display().lower()}) to your collection. You own {total} now."
    if filled:
        msg += f" Took {filled} off your want list."
    if request.POST.get("also_deck") == "1":
        board = _board_from(deck, request.POST.get("board"))
        try:
            add, note = check_deck_rules(deck, card, board, qty)
        except RuleError as exc:
            messages.success(request, msg)
            messages.error(request, f"Didn't add it to {deck.name}: {exc}")
            return _back(request, back)
        versions.checkpoint(deck)
        entry, created = DeckCard.objects.get_or_create(deck=deck, card=card, board=board, defaults={"quantity": add})
        if not created:
            entry.quantity += add
            entry.save(update_fields=["quantity"])
        deck.save(update_fields=["updated_at"])
        msg += f" Added {add} to {entry.get_board_display().lower()}.{note}"
    messages.success(request, msg)
    return _back(request, back)


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
    return _back(request, reverse("collection:deck", args=[deck.pk]) + "#bracket")


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
            "deckName": deck.name, "format": deck.format, "cards": cards, "commanders": commanders,
            "life": 40 if commander_format else 20, "freeMulligan": commander_format,
        },
    })


@require_POST
def deck_add(request, pk):
    deck = get_object_or_404(Deck, pk=pk)
    card = get_object_or_404(Card, pk=request.POST.get("card_id"))
    versions.checkpoint(deck)
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
    versions.checkpoint(deck)
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

    # Owning the card isn't required: copies you don't have show up under "Cards you need"
    # (or as proxies). Format rules (copy limits, legality) still apply above.

    with transaction.atomic():
        target = DeckCard.objects.filter(deck=deck, card=card, board=board).exclude(pk=entry.pk).first()
        if target:  # moving onto a section that already has this card: merge them
            target.quantity += qty  # check_deck_rules already counted target's copies
            target.save()
            entry.delete()
        else:
            entry.quantity, entry.board = qty, board
            if "proxies" in request.POST:
                entry.proxies = min(_positive_int(request.POST.get("proxies"), entry.proxies), qty)
            elif "proxy" in request.POST or request.POST.get("proxy_form"):
                entry.proxies = qty if request.POST.get("proxy") else 0
            entry.proxies = min(entry.proxies, qty)
            entry.save()
        deck.save(update_fields=["updated_at"])
    messages.success(request, f"Updated {card.name}.{note}")
    return _back(request, back)


@require_POST
def deck_add_basics(request, pk):
    deck = get_object_or_404(Deck, pk=pk)
    versions.checkpoint(deck)
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
    gone = trash.delete_deck(deck)
    messages.success(request, f"Deleted {gone.name}. Your cards are still in your library, and you can put the "
                              f"deck back from Recently deleted for {trash.KEEP_DAYS} days.")
    return redirect(reverse("collection:decks") + "#recently-deleted")


@require_POST
def deck_delete_many(request):
    """Delete the decks ticked on the Decks page."""
    ids = [i for i in request.POST.getlist("deck") if i.isdigit()]
    decks = list(Deck.objects.filter(pk__in=ids))
    if not decks:
        messages.error(request, "Tick at least one deck to delete.")
        return redirect("collection:decks")
    if request.POST.get("confirm") != "yes":
        messages.error(request, "Confirm the delete first.")
        return redirect("collection:decks")
    names = [trash.delete_deck(d).name for d in decks]
    shown = ", ".join(names[:5]) + (f" and {len(names) - 5} more" if len(names) > 5 else "")
    messages.success(request, f"Deleted {len(names)} deck{'s' if len(names) != 1 else ''}: {shown}. "
                              f"You can put them back from Recently deleted for {trash.KEEP_DAYS} days.")
    return redirect(reverse("collection:decks") + "#recently-deleted")


@require_POST
def deck_undelete(request, pk):
    gone = get_object_or_404(DeletedDeck, pk=pk)
    deck, skipped = trash.restore(gone)
    msg = f"Put {deck.name} back."
    if skipped:
        msg += f" {skipped} card(s) are no longer in the app's card list and were skipped."
    messages.success(request, msg)
    return redirect("collection:deck", pk=deck.pk)


@require_POST
def deck_purge(request, pk):
    """Delete a deck from Recently deleted for good."""
    gone = get_object_or_404(DeletedDeck, pk=pk)
    if request.POST.get("confirm") != "yes":
        messages.error(request, "Confirm first: this can't be undone.")
        return redirect(reverse("collection:decks") + "#recently-deleted")
    name = gone.name
    gone.delete()
    messages.success(request, f"{name} is gone for good.")
    return redirect(reverse("collection:decks") + "#recently-deleted")


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
    wanted = WantItem.objects.filter(card__name=card.name).aggregate(n=Sum("quantity"))["n"] or 0
    decks = sorted({e.deck for e in DeckCard.objects.filter(card__name=card.name).select_related("deck")},
                   key=lambda d: d.name)
    legal = sorted(((fmt, status) for fmt, status in (card.legalities or {}).items()
                    if fmt in dict(Deck.FORMAT_CHOICES)), key=lambda kv: list(dict(Deck.FORMAT_CHOICES)).index(kv[0]))
    return render(request, "collection/card.html", {
        "active_tab": "cards", "card": card, "rulings": list(card.rulings.all()),
        "rulings_error": rulings_error, "keyword_rules": keyword_rules, "owned": owned, "decks": decks,
        "wanted": wanted,
        "legal": legal, "rules_loaded": RulesEdition.objects.exists(),
    })



# ------------------------------------------------------------------ Deck list import, needs, precons

def _deck_import_preview(request, deck, text, error=None):
    result = None
    if text.strip() and not error:
        try:
            result = importer.resolve(text, scryfall.lookup)
        except scryfall.ScryfallError as exc:
            error = f"Couldn't check the list. {exc}"
    counts = result.board_counts() if result else {}
    return render(request, "collection/deck_import.html", {
        "active_tab": "decks", "deck": deck, "text": text, "result": result, "error": error,
        "items_json": json.dumps(result.deck_items()) if result else "[]",
        "board_counts": [(label, counts.get(key, 0)) for key, label in DeckCard.BOARD_CHOICES if counts.get(key)],
        "deck_has_cards": deck.entries.exists(),
    })


def deck_import(request, pk):
    """Paste a deck list (Arena, Moxfield, Archidekt...) into this deck: check, then add."""
    deck = get_object_or_404(Deck, pk=pk)
    if request.method != "POST":
        return _deck_import_preview(request, deck, "")
    if request.POST.get("action") == "add":
        try:
            items = json.loads(request.POST.get("items", "[]"))
            assert isinstance(items, list) and all({"id", "board", "quantity"} <= set(i) for i in items)
        except (ValueError, AssertionError):
            messages.error(request, "That import couldn't be read. Check the list again.")
            return redirect("collection:deck_import", pk=deck.pk)
        try:
            versions.checkpoint(deck, "Before importing a list" if deck.entries.exists() else "")
            summary = services.import_to_deck(deck, items, replace=request.POST.get("replace") == "1",
                                              add_to_collection=request.POST.get("collect") == "1")
        except scryfall.ScryfallError as exc:
            return _deck_import_preview(request, deck, request.POST.get("cards", ""), f"Nothing was added. {exc}")
        msg = f"Added {summary['copies']} cards to {deck.name}."
        if request.POST.get("collect") == "1":
            msg += " They're in your collection too."
        if summary["missing"]:
            msg += f" Scryfall couldn't find {len(summary['missing'])} of them this time."
        messages.success(request, msg)
        return redirect("collection:deck", pk=deck.pk)
    text = request.POST.get("cards", "")
    if not text.strip():
        return _deck_import_preview(request, deck, "", "Paste at least one card, one per line.")
    return _deck_import_preview(request, deck, text)


@require_POST
def deck_collect_needed(request, pk):
    """After buying the missing cards: add them to the collection (the deck's printings, non-foil)."""
    deck = get_object_or_404(Deck.objects.prefetch_related("entries__card"), pk=pk)
    if request.POST.get("confirm") != "yes":
        messages.error(request, "Tick the box to confirm you've got these cards, then press the button again.")
        return redirect(reverse("collection:deck", args=[deck.pk]) + "#needs")
    needs = deckrules.needed_cards(_deck_entries(deck))
    added = 0
    with transaction.atomic():
        for n in needs:
            item, created = CollectionItem.objects.get_or_create(card_id=n["card_id"], finish="nonfoil",
                                                                 defaults={"quantity": n["need"]})
            if not created:
                item.quantity += n["need"]
                item.save()
            added += n["need"]
        services.record_collection_value()
    messages.success(request, f"Added {added} card{'s' if added != 1 else ''} to your collection. {deck.name} is complete.")
    return redirect("collection:deck", pk=deck.pk)


def precon_list(request):
    """Browse official preconstructed decks (MTGJSON) and build one."""
    error = None
    try:
        services_count = precons.refresh_catalog(force=request.GET.get("refresh") == "1")
    except precons.PreconError as exc:
        error = str(exc)
        services_count = Precon.objects.count()
    q = request.GET.get("q", "").strip()
    deck_type = request.GET.get("type", "Commander Deck")
    rows = Precon.objects.all()
    if deck_type:
        rows = rows.filter(deck_type=deck_type)
    for word in q.split():
        rows = rows.filter(Q(name__icontains=word) | Q(code__iexact=word))
    types = list(Precon.objects.values_list("deck_type", flat=True).distinct().order_by("deck_type"))
    total = rows.count()
    return render(request, "collection/precons.html", {
        "active_tab": "decks", "precons": rows[:120], "total": total, "q": q, "deck_type": deck_type,
        "types": [t for t in types if t], "error": error, "catalog_size": services_count,
    })


@require_POST
def precon_build(request):
    """Create a deck from an official precon, with its exact printings."""
    file_name = request.POST.get("file_name", "")
    precon = Precon.objects.filter(file_name=file_name).first()
    if not precon:
        messages.error(request, "That precon isn't in the list. Refresh the precon list and try again.")
        return redirect("collection:precons")
    try:
        data = precons.fetch_deck(file_name)
        items, _ = services.rows_to_items(data["rows"])
        own = request.POST.get("own") == "1"
        deck = Deck.objects.create(
            name=(request.POST.get("name") or precon.name).strip()[:100],
            format=precons.deck_format(precon.deck_type),
            description=f"Official precon: {precon.name} ({precon.code})"
                        + (f", released {precon.release_date:%B %Y}" if precon.release_date else "") + ".",
            source="precon", source_id=file_name, synced_at=timezone.now(),
        )
        summary = services.import_to_deck(deck, items, add_to_collection=own)
    except (precons.PreconError, scryfall.ScryfallError) as exc:
        messages.error(request, f"The deck wasn't built. {exc}")
        return redirect("collection:precons")
    msg = f"Built {deck.name}: {summary['copies']} cards."
    if own:
        msg += " Every card is in your collection now."
    if summary["missing"]:
        msg += f" Scryfall couldn't find {len(summary['missing'])} card(s)."
    messages.success(request, msg)
    return redirect("collection:deck", pk=deck.pk)



# ------------------------------------------------------------------ Backup and restore

def backup_page(request):
    if request.method == "POST":
        upload = request.FILES.get("backup")
        if request.POST.get("confirm") != "yes":
            messages.error(request, "Tick the box to confirm you want to replace what's here, then restore again.")
        elif not upload:
            messages.error(request, "Choose a backup file first.")
        elif upload.size > 200 * 1024 * 1024:
            messages.error(request, "That file is too big to be a Mana Ledger backup.")
        else:
            try:
                done = backup.restore(upload.read())
            except backup.BackupError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"Restored {done['objects']:,} records from {upload.name}.")
                return redirect("collection:backup")
    return render(request, "collection/backup.html", {
        "active_tab": "", "counts": backup.counts(), "database": settings.DATABASE_DESCRIPTION,
        "permanent": not settings.DATABASES["default"]["ENGINE"].endswith("sqlite3"),
    })


def backup_download(request):
    response = HttpResponse(backup.dump(), content_type="application/json")
    response["Content-Disposition"] = f'attachment; filename="{backup.filename()}"'
    return response



# ------------------------------------------------------------------ Archidekt

def archidekt_page(request):
    """Link an Archidekt username, list its public decks, and import them."""
    profile = LinkedProfile.objects.filter(site="archidekt").first()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "link":
            try:
                name = archidekt.clean_username(request.POST.get("username"))
            except archidekt.ArchidektError as exc:
                messages.error(request, str(exc))
            else:
                LinkedProfile.objects.update_or_create(site="archidekt", defaults={"username": name})
                messages.success(request, f"Linked Archidekt account {name}.")
        elif action == "unlink" and profile:
            profile.delete()
            messages.success(request, "Unlinked your Archidekt account. Decks you imported stay here.")
        return redirect("collection:archidekt")

    decks, error = [], None
    if profile:
        try:
            decks = archidekt.user_decks(profile.username)
        except archidekt.ArchidektError as exc:
            error = str(exc)
    imported = {d.source_id: d for d in Deck.objects.filter(source="archidekt")}
    for d in decks:
        d["mine"] = imported.get(str(d["id"]))
    return render(request, "collection/archidekt.html", {
        "active_tab": "decks", "profile": profile, "decks": decks, "error": error,
    })


def _archidekt_import(deck, number, own=False):
    """Copy an Archidekt deck's list into `deck`, replacing what's there."""
    data = archidekt.fetch_deck(number)
    items, missing = services.rows_to_items(data["rows"])
    if not items:
        raise archidekt.ArchidektError("None of that deck's cards could be found on Scryfall.")
    summary = services.import_to_deck(deck, items, replace=True, add_to_collection=own)
    deck.source, deck.source_id, deck.source_url = "archidekt", str(data["id"]), data["url"]
    deck.synced_at = timezone.now()
    deck.save(update_fields=["source", "source_id", "source_url", "synced_at", "updated_at"])
    summary["missing"] = list(summary["missing"]) + missing
    return data, summary


@require_POST
def archidekt_import(request):
    """Import one Archidekt deck (from the list, or a pasted deck link) as a new deck."""
    number = archidekt.deck_id(request.POST.get("deck", ""))
    if not number:
        messages.error(request, "Paste an Archidekt deck link, like archidekt.com/decks/123456.")
        return redirect("collection:archidekt")
    existing = Deck.objects.filter(source="archidekt", source_id=str(number)).first()
    if existing:
        messages.error(request, f"{existing.name} is already imported. Use \"Update from Archidekt\" on it instead.")
        return redirect("collection:deck", pk=existing.pk)
    try:
        with transaction.atomic():
            deck = Deck.objects.create(name=f"Archidekt deck {number}")
            data, summary = _archidekt_import(deck, number, own=request.POST.get("own") == "1")
            deck.name = data["name"][:100]
            deck.format = data["format"]
            deck.description = f"Imported from Archidekt: {data['url']}"
            deck.save(update_fields=["name", "format", "description"])
    except (archidekt.ArchidektError, scryfall.ScryfallError) as exc:
        messages.error(request, f"The deck wasn't imported. {exc}")
        return redirect("collection:archidekt")
    msg = f"Imported {deck.name}: {summary['copies']} cards."
    if summary["missing"]:
        msg += f" {len(summary['missing'])} card(s) couldn't be found on Scryfall."
    messages.success(request, msg)
    return redirect("collection:deck", pk=deck.pk)


@require_POST
def archidekt_sync(request, pk):
    """Replace a deck's list with the current one on Archidekt."""
    deck = get_object_or_404(Deck, pk=pk, source="archidekt")
    if request.POST.get("confirm") != "yes":
        messages.error(request, "Tick the box to confirm replacing this list, then press Update again.")
        return redirect("collection:deck", pk=deck.pk)
    try:
        with transaction.atomic():
            versions.checkpoint(deck, "Before updating from Archidekt")
            _, summary = _archidekt_import(deck, deck.source_id)
    except (archidekt.ArchidektError, scryfall.ScryfallError) as exc:
        messages.error(request, f"Nothing was changed. {exc}")
    else:
        messages.success(request, f"Updated {deck.name} from Archidekt: {summary['copies']} cards.")
    return redirect("collection:deck", pk=deck.pk)



# ------------------------------------------------------------------ Suggestions from your collection

def _collection_cards(exclude_deck=None):
    """One dict per card name you own (any printing): what suggest.py needs."""
    items = CollectionItem.objects.select_related("card").order_by("card__name", "-card__details_loaded", "card__id")
    in_decks = {}
    rows = DeckCard.objects.select_related("deck").values_list("card__name", "deck__name", "deck_id")
    for name, deck_name, deck_id in rows:
        if exclude_deck is None or deck_id != exclude_deck.pk:
            in_decks.setdefault(name, set()).add(deck_name)
    cards = {}
    for item in items:
        c = item.card
        row = cards.get(c.name)
        if row is None:
            cards[c.name] = row = {
                "card_id": c.id, "name": c.name, "type_line": c.type_line, "oracle_text": c.oracle_text,
                "color_identity": c.color_identity, "legalities": c.legalities or {}, "cmc": c.cmc,
                "mana_cost": c.mana_cost, "game_changer": c.game_changer, "is_basic": c.is_basic_land,
                "price": _f(c.best_price), "owned": 0, "in_decks": sorted(in_decks.get(c.name, [])),
                "image_url": c.image_url,
            }
        row["owned"] += item.quantity
    return list(cards.values())


def deck_suggestions(request, pk):
    deck = get_object_or_404(Deck.objects.prefetch_related("entries__card"), pk=pk)
    entries = _deck_entries(deck)
    result = suggest.suggestions(entries, _collection_cards(exclude_deck=deck), deck.format,
                                 intended=deck.target_bracket)
    return render(request, "collection/suggestions.html", {
        "active_tab": "decks", "deck": deck, "s": result, "bracket_choices": Deck.BRACKET_CHOICES,
        "default_board": "main", "edh": _edhrec_block(request, deck, entries),
    })


def _edhrec_block(request, deck, entries):
    """EDHREC's top 20 for the deck's commander, each marked in the deck / in your collection."""
    commanders = sorted({e["name"] for e in entries if e["board"] == "commander"})
    if deck.format != "commander" or not commanders:
        return None
    sort = request.GET.get("edh", "inclusion")
    sort = sort if sort in edhrec.SORTS else "inclusion"
    block = {"commanders": commanders, "sort": sort, "sorts": edhrec.SORTS, "error": None, "cards": [],
             "url": edhrec.PAGE_URL.format(slug=edhrec.slug(commanders))}
    try:
        page = edhrec.page(commanders, refresh=request.GET.get("edh_refresh") == "1")
    except edhrec.EdhrecError as exc:
        block["error"] = str(exc)
        return block
    block.update({"fetched_at": page.fetched_at, "num_decks": page.num_decks})
    in_deck = {}
    for e in entries:
        row = in_deck.setdefault(e["name"], {"quantity": 0, "proxies": 0})
        row["quantity"] += e["quantity"]
        row["proxies"] += e["proxies"]
    owned_by_name = _owned_by_name()
    owned_ids = {}
    for card_id, name in Card.objects.filter(items__isnull=False).values_list("id", "name").order_by("-id"):
        owned_ids.setdefault(name, card_id)
    # Only cards that aren't in the deck yet, and no lands (EDHREC's land lists, or a land we know of).
    known_lands = set(Card.objects.filter(name__in=[c["name"] for c in page.cards], type_line__icontains="land")
                      .exclude(type_line__icontains="//").values_list("name", flat=True))
    others = [c for c in page.cards
              if c["name"] not in in_deck and not c.get("is_land") and c["name"] not in known_lands]
    block["skipped_in_deck"] = sum(1 for c in page.cards if c["name"] in in_deck)
    for c in edhrec.top(others, 20, sort):
        c["in_deck"] = in_deck.get(c["name"])
        c["owned"] = owned_by_name.get(c["name"], 0)
        c["owned_card_id"] = owned_ids.get(c["name"])
        block["cards"].append(c)
    block["owned_count"] = sum(1 for c in block["cards"] if c["owned"])
    return block


def _card_for(scryfall_id, name):
    """Save (or refresh) a card from Scryfall by id, falling back to its name."""
    try:
        data = scryfall.get_card(scryfall_id) if scryfall_id else scryfall.named(name)
    except scryfall.ScryfallError:
        if not name:
            raise
        data = scryfall.named(name)
    return services.save_card(data)


@require_POST
def deck_edhrec_add(request, pk):
    """From the EDHREC list: add a card to your collection, or put it in the deck as a proxy."""
    deck = get_object_or_404(Deck, pk=pk)
    back = reverse("collection:deck_suggestions", args=[deck.pk]) + "#edhrec"
    action = request.POST.get("action")
    name = request.POST.get("name", "").strip()
    try:
        card = _card_for(request.POST.get("scryfall_id", "").strip(), name)
    except scryfall.ScryfallError as exc:
        messages.error(request, f"Couldn't find {name or 'that card'} on Scryfall. {exc}")
        return _back(request, back)
    if action == "collect":
        item, created = CollectionItem.objects.get_or_create(card=card, finish="nonfoil", defaults={"quantity": 1})
        if not created:
            item.quantity += 1
            item.save(update_fields=["quantity"])
        filled = services.fill_wants(card, 1, "nonfoil")
        services.record_collection_value()
        msg = f"Added {card.name} ({card.set_code.upper()}) to your collection."
        if filled:
            msg += " Took it off your want list."
        messages.success(request, msg + " Use Add to deck to put it in this deck.")
    elif action == "proxy":
        try:
            qty, note = check_deck_rules(deck, card, "main", 1)
        except RuleError as exc:
            messages.error(request, str(exc))
            return _back(request, back)
        versions.checkpoint(deck)
        entry, created = DeckCard.objects.get_or_create(deck=deck, card=card, board="main",
                                                        defaults={"quantity": qty, "proxies": qty})
        if not created:
            entry.quantity += qty
            entry.proxies = min(entry.quantity, entry.proxies + qty)
            entry.save(update_fields=["quantity", "proxies"])
        deck.save(update_fields=["updated_at"])
        messages.success(request, f"Added {card.name} to {deck.name} as a proxy.{note}")
    else:
        messages.error(request, "Unknown action.")
    return _back(request, back)


def commander_ideas(request):
    ideas = suggest.commander_ideas(_collection_cards(), limit=24)
    built = set(DeckCard.objects.filter(board="commander").values_list("card__name", flat=True))
    for idea in ideas:
        idea["built"] = idea["card"]["name"] in built
    return render(request, "collection/commander_ideas.html", {
        "active_tab": "decks", "ideas": ideas, "bracket_choices": Deck.BRACKET_CHOICES,
    })


@require_POST
def deck_from_commander(request):
    """Start a Commander deck around a commander you own, then go to its suggestions."""
    card = get_object_or_404(Card, pk=request.POST.get("card_id"))
    if not CollectionItem.objects.filter(card__name=card.name).exists():
        messages.error(request, f"{card.name} isn't in your collection.")
        return redirect("collection:commander_ideas")
    bracket = request.POST.get("target_bracket", "")
    deck = Deck.objects.create(name=card.name.split(" // ")[0][:100], format="commander",
                               target_bracket=int(bracket) if bracket in {"1", "2", "3", "4", "5"} else None)
    DeckCard.objects.create(deck=deck, card=card, board="commander", quantity=1)
    messages.success(request, f"Started {deck.name}. Here's what your collection has for it.")
    return redirect("collection:deck_suggestions", pk=deck.pk)


@require_POST
def deck_cut(request, pk):
    """Remove every copy of a card from a deck (main and sideboard; never the commander)."""
    deck = get_object_or_404(Deck, pk=pk)
    versions.checkpoint(deck)
    name = request.POST.get("name", "")
    removed, _ = DeckCard.objects.filter(deck=deck, card__name=name).exclude(board="commander").delete()
    if removed:
        deck.save(update_fields=["updated_at"])
        messages.success(request, f"Cut {name} from {deck.name}.")
    return _back(request, reverse("collection:deck_suggestions", args=[deck.pk]))



# ------------------------------------------------------------------ Want list

def _want_fields(post):
    finish = post.get("finish", "any")
    if finish not in dict(WantItem.FINISH_CHOICES):
        finish = "any"
    priority = post.get("priority", "2")
    priority = int(priority) if priority in {"1", "2", "3"} else 2
    deck = Deck.objects.filter(pk=post.get("deck") or 0).first()
    return {
        "quantity": max(1, min(99, _positive_int(post.get("quantity"), 1))),
        "finish": finish, "priority": priority, "deck": deck,
        "max_price": _decimal_or_none(post.get("max_price")),
        "note": post.get("note", "").strip(),
    }


def want_list(request):
    deck_filter = request.GET.get("deck", "")
    items = WantItem.objects.select_related("card", "deck")
    if deck_filter == "none":
        items = items.filter(deck__isnull=True)
    elif deck_filter.isdigit():
        items = items.filter(deck_id=int(deck_filter))
    items = list(items)
    owned = _owned_by_name()
    for i in items:
        i.owned = owned.get(i.card.name, 0)
    total = sum((i.total for i in items if i.total is not None), Decimal("0"))
    deals = [i for i in items if i.is_deal]
    return render(request, "collection/wants.html", {
        "active_tab": "wants", "items": items, "deck_filter": deck_filter,
        "decks": Deck.objects.order_by("name"), "wanted_decks": Deck.objects.filter(wants__isnull=False).distinct().order_by("name"),
        "copies": sum(i.quantity for i in items), "total": total, "deals": len(deals),
        "unpriced": sum(1 for i in items if i.unit_price is None),
        "high": sum(i.quantity for i in items if i.priority == 1),
        "buy_list": "\n".join(f"{i.quantity} {i.card.name}" + ("" if i.any_printing else f" ({i.card.set_code.upper()}) {i.card.collector_number}")
                              for i in items),
        "finishes": WantItem.FINISH_CHOICES, "priorities": WantItem.PRIORITY_CHOICES,
        "q": request.GET.get("q", ""),
    })


@require_POST
def want_add(request):
    """Add by card id (card page, deck pages), by printing (Add Cards results), or by name and set."""
    fields = _want_fields(request.POST)
    back = reverse("collection:wants")
    try:
        if request.POST.get("card_id"):
            card = get_object_or_404(Card, pk=request.POST["card_id"])
            any_printing = request.POST.get("any_printing", "1") == "1"
        elif request.POST.get("scryfall_id"):
            card = services.save_card(scryfall.get_card(request.POST["scryfall_id"]))
            any_printing = request.POST.get("any_printing", "0") == "1"
        else:
            name = request.POST.get("name", "").strip()
            set_code = request.POST.get("set", "").strip().lower()
            if not name:
                messages.error(request, "Type a card name.")
                return _back(request, back)
            data = None
            if set_code:
                found, _ = scryfall.lookup([{"name": name, "set": set_code}])
                data = found[0] if found else None
            if data is None:
                found, _ = scryfall.lookup([{"name": name}])
                data = found[0] if found else scryfall.fuzzy(name)
            if data is None:
                messages.error(request, f'Scryfall has no card named "{name}". Check the spelling.')
                return _back(request, back)
            card = services.save_card(data)
            any_printing = not set_code
    except scryfall.ScryfallError as exc:
        messages.error(request, f"Couldn't add that card. {exc}")
        return _back(request, back)
    item, created = services.add_want(card, any_printing=any_printing, **fields)
    where = f" for {item.deck.name}" if item.deck else ""
    if created:
        messages.success(request, f"Added {item.quantity} x {card.name} to your want list{where}.")
    else:
        messages.success(request, f"You now want {item.quantity} x {card.name}{where}.")
    return _back(request, back)


@require_POST
def want_update(request, pk):
    item = get_object_or_404(WantItem, pk=pk)
    qty = _positive_int(request.POST.get("quantity"), item.quantity)
    if qty == 0 or request.POST.get("remove"):
        item.delete()
        messages.success(request, f"Took {item.card.name} off your want list.")
        return _back(request, reverse("collection:wants"))
    fields = _want_fields(request.POST)
    item.quantity = min(qty, 99)
    item.max_price = fields["max_price"]
    item.priority = fields["priority"]
    item.finish = fields["finish"]
    item.save()
    messages.success(request, f"Saved {item.card.name}.")
    return _back(request, reverse("collection:wants"))


@require_POST
def want_got(request, pk):
    """Bought it: move the copies into the collection."""
    item = get_object_or_404(WantItem.objects.select_related("card"), pk=pk)
    qty = _positive_int(request.POST.get("quantity"), item.quantity) or item.quantity
    name = item.card.name
    left = max(0, item.quantity - qty)
    owned = services.got_want(item, qty, request.POST.get("finish"), _decimal_or_none(request.POST.get("price_paid")))
    msg = f"Added {qty} x {name} ({owned.get_finish_display().lower()}) to your collection."
    msg += f" Still want {left}." if left else " It's off your want list."
    messages.success(request, msg)
    return _back(request, reverse("collection:wants"))


@require_POST
def want_import(request):
    """Paste a list (same format as Import) straight onto the want list."""
    text = request.POST.get("cards", "")
    fields = _want_fields(request.POST)
    fields.pop("quantity")
    if not text.strip():
        messages.error(request, "Paste at least one card, one per line.")
        return redirect("collection:wants")
    try:
        result = importer.resolve(text, scryfall.lookup)
    except scryfall.ScryfallError as exc:
        messages.error(request, f"Couldn't check the list. {exc}")
        return redirect("collection:wants")
    added = 0
    for m in result.matches:
        card = services.save_card(m.card)
        services.add_want(card, quantity=m.line.quantity, any_printing=not m.line.set_code,
                          **dict(fields, finish=m.finish if m.finish != "nonfoil" else fields["finish"]))
        added += m.line.quantity
    msg = f"Added {added} card{'s' if added != 1 else ''} to your want list."
    if result.problems:
        msg += " Skipped: " + "; ".join(f"line {p.number}: {p.message}" for p in result.problems[:5])
        if len(result.problems) > 5:
            msg += f" (and {len(result.problems) - 5} more)"
    (messages.warning if result.problems else messages.success)(request, msg)
    return redirect("collection:wants")


@require_POST
def deck_want_needed(request, pk):
    """Put every card a deck still needs on the want list, for that deck."""
    deck = get_object_or_404(Deck.objects.prefetch_related("entries__card"), pk=pk)
    needs = deckrules.needed_cards(_deck_entries(deck))
    added = 0
    for n in needs:
        card = Card.objects.get(pk=n["card_id"])
        current = WantItem.objects.filter(deck=deck, card__name=card.name).first()
        if current:
            if current.quantity < n["need"]:
                current.quantity = n["need"]
                current.save()
            continue
        services.add_want(card, quantity=n["need"], deck=deck)
        added += n["need"]
    messages.success(request, f"Added {added} card{'s' if added != 1 else ''} from {deck.name} to your want list."
                     if added else f"Everything {deck.name} needs is already on your want list.")
    return redirect(reverse("collection:wants") + f"?deck={deck.pk}")



# ------------------------------------------------------------------ Proxies

@require_POST
def deck_proxy(request, pk):
    """Mark the copies a deck still needs as proxies: one card (name=...) or all of them."""
    deck = get_object_or_404(Deck.objects.prefetch_related("entries__card"), pk=pk)
    only = request.POST.get("name")
    needs = [n for n in deckrules.needed_cards(_deck_entries(deck)) if not only or n["name"] == only]
    marked = 0
    with transaction.atomic():
        for n in needs:
            left = n["need"]
            entries = sorted((e for e in deck.entries.all() if e.card.name == n["name"]),
                             key=lambda e: {"commander": 0, "main": 1, "side": 2}.get(e.board, 3))
            for entry in entries:
                room = entry.quantity - entry.proxies
                take = min(room, left)
                if take > 0:
                    entry.proxies += take
                    entry.save(update_fields=["proxies"])
                    left -= take
                    marked += take
        deck.save(update_fields=["updated_at"])
    messages.success(request, f"Marked {marked} cop{'y' if marked == 1 else 'ies'} as proxies."
                     if marked else "Nothing to mark: you own or proxy every card.")
    return _back(request, reverse("collection:deck", args=[deck.pk]) + "#needs")


def deck_proxies(request, pk):
    """Printable proxy sheet: the deck's proxies (or every card), at real card size."""
    deck = get_object_or_404(Deck.objects.prefetch_related("entries__card"), pk=pk)
    everything = request.GET.get("all") == "1"
    basics = request.GET.get("basics") == "1"
    style = "text" if request.GET.get("style") == "text" else "image"
    label = request.GET.get("label", "1") == "1"
    cards = []
    order = {"commander": 0, "main": 1, "side": 2}
    for e in sorted(deck.entries.all(), key=lambda e: (order.get(e.board, 3), e.card.name)):
        c = e.card
        if everything:
            if c.is_basic_land and not basics:
                continue
            copies = e.quantity
        else:
            copies = min(e.proxies, e.quantity)
        cards.extend([c] * copies)
    pages = [cards[i:i + 9] for i in range(0, len(cards), 9)]
    return render(request, "collection/proxies.html", {
        "active_tab": "decks", "deck": deck, "pages": pages, "count": len(cards), "everything": everything,
        "basics": basics, "style": style, "label": label,
        "proxy_total": sum(min(e.proxies, e.quantity) for e in deck.entries.all()),
    })



@require_POST
def deck_cover(request, pk):
    """Pick the card whose art is the deck's cover (blank: back to the default)."""
    deck = get_object_or_404(Deck, pk=pk)
    card_id = request.POST.get("card_id", "")
    if card_id and deck.entries.filter(card_id=card_id).exists():
        deck.cover_card_id = int(card_id)
        messages.success(request, f"{deck.cover_card.name} is now the cover of {deck.name}.")
    else:
        deck.cover_card = None
        messages.success(request, f"{deck.name} uses its commander (or most valuable card) as the cover.")
    deck.save(update_fields=["cover_card"])
    return _back(request, reverse("collection:deck", args=[deck.pk]))



# ------------------------------------------------------------------ Deck versions

@require_POST
def deck_version_save(request, pk):
    deck = get_object_or_404(Deck, pk=pk)
    label = request.POST.get("label", "").strip() or f"Saved {timezone.localtime():%b %-d, %-I:%M %p}"
    versions.snapshot(deck, label=label)
    messages.success(request, f'Saved this version of {deck.name} as "{label}".')
    return _back(request, reverse("collection:deck", args=[deck.pk]))


@require_POST
def deck_version_restore(request, pk, version_id):
    """Put a whole saved version back (the current list is saved first, so this can be undone)."""
    deck = get_object_or_404(Deck, pk=pk)
    version = get_object_or_404(DeckVersion, pk=version_id, deck=deck)
    if request.POST.get("confirm") != "yes":
        messages.error(request, "Tick the box to confirm replacing the deck with that version.")
        return _back(request, reverse("collection:deck", args=[deck.pk]))
    cards = {c.id: c for c in Card.objects.filter(pk__in=[c["card_id"] for c in version.cards])}
    missing = 0
    with transaction.atomic():
        versions.snapshot(deck, label="Before restoring an older version")
        deck.entries.all().delete()
        for c in version.cards:
            if c["card_id"] not in cards:
                missing += 1
                continue
            DeckCard.objects.create(deck=deck, card=cards[c["card_id"]], board=c["board"],
                                    quantity=c["quantity"], proxies=min(c.get("proxies", 0), c["quantity"]))
        deck.save(update_fields=["updated_at"])
    when = timezone.localtime(version.created_at)
    msg = f"Restored {deck.name} to the version from {when:%b %-d, %-I:%M %p}. The list you had is saved as a version, so you can switch back."
    if missing:
        msg += f" {missing} card(s) are no longer in the app's card list and were skipped."
    messages.success(request, msg)
    return redirect(reverse("collection:deck", args=[deck.pk]) + "#deck-list")


@require_POST
def deck_put_back(request, pk):
    """Put one removed card back into the deck (from "Show changes")."""
    deck = get_object_or_404(Deck, pk=pk)
    card = get_object_or_404(Card, pk=request.POST.get("card_id"))
    board = request.POST.get("board", "main")
    board = board if board in dict(DeckCard.BOARD_CHOICES) else "main"
    qty = max(1, min(99, _positive_int(request.POST.get("quantity"), 1)))
    versions.checkpoint(deck)
    entry, created = DeckCard.objects.get_or_create(deck=deck, card=card, board=board, defaults={"quantity": qty})
    if not created:
        entry.quantity += qty
        entry.save(update_fields=["quantity"])
    deck.save(update_fields=["updated_at"])
    messages.success(request, f"Put {card.name} back in {deck.name}.")
    return _back(request, reverse("collection:deck", args=[deck.pk]))



@require_POST
def deck_trim(request, pk):
    """Remove the cards whose Keep box was unticked in the table view.

    Only cards that were on the page ("shown") can be removed, so a page that didn't
    send its checkboxes can never empty the deck. A version is saved first."""
    deck = get_object_or_404(Deck, pk=pk)
    shown = {int(x) for x in request.POST.getlist("shown") if x.isdigit()}
    keep = {int(x) for x in request.POST.getlist("keep") if x.isdigit()}
    gone = deck.entries.filter(pk__in=shown - keep).select_related("card")
    cards = list(gone)
    if not cards:
        messages.error(request, "Nothing to remove: every card is still ticked Keep.")
        return _back(request, reverse("collection:deck", args=[deck.pk]))
    copies = sum(e.quantity for e in cards)
    with transaction.atomic():
        versions.checkpoint(deck, f"Before removing {copies} card{'s' if copies != 1 else ''}")
        gone.delete()
        deck.save(update_fields=["updated_at"])
    names = ", ".join(e.card.name for e in cards[:5]) + (f" and {len(cards) - 5} more" if len(cards) > 5 else "")
    messages.success(request, f"Removed {copies} card{'s' if copies != 1 else ''} ({names}). "
                              "Changed your mind? Versions → Restore the version saved just before.")
    return _back(request, reverse("collection:deck", args=[deck.pk]) + "?view=table")


# ------------------------------------------------------------------ Life tracker

def _art(url):
    """Scryfall 'normal' image -> its art crop (for the life tracker backgrounds)."""
    return url.replace("/normal/", "/art_crop/", 1) if url and "/normal/" in url else (url or "")


def life_tracker(request):
    """Full-screen life counter for a game, with each player's commander behind their life."""
    decks = []
    for deck in Deck.objects.prefetch_related("entries__card").select_related("cover_card").order_by("name"):
        entries = list(deck.entries.all())
        cmd = [e.card for e in entries if e.board == "commander"]
        if not cmd:
            cover = deck.cover_card or next((e.card for e in entries if e.card.image_url and "Land" not in e.card.type_line), None)
            cmd = [cover] if cover else []
        decks.append({
            "id": deck.pk, "name": deck.name, "format": deck.format,
            "commanders": [{"name": c.name, "art": _art(c.image_url), "image": c.image_url} for c in cmd[:2]],
            "isCommander": any(e.board == "commander" for e in entries),
        })
    start = _positive_int(request.GET.get("deck"), 0)
    return render(request, "collection/life.html", {"decks": decks, "start_deck": start})


def life_commander(request):
    """Art for any card by exact name (the life tracker's commander search)."""
    name = request.GET.get("name", "").strip()
    if not name:
        return JsonResponse({"error": "Type a card name."}, status=400)
    card = Card.objects.filter(name__iexact=name).exclude(image_url="").order_by("-id").first()
    if card is None:
        try:
            data = scryfall.named(name)
        except scryfall.ScryfallError as exc:
            return JsonResponse({"error": str(exc)}, status=404)
        return JsonResponse({"name": data["name"], "art": _art(data.get("image_url") or ""),
                             "image": data.get("image_url") or "", "colors": data.get("color_identity") or ""})
    return JsonResponse({"name": card.name, "art": _art(card.image_url), "image": card.image_url,
                         "colors": card.color_identity or ""})
