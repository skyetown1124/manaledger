"""Card display filters: {{ card.mana_cost|mana }}, {{ text|highlight:terms }}, {{ "WU"|pips }}."""
import re

from django import template
from django.utils.html import escape, format_html_join
from django.utils.safestring import mark_safe

register = template.Library()

SYMBOL_RE = re.compile(r"\{([^}]+)\}")
COLOR_TITLES = {"W": "white", "U": "blue", "B": "black", "R": "red", "G": "green", "C": "colorless"}


def _symbol_class(symbol):
    s = symbol.upper()
    colors = [c for c in "WUBRG" if c in s]
    if "/" in s and len(colors) == 2:
        return f"ms ms-{colors[0]} ms-hybrid-{colors[1]}"
    if colors:
        return f"ms ms-{colors[0]}"
    return "ms ms-generic"


@register.filter
def mana(cost):
    """'{2}{U}{U}' -> small round symbols. Split cards keep their ' // ' divider."""
    if not cost:
        return ""
    parts = []
    for half in str(cost).split(" // "):
        symbols = SYMBOL_RE.findall(half)
        parts.append(format_html_join(
            "", '<span class="{}" title="{}">{}</span>',
            ((_symbol_class(s), s, s.replace("/", "")[:2]) for s in symbols),
        ))
    joined = '<span class="ms-split">//</span>'.join(parts)
    return mark_safe(f'<span class="mana" aria-label="Mana cost {escape(cost)}">{joined}</span>')


@register.filter
def pips(letters):
    """'WU' -> one colored dot per color; '' -> a colorless dot."""
    letters = letters or "C"
    return format_html_join(
        "", '<span class="pip pip-{}" title="{}"></span>',
        ((c, COLOR_TITLES.get(c, c)) for c in letters),
    )


@register.filter
def highlight(text, terms):
    """Escape card text, mark search terms with <mark>, and keep line breaks."""
    safe = escape(text or "")
    words = [t for t in (terms or []) if t]
    if words:
        pattern = re.compile("|".join(re.escape(escape(w)) for w in sorted(words, key=len, reverse=True)), re.I)
        safe = pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", safe)
    return mark_safe(safe.replace("\n", "<br>"))


@register.filter
def get_item(mapping, key):
    return (mapping or {}).get(key)


# "702.19b" anywhere, or "section 702" / "rule 704" (a bare section number, with no decimal after it)
_RULE_REF = re.compile(r"\b(\d{3}\.\d+[a-z]?)\b|\b((?i:rules?|sections?)\s+)(\d{3})(?![.\d]*\d)\b")


@register.filter
def rule_text(text, terms=None):
    """Escape rules text, highlight search terms, and link rule numbers ("See rule 702.19b")."""
    from django.urls import reverse

    safe = escape(text or "")
    words = [t for t in (terms or []) if t]
    if words:
        pattern = re.compile("|".join(re.escape(escape(w)) for w in sorted(words, key=len, reverse=True)), re.I)
        safe = pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", safe)

    def link(m):
        if m.group(1):
            n = m.group(1)
            return f'<a href="{reverse("collection:rule", args=[n])}">{n}</a>'
        return f'{m.group(2)}<a href="{reverse("collection:rule", args=[m.group(3)])}">{m.group(3)}</a>'

    return mark_safe(_RULE_REF.sub(link, safe).replace("\n", "<br>"))
