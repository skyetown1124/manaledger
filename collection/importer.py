"""Read a pasted card list and match each line to a Scryfall printing.

Accepted line formats (the same ones MTG Arena, Moxfield and Archidekt export):

    4 Lightning Bolt (2XM) 117        quantity, name, set code, collector number
    4x Lightning Bolt (2XM)           set without a number: newest printing in that set
    Lightning Bolt                    name only: Scryfall's default printing, 1 copy
    1 Sol Ring (CMM) 410 *F*          *F* or "foil" = foil, *E* or "etched" = etched foil
    [2XM] works the same as (2XM).

Blank lines, "Deck" / "Sideboard" / "Commander" headers and lines starting with
# or // are skipped.
"""
import re
import unicodedata
from dataclasses import dataclass, field

MAX_LINES = 2000

LINE_RE = re.compile(
    r"""^\s*
    (?:(?P<qty>\d{1,3})\s*[xX]?\s+)?                       # 4  or  4x
    (?P<name>.+?)                                         # card name
    (?:\s+[(\[](?P<set>[A-Za-z0-9]{2,6})[)\]]               # (SET) or [SET]
       (?:\s+(?P<cn>[A-Za-z0-9★\-]+))?)?                    # collector number
    (?P<flags>(?:\s+(?:\*[FfEe]\*|\(?(?:foil|etched)\)?))*) # *F*, foil, (etched)
    \s*$""",
    re.X | re.I,
)
HEADER_RE = re.compile(r"^(deck|main ?deck|mainboard|sideboard|commander|companion|maybeboard|about|name\b.*)$", re.I)


@dataclass
class Line:
    number: int
    raw: str
    quantity: int
    name: str
    set_code: str = ""
    collector_number: str = ""
    finish: str = "nonfoil"

    @property
    def identifier(self):
        """What to ask Scryfall for: the most specific thing the line gives."""
        if self.set_code and self.collector_number:
            return {"set": self.set_code, "collector_number": self.collector_number}
        if self.set_code:
            return {"name": self.name, "set": self.set_code}
        return {"name": self.name}


@dataclass
class Problem:
    number: int
    raw: str
    message: str


@dataclass
class Match:
    line: Line
    card: dict           # parsed Scryfall card (see scryfall.parse_card)
    finish: str
    note: str = ""

    @property
    def price(self):
        key = {"nonfoil": "usd", "foil": "usd_foil", "etched": "usd_etched"}[self.finish]
        return self.card.get(key)

    @property
    def value(self):
        return (self.price or 0) * self.line.quantity


@dataclass
class ImportResult:
    matches: list = field(default_factory=list)
    problems: list = field(default_factory=list)

    @property
    def copies(self):
        return sum(m.line.quantity for m in self.matches)

    @property
    def total_value(self):
        return sum(m.value for m in self.matches)

    def items(self):
        """Merged {id, quantity, finish} rows, ready to add to the collection."""
        merged = {}
        for m in self.matches:
            key = (m.card["scryfall_id"], m.finish)
            merged[key] = merged.get(key, 0) + m.line.quantity
        return [{"id": cid, "finish": fin, "quantity": qty} for (cid, fin), qty in merged.items()]


def normalize(name):
    text = (name or "").replace("’", "'").replace("‘", "'")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()  # Lim-Dûl -> Lim-Dul
    return re.sub(r"\s+", " ", text).strip().lower()


def names_match(given, card_name):
    """'Delver of Secrets' matches 'Delver of Secrets // Insectile Aberration'."""
    g = normalize(given)
    faces = [normalize(card_name)] + [normalize(f) for f in card_name.split(" // ")]
    return g in faces


def parse_lines(text):
    """Return (lines, problems). Doesn't contact Scryfall."""
    lines, problems = [], []
    rows = (text or "").splitlines()
    if len(rows) > MAX_LINES:
        problems.append(Problem(0, "", f"That's {len(rows)} lines. Import up to {MAX_LINES} at a time."))
        rows = rows[:MAX_LINES]
    for number, raw in enumerate(rows, start=1):
        text_line = raw.strip()
        if not text_line or text_line.startswith(("#", "//")) or HEADER_RE.match(text_line):
            continue
        match = LINE_RE.match(text_line)
        if not match or not match.group("name").strip():
            problems.append(Problem(number, raw, "Couldn't read this line. Use a format like: 4 Lightning Bolt (2XM) 117"))
            continue
        qty = int(match.group("qty") or 1)
        if qty < 1:
            problems.append(Problem(number, raw, "Quantity must be at least 1."))
            continue
        flags = (match.group("flags") or "").lower()
        finish = "etched" if ("*e*" in flags or "etched" in flags) else "foil" if ("*f*" in flags or "foil" in flags) else "nonfoil"
        lines.append(Line(
            number=number, raw=raw, quantity=qty, name=match.group("name").strip(),
            set_code=(match.group("set") or "").lower(),
            collector_number=(match.group("cn") or "").lower(),
            finish=finish,
        ))
    return lines, problems


def _find(line, cards):
    for card in cards:
        same_set = card["set_code"].lower() == line.set_code
        if line.set_code and line.collector_number:
            if same_set and card["collector_number"].lower() == line.collector_number:
                return card
        elif line.set_code:
            if same_set and names_match(line.name, card["name"]):
                return card
        elif names_match(line.name, card["name"]):
            return card
    return None


def resolve(text, lookup):
    """Parse `text` and match every line using `lookup` (scryfall.lookup)."""
    result = ImportResult()
    lines, result.problems = parse_lines(text)
    if not lines:
        return result

    unique, seen = [], set()
    for line in lines:
        key = tuple(sorted(line.identifier.items()))
        if key not in seen:
            seen.add(key)
            unique.append(line.identifier)
    cards, _not_found = lookup(unique)

    for line in lines:
        card = _find(line, cards)
        if card is None:
            if line.set_code and line.collector_number:
                msg = f"Scryfall has no card numbered {line.collector_number.upper()} in set {line.set_code.upper()}. Check the set code and number."
            elif line.set_code:
                msg = f'Scryfall has no "{line.name}" in set {line.set_code.upper()}. Check the spelling or the set code.'
            else:
                msg = f'Scryfall has no card named "{line.name}". Check the spelling.'
            result.problems.append(Problem(line.number, line.raw, msg))
            continue
        if line.collector_number and not names_match(line.name, card["name"]):
            result.problems.append(Problem(
                line.number, line.raw,
                f'{line.set_code.upper()} #{line.collector_number.upper()} is {card["name"]}, not "{line.name}". '
                "Fix the name or the number.",
            ))
            continue

        finish, note = line.finish, ""
        available = card.get("finishes") or ["nonfoil"]
        if finish not in available:
            finish = available[0]
            note = f"This printing has no {line.finish} version; added as {finish}."
        result.matches.append(Match(line=line, card=card, finish=finish, note=note))

    result.problems.sort(key=lambda p: p.number)
    return result
