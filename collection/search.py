"""Card search with a small Scryfall-style syntax.

    parse('t:creature o:"draw a card" c:u mv<=3')

returns a SearchResult whose `.q` is a Django Q object for Card querysets.
Words without a prefix match the card's name, type line or rules text.

Supported filters (put "-" in front of any of them to exclude matches):
    name:  n:        name contains
    o:  oracle:      rules text contains
    t:  type:        type line contains
    k:  kw:          has a keyword ability (Flying, Ward, ...)
    c:  color:       colors.  c:ur = blue and red (and maybe more),
                              c=ur = exactly blue-red, c<=ur = only blue/red/colorless,
                              c:c = colorless, c:m = multicolor
    id: identity:    color identity fits inside these colors (for Commander)
    mv  cmc          mana value, with = : < <= > >=   e.g. mv<=3
    r:  rarity:      c / u / r / m (or common, uncommon, rare, mythic)
    s:  set:  e:     set code, e.g. s:mh2
    f:  format:      legal in a format, e.g. f:modern
    is:commander     can be a Commander (legendary creature, or says so)
"""
import re
from dataclasses import dataclass, field

from django.db.models import Q

COLORS = "WUBRG"
COLOR_WORDS = {
    "white": "W", "blue": "U", "black": "B", "red": "R", "green": "G",
    # common two- and three-color names
    "azorius": "WU", "dimir": "UB", "rakdos": "BR", "gruul": "RG", "selesnya": "GW",
    "orzhov": "WB", "izzet": "UR", "golgari": "BG", "boros": "RW", "simic": "GU",
    "esper": "WUB", "grixis": "UBR", "jund": "BRG", "naya": "RGW", "bant": "GWU",
    "abzan": "WBG", "jeskai": "URW", "sultai": "BGU", "mardu": "RWB", "temur": "GUR",
}
COLOR_NAMES = {"W": "white", "U": "blue", "B": "black", "R": "red", "G": "green"}
RARITIES = {
    "c": "common", "common": "common", "u": "uncommon", "uncommon": "uncommon",
    "r": "rare", "rare": "rare", "m": "mythic", "mythic": "mythic",
    "s": "special", "special": "special",
}
NUM_OPS = {":": "", "=": "", ">": "__gt", ">=": "__gte", "<": "__lt", "<=": "__lte"}
OP_WORDS = {":": "is", "=": "is", ">": "is more than", ">=": "is at least", "<": "is less than",
            "<=": "is at most", "!=": "is not"}

ALIASES = {
    "n": "name", "name": "name",
    "o": "oracle", "oracle": "oracle", "text": "oracle",
    "t": "type", "type": "type",
    "k": "keyword", "kw": "keyword", "keyword": "keyword",
    "c": "color", "color": "color", "colors": "color",
    "id": "identity", "identity": "identity", "ci": "identity",
    "mv": "mv", "cmc": "mv", "manavalue": "mv",
    "r": "rarity", "rarity": "rarity",
    "s": "set", "set": "set", "e": "set", "edition": "set",
    "f": "format", "format": "format", "legal": "format",
    "is": "is",
}

# A token is:  -key:"quoted value"  |  -key:value  |  -"quoted words"  |  word
TOKEN_RE = re.compile(r'-?[A-Za-z]+(?:>=|<=|!=|[:=<>])"[^"]*"?|-?"[^"]*"?|\S+')
FILTER_RE = re.compile(r'^(-?)([A-Za-z]+)(>=|<=|!=|[:=<>])(.*)$', re.S)
FORMAT_RE = re.compile(r"^[a-z]+$")


@dataclass
class SearchResult:
    q: Q = field(default_factory=Q)
    descriptions: list = field(default_factory=list)  # plain-English summary, one per filter
    errors: list = field(default_factory=list)        # filters we couldn't understand
    highlight: list = field(default_factory=list)     # words to highlight in rules text

    @property
    def is_empty(self):
        return not self.descriptions


def _unquote(text):
    return text.strip().strip('"').strip()


def parse_colors(text):
    """'ur', 'izzet', 'blue' -> 'UR'. Returns None when it isn't a color."""
    t = text.lower()
    if t in COLOR_WORDS:
        return COLOR_WORDS[t]
    if t and all(ch in "wubrg" for ch in t):
        return "".join(c for c in COLORS if c.lower() in t)
    return None


def _color_words(letters):
    return "/".join(COLOR_NAMES[c] for c in letters)


def identity_q(letters):
    """Cards whose color identity fits inside `letters` (Commander deckbuilding rule)."""
    q = Q()
    for c in COLORS:
        if c not in letters:
            q &= ~Q(color_identity__contains=c)
    return q


def _text_q(value):
    return Q(name__icontains=value) | Q(type_line__icontains=value) | Q(oracle_text__icontains=value)


