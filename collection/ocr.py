"""Read Magic cards from photos with Tesseract OCR (free, runs on your machine).

What the OCR reads well, and how it's used:
  * Card names (big text at the top of each card) read cleanly.
  * Collector numbers in the bottom-left corner of cards printed since about 2015
    ("107/281 M" or "0107 R") read well.
  * The set code under the number ("DMU • EN") is tiny and often garbled
    (DMU -> OMU), so it is treated as a hint, not a fact.

For each collector number found, candidate printings are built from the set
code and its usual misreadings, plus every set with that many cards (the "/281"),
then checked against Scryfall. The card names read in the same photo pick the
right one. Cards without collector info (older frames) are found by name with
Scryfall's typo-tolerant search, using the default printing.

identify() takes a Scryfall-like object (lookup, fuzzy, search, sets), which
keeps this module testable without the network.
"""
import difflib
import itertools
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

try:
    import pytesseract
    from PIL import Image, ImageOps
except ImportError:  # the rest of the app works without photo import
    pytesseract = None

MAX_PHOTOS = 20
MAX_BYTES = 15 * 1024 * 1024
LANGS = {"EN", "ES", "FR", "DE", "IT", "PT", "JA", "JP", "KO", "RU", "ZHS", "ZHT", "ZH", "PH", "CS", "CT"}

# "107/281 M", "0107 R", "R 0107", "107/281"  (needs a rarity letter or a /total)
NUMBER_RE = re.compile(r"(?:\b([CURMSLTP])\s+)?\b(\d{1,4})(?:\s*/\s*(\d{2,4}))?(?:\s+([CURMSLTP])\b)?")
# Characters OCR mixes up in small print
CONFUSIONS = {
    "O": "D0Q", "D": "O0", "0": "OD", "Q": "O", "N": "MH", "M": "NW", "W": "M", "H": "N",
    "I": "1L", "1": "IL", "L": "1I", "S": "5", "5": "S", "B": "8", "8": "B", "Z": "2", "2": "Z",
    "G": "6C", "6": "G", "C": "G", "U": "V", "V": "U", "E": "F", "F": "E", "T": "7", "7": "T",
}
TYPE_WORDS = {"legendary", "basic", "snow", "world", "creature", "instant", "sorcery", "artifact",
              "enchantment", "land", "planeswalker", "battle", "kindred", "tribal"}
SMALL_WORDS = {"of", "the", "and", "a", "an", "to", "in", "on", "for", "with", "from", "at", "de", "la"}


@dataclass
class TextLine:
    text: str
    left: int
    top: int
    right: int
    bottom: int

    @property
    def height(self):
        return self.bottom - self.top


@dataclass
class Anchor:
    """One card's collector info as read: number, optional /total and rarity, and the set-code text."""
    number: str
    total: str = ""
    rarity: str = ""
    set_text: str = ""


@dataclass
class PhotoResult:
    filename: str
    cards: list = field(default_factory=list)   # parsed Scryfall cards found in this photo
    message: str = ""


def status():
    """(ready, message). Photo import needs pytesseract, Pillow and the Tesseract program."""
    if pytesseract is None:
        return False, "Photo import needs two Python packages. Run: pip install -r requirements.txt"
    try:
        pytesseract.get_tesseract_version()
    except Exception:
        return False, ("Photo import needs the free Tesseract OCR program. In a codespace or on Linux run: "
                       "sudo apt-get install -y tesseract-ocr  (Mac: brew install tesseract; "
                       "Windows: install it from github.com/UB-Mannheim/tesseract/wiki).")
    return True, ""


# ----------------------------------------------------------------- reading text

def prepare(image):
    """Upright, grayscale, high-contrast, and big enough for the small collector text."""
    img = ImageOps.exif_transpose(image).convert("L")
    longest = max(img.size)
    target = min(max(longest, 2400), 4000)
    if longest != target:
        scale = target / longest
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    return ImageOps.autocontrast(img, cutoff=1)


