from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import scryfall, services
from .models import CollectionItem, CollectionSnapshot, PriceSnapshot
from .stats import build_dashboard


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
    "added": "-added_at",
}


def cards(request):
    q = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "value")
    items = CollectionItem.objects.select_related("card")
    if q:
        items = items.filter(card__name__icontains=q)
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
        rows.append({"item": item, "card": item.card, "unit_price": item.unit_price,
                     "total": item.total_value, "gain": gain})
    total = sum((r["total"] for r in rows), Decimal("0"))
    return render(request, "collection/cards.html", {
        "active_tab": "cards", "rows": rows, "q": q, "sort": sort, "total": total,
        "copies": sum(r["item"].quantity for r in rows),
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