def _filter(key, op, value):
    """Return (Q, description) for one filter, or raise ValueError with a helpful message."""
    if value == "":
        raise ValueError(f'"{key}{op}" needs a value after it.')

    if key == "name":
        return Q(name__icontains=value), f'name contains "{value}"'
    if key == "oracle":
        return Q(oracle_text__icontains=value), f'text contains "{value}"'
    if key == "type":
        return Q(type_line__icontains=value), f'type contains "{value}"'
    if key == "keyword":
        return Q(keywords__icontains=value), f'has keyword "{value}"'
    if key == "set":
        return Q(set_code__iexact=value), f"set is {value.upper()}"

    if key == "rarity":
        rarity = RARITIES.get(value.lower())
        if not rarity:
            raise ValueError(f'"{value}" isn\'t a rarity. Use c, u, r or m.')
        return Q(rarity=rarity), f"rarity is {rarity}"

    if key == "mv":
        try:
            number = float(value)
        except ValueError:
            raise ValueError(f'Mana value needs a number, like mv<=3 (got "{value}").')
        shown = int(number) if number.is_integer() else number
        if op == "!=":
            return ~Q(cmc=number), f"mana value is not {shown}"
        return Q(**{"cmc" + NUM_OPS[op]: number}), f"mana value {OP_WORDS[op]} {shown}"

    if key == "color":
        v = value.lower()
        if v in ("c", "colorless"):
            return Q(colors=""), "colorless"
        if v in ("m", "multi", "multicolor", "gold"):
            return Q(colors__regex=r"^.{2,}$"), "multicolor"
        letters = parse_colors(v)
        if letters is None:
            raise ValueError(f'"{value}" isn\'t a color. Use letters w u b r g, c for colorless, or m for multicolor.')
        has_all = Q()
        for c in letters:
            has_all &= Q(colors__contains=c)
        if op in (":", ">="):
            return has_all, f"color includes {_color_words(letters)}"
        if op == "=":
            return has_all & Q(colors__regex=rf"^.{{{len(letters)}}}$"), f"color is exactly {_color_words(letters)}"
        if op == "<=":
            q = Q()
            for c in COLORS:
                if c not in letters:
                    q &= ~Q(colors__contains=c)
            return q, f"color is only {_color_words(letters)} (or colorless)"
        raise ValueError(f'Use c:, c=, or c<= with colors (got "{op}").')

    if key == "identity":
        v = value.lower()
        letters = "" if v in ("c", "colorless") else parse_colors(v)
        if letters is None:
            raise ValueError(f'"{value}" isn\'t a color identity. Use letters like id:wub.')
        words = _color_words(letters) if letters else "colorless"
        return identity_q(letters), f"fits in a {words} Commander deck"

    if key == "format":
        fmt = value.lower()
        if not FORMAT_RE.match(fmt):
            raise ValueError(f'"{value}" isn\'t a format name.')
        return (Q(**{f"legalities__{fmt}": "legal"}) | Q(**{f"legalities__{fmt}": "restricted"}),
                f"legal in {fmt.capitalize()}")

    if key == "is":
        if value.lower() in ("commander", "general"):
            q = (Q(type_line__icontains="legendary") & Q(type_line__icontains="creature")) | \
                Q(oracle_text__icontains="can be your commander")
            return q, "can be your commander"
        raise ValueError(f'"is:{value}" isn\'t supported. Try is:commander.')

    raise ValueError(f"Unknown filter {key}.")


def parse(query):
    result = SearchResult()
    for token in TOKEN_RE.findall(query or ""):
        match = FILTER_RE.match(token)
        if match and match.group(2).lower() in ALIASES:
            negate, raw_key, op, raw_value = match.groups()
            key = ALIASES[raw_key.lower()]
            value = _unquote(raw_value)
            try:
                q, text = _filter(key, op, value)
            except ValueError as exc:
                result.errors.append(str(exc))
                continue
            if op == "!=" and key not in ("mv",):
                negate = "" if negate else "-"
            if key in ("oracle",) and not negate:
                result.highlight.append(value)
        elif match and match.group(3) in (":", "=") and not match.group(4).startswith("/"):
            # Looks like a filter (word:value) but the word isn't one we know.
            result.errors.append(
                f'Unknown filter "{match.group(2)}{match.group(3)}". '
                "Try name:, o:, t:, k:, c:, id:, mv, r:, s:, f: or is:commander."
            )
            continue
        else:
            negate = "-" if token.startswith("-") and len(token) > 1 else ""
            value = _unquote(token[1:] if negate else token)
            if not value:
                continue
            q, text = _text_q(value), f'name, type or text contains "{value}"'
            if not negate:
                result.highlight.append(value)

        if negate:
            q, text = ~q, "not " + text
        result.q &= q
        result.descriptions.append(text)
    return result