def _ocr_words(img, dx=0, dy=0):
    """Words as (text, left, top, width, height, confidence)."""
    data = pytesseract.image_to_data(img, config="--psm 11", output_type=pytesseract.Output.DICT)
    return [
        (t.strip(), data["left"][i] + dx, data["top"][i] + dy, data["width"][i], data["height"][i],
         float(data["conf"][i]))
        for i, t in enumerate(data["text"])
        if t.strip() and float(data["conf"][i]) > 30
    ]


def _merge(words, extra):
    """Add words from another pass. Where two readings overlap, keep the more confident one."""
    for word in extra:
        clashes = [w for w in words if _overlaps(word, w)]
        if not clashes:
            words.append(word)
        elif all(word[5] > w[5] for w in clashes):
            for w in clashes:
                words.remove(w)
            words.append(word)
    return words


def _tiles(img, size=1000, step=750):
    ys = list(range(0, max(1, img.height - size), step)) + [max(0, img.height - size)]
    xs = list(range(0, max(1, img.width - size), step)) + [max(0, img.width - size)]
    for y in sorted(set(ys)):
        for x in sorted(set(xs)):
            yield x, y, img.crop((x, y, min(x + size, img.width), min(y + size, img.height)))


def _overlaps(a, b):
    """True when two word boxes cover mostly the same spot."""
    w = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    h = min(a[2] + a[4], b[2] + b[4]) - max(a[2], b[2])
    return w > 0 and h > 0 and w * h > 0.5 * min(a[3] * a[4], b[3] * b[4])


def words_to_lines(words):
    """Group OCR words [(text, left, top, width, height)] into lines of text.

    Words on the same row are joined left to right; a wide gap starts a new line,
    so cards side by side in one photo stay separate.
    """
    rows = []  # each: [mid_y, height, [words]]
    for w in sorted(words, key=lambda w: w[2] + w[4] / 2):
        mid, h = w[2] + w[4] / 2, w[4]
        for row in rows[-8:]:
            if abs(mid - row[0]) < 0.5 * max(h, row[1]):
                row[2].append(w)
                break
        else:
            rows.append([mid, h, [w]])

    lines = []
    for _, _, row_words in rows:
        current = None
        for text, left, top, width, height in sorted(row_words, key=lambda w: w[1]):
            if current and left - current.right < 2.2 * max(height, current.height):
                current.text += " " + text
                current.right = max(current.right, left + width)
                current.top, current.bottom = min(current.top, top), max(current.bottom, top + height)
            else:
                current = TextLine(text, left, top, left + width, top + height)
                lines.append(current)
    return sorted(lines, key=lambda l: (l.top, l.left))


def read_lines(image):
    """Run Tesseract on a PIL image. Returns (lines of text, image size).

    Pass 1 reads the whole photo (names, rules text). Pass 2 reads an inverted
    copy in tiles, because the collector info is small light text on the black
    border, which a whole-photo pass tends to skip.
    """
    img = prepare(image)
    words = _ocr_words(img)
    inverted = ImageOps.invert(img)
    for x, y, tile in _tiles(inverted):
        _merge(words, _ocr_words(tile, x, y))
    _reread_corners(inverted, words)
    return words_to_lines([w[:5] for w in words]), img.size


