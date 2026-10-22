"""Formatting filters: {{ value|money }}, {{ change|signed_money }}, {{ share|pct }}."""
from django import template

register = template.Library()


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@register.filter
def money(value):
    """1234.5 -> '$1,234.50'; None -> an em dash."""
    n = _num(value)
    return "—" if n is None else f"${n:,.2f}"


@register.filter
def money_short(value):
    """Whole dollars for big tiles: 1234.5 -> '$1,235'."""
    n = _num(value)
    if n is None:
        return "—"
    return f"${n:,.2f}" if abs(n) < 100 else f"${n:,.0f}"


@register.filter
def signed_money(value):
    n = _num(value)
    if n is None:
        return "—"
    sign = "+" if n > 0 else "−" if n < 0 else ""
    return f"{sign}${abs(n):,.2f}"


@register.filter
def pct(value):
    n = _num(value)
    return "—" if n is None else f"{n:.1f}%"


@register.filter
def signed_pct(value):
    n = _num(value)
    if n is None:
        return "—"
    sign = "+" if n > 0 else "−" if n < 0 else ""
    return f"{sign}{abs(n):.1f}%"


@register.filter
def tone(value):
    """CSS class for a gain or loss."""
    n = _num(value)
    if not n:
        return "flat"
    return "up" if n > 0 else "down"