def _reread_corners(inverted, words):
    """Each language code ("EN") marks a card's collector info. Re-read that small
    area enlarged, which picks up the collector number a page-wide pass misses."""
    marks = [w for w in words if w[0].upper().strip(".,•") in LANGS]
    for text, left, top, width, height, _conf in marks:
        h = max(height, 8)
        zoom = max(2, min(5, round(48 / h)))  # aim for text about 48px tall
        box = (max(0, left - 9 * h), max(0, top - 3 * h),
               min(inverted.width, left + 12 * h), min(inverted.height, top + 2 * h))
        crop = inverted.crop(box)
        crop = crop.resize((crop.width * zoom, crop.height * zoom), Image.LANCZOS)
        found = []
        for t, l, tp, w, hh, conf in _ocr_words_block(crop):
            found.append((t, box[0] + l // zoom, box[1] + tp // zoom, max(1, w // zoom), max(1, hh // zoom), conf))
        _merge(words, found)


def _ocr_words_block(img):
    """Like _ocr_words, but reads the image as one block of text (better for a small crop)."""
    data = pytesseract.image_to_data(img, config="--psm 6", output_type=pytesseract.Output.DICT)
    return [
        (t.strip(), data["left"][i], data["top"][i], data["width"][i], data["height"][i], float(data["conf"][i]))
        for i, t in enumerate(data["text"])
        if t.strip() and float(data["conf"][i]) > 30
    ]


# ----------------------------------------------------------------- what's on the card

def anchors(lines):
    """Collector info found in the photo, one Anchor per card."""
    found, used = [], set()
    for i, line in enumerate(lines):
        for m in NUMBER_RE.finditer(line.text):
            rarity1, number, total, rarity2 = m.groups()
            if not (rarity1 or rarity2 or total):
                continue
            if total and int(total) < 20:  # power/toughness like 4/5
                continue
            anchor = Anchor(number=number.lstrip("0") or "0", total=total or "", rarity=rarity1 or rarity2 or "")
            # The set code sits on the next line down: "DMU • EN  Artist Name"
            h = max(line.height, 8)
            for j, below in enumerate(lines):
                if j in used or j == i:
                    continue
                gap = below.top - line.bottom
                if -0.3 * h <= gap <= 2.5 * h and abs(below.left - line.left) <= 6 * h:
                    anchor.set_text = _set_text(below.text)
                    if anchor.set_text:
                        used.add(j)
                        break
            found.append(anchor)
            break
    return found


def _set_text(text):
    """'DMU • EN Chris Rahn' -> 'DMU'. Takes the token before the language code, else the first token."""
    tokens = re.findall(r"[A-Za-z0-9]+", text)
    for k, tok in enumerate(tokens):
        if tok.upper() in LANGS and k > 0:
            return tokens[k - 1].upper()[:5]
    return tokens[0].upper()[:5] if tokens and 3 <= len(tokens[0]) <= 5 else ""


def set_candidates(set_text, total, known_sets, limit=40):
    """Real set codes the misread text could be, plus sets with exactly `total` cards."""
    codes = {s["code"].upper() for s in known_sets}
    picks = []
    text = re.sub(r"[^A-Z0-9]", "", (set_text or "").upper())
    if text:
        if text in codes:
            picks.append(text)
        for length in {3, 4, len(text)}:
            base = text[:length]
            if len(base) < 3:
                continue
            options = [[ch] + list(CONFUSIONS.get(ch, "")) for ch in base]
            for combo in itertools.product(*options):
                code = "".join(combo)
                changed = sum(a != b for a, b in zip(code, base))
                if changed <= 2 and code in codes and code not in picks:
                    picks.append(code)
    if total:
        for s in known_sets:
            if str(s["size"]) == str(int(total)) and s["code"].upper() not in picks:
                picks.append(s["code"].upper())
    return [p.lower() for p in picks[:limit]]


def _norm(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", "", text).strip()


def similarity(read_text, card_name):
    """0..1: how well OCR text matches a card name (either face of double-faced cards)."""
    names = [card_name] + card_name.split(" // ")
    return max(difflib.SequenceMatcher(None, _norm(read_text), _norm(n)).ratio() for n in names)


def name_candidates(lines, limit=12):
    """Lines that look like card names: the biggest Title Case text, not type lines or artist credits."""
    picks = []
    lettered = [l for l in lines if sum(ch.isalpha() for ch in l.text) >= 4]
    tallest = max((l.height for l in lettered), default=0)
    for line in lines:
        raw = line.text.strip()
        if "—" in raw or "{" in raw or line.height < 0.75 * tallest:
            continue  # type lines, rules text and small print are shorter than names
        if any(tok.upper() in LANGS for tok in re.findall(r"[A-Za-z]+", raw)):
            continue  # "DMU • EN Chris Rahn": collector line with the artist's name
        text = re.sub(r"[^A-Za-z ,'\-]", "", raw).strip(" -,")
        words = [w for w in re.split(r"[ \-]", text) if w]
        if not (1 <= len(words) <= 7) or len(text.replace(" ", "")) < 4:
            continue
        if all(w.lower() in TYPE_WORDS for w in words):
            continue
        big_words = [w for w in words if w.lower() not in SMALL_WORDS]
        if not big_words or sum(w[0].isupper() for w in big_words) / len(big_words) < 0.75:
            continue
        if text not in picks:
            picks.append(text)
        if len(picks) == limit:
            break
    return picks


# ----------------------------------------------------------------- identify

def identify(photos, read, scryfall):
    """Identify cards in photos.

    photos:   [(filename, PIL image)]
    read:     image -> (lines, size), usually read_lines
    scryfall: object with lookup(identifiers), fuzzy(name), search(query), sets()
    Returns [PhotoResult].
    """
    scanned = []
    known = None
    batch = []  # identifiers to look up in one go
    for filename, image in photos:
        lines, _size = read(image)
        found_anchors = anchors(lines)
        if found_anchors and known is None:
            known = scryfall.sets()
        options = []
        for a in found_anchors:
            codes = set_candidates(a.set_text, a.total, known or [])
            ids = [{"set": c, "collector_number": a.number} for c in codes]
            batch += ids
            options.append((a, ids))
        scanned.append((filename, lines, options))

    by_key = {}
    if batch:
        unique = list({(i["set"], i["collector_number"]): i for i in batch}.values())
        cards, _ = scryfall.lookup(unique)
        by_key = {(c["set_code"].lower(), c["collector_number"].lower()): c for c in cards}

    results = []
    for filename, lines, options in scanned:
        result = PhotoResult(filename)
        names = name_candidates(lines)
        name_hits = {}  # OCR name text -> card from fuzzy search (only when asked)

        def fuzzy_hit(text):
            if text not in name_hits:
                card = scryfall.fuzzy(text)
                name_hits[text] = card if card and similarity(text, card["name"]) >= 0.8 else None
            return name_hits[text]

        unresolved = 0
        for anchor, ids in options:
            matches = [by_key[k] for k in ((i["set"], i["collector_number"]) for i in ids) if k in by_key]
            chosen = None
            # Prefer the printing whose name is printed in this photo.
            scored = sorted(((max((similarity(n, c["name"]) for n in names), default=0), c) for c in matches),
                            key=lambda sc: -sc[0])
            if scored and scored[0][0] >= 0.8:
                chosen = scored[0][1]
            elif len(matches) == 1 and not names:
                chosen = matches[0]
            else:
                # Fall back to name + collector number, e.g. a set code we couldn't read at all.
                for text in names:
                    card = fuzzy_hit(text)
                    if card:
                        hits = [c for c in scryfall.search(f'!"{card["name"]}" cn:{anchor.number}')
                                if c["collector_number"].lstrip("0") == anchor.number]
                        if hits:
                            chosen = hits[0]
                            break
            if chosen and chosen not in result.cards:
                result.cards.append(chosen)
            elif not chosen:
                unresolved += 1

        # Names with no collector info nearby (older cards): default printing.
        covered = {c["name"] for c in result.cards}
        by_name = []
        for text in names:
            if any(similarity(text, n) >= 0.8 for n in covered):
                continue
            card = fuzzy_hit(text)
            if card and card["name"] not in covered:
                covered.add(card["name"])
                by_name.append(card)
        result.cards += by_name

        found = len(result.cards)
        if not found:
            result.message = ("Couldn't read a card. Try a closer, sharper photo in good light without glare, "
                              "or type this card into the list.")
        else:
            parts = [f"Found {found} card{'s' if found != 1 else ''}."]
            if by_name:
                parts.append(f"{len(by_name)} found by name only, so the default printing was used: check the set.")
            if unresolved:
                parts.append(f"{unresolved} more card{'s' if unresolved != 1 else ''} had a number that couldn't be matched.")
            result.message = " ".join(parts)
        results.append(result)
    return results


def to_list_text(results):
    """Card list lines for the Import box: '2 Lightning Bolt (2XM) 117'."""
    counts, order = Counter(), {}
    for r in results:
        for c in r.cards:
            key = (c["name"], c["set_code"].upper(), c["collector_number"])
            counts[key] += 1
            order.setdefault(key, len(order))
    return "\n".join(f"{counts[k]} {k[0]} ({k[1]}) {k[2]}" for k in sorted(counts, key=order.get))
