"""Load and search the Magic: The Gathering Comprehensive Rules.

Wizards publishes the rules as a text file linked from https://magic.wizards.com/en/rules
(a new version a few times a year). The file looks like this:

    Magic: The Gathering Comprehensive Rules
    These rules are effective as of September 25, 2026.
    ...Contents (chapter and section titles)...
    1. Game Concepts                      <- chapter
    100. General                          <- section
    100.1. These Magic rules apply ...    <- rule
    100.1a A two-player game is ...       <- subrule
    Example: ...                          <- example for the entry above
    ...
    Glossary
    Abandon                               <- term
    To turn a face-up ongoing scheme ...  <- definition
    (blank line between terms)
    Credits

parse() turns that into plain dicts (testable without a database); save() and
load_latest() put it in the database.
"""
import re

import requests

RULES_PAGE = "https://magic.wizards.com/en/rules"
TXT_LINK_RE = re.compile(r"https://media\.wizards\.com/[^\"'\s<>]+?\.txt", re.I)

CHAPTER_RE = re.compile(r"^(\d)\.\s+(.+)$")
SECTION_RE = re.compile(r"^(\d{3})\.\s+(.+)$")
RULE_RE = re.compile(r"^(\d{3}\.\d+)\.\s+(.+)$")
SUBRULE_RE = re.compile(r"^(\d{3}\.\d+[a-z]+)\s+(.+)$")
EXAMPLE_RE = re.compile(r"^Example:\s*(.+)$")
EFFECTIVE_RE = re.compile(r"effective as of ([A-Z][a-z]+ \d{1,2}, \d{4})")
# Rule numbers inside rules text, e.g. "See rule 702.19b" or "section 702"
REFERENCE_RE = re.compile(r"\b(\d{3}\.\d+[a-z]?)\b|\b((?i:rules?|sections?)\s+)(\d{3})(?![.\d]*\d)\b")
NUMBER_QUERY_RE = re.compile(r"^\s*(\d{1,3}(?:\.\d+[a-z]?)?)\.?\s*$", re.I)


class RulesError(Exception):
    pass


def decode(raw):
    """The rules file has been published as UTF-8 (with or without a BOM) and as Windows-1252."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _clean(line):
    return line.replace(" ", " ").replace("﻿", "").strip()


def parse(text):
    """Returns {"effective": str, "rules": [dict], "glossary": [dict]}."""
    lines = [_clean(l) for l in text.splitlines()]
    effective = ""
    for line in lines[:40]:
        m = EFFECTIVE_RE.search(line)
        if m:
            effective = m.group(1)
            break

    # The contents list repeats the chapter titles, so the rules body starts at the
    # SECOND "1. ..." chapter line. Fall back to the first one if there's no contents.
    starts = [i for i, l in enumerate(lines) if CHAPTER_RE.match(l) and l.startswith("1.")]
    if not starts:
        raise RulesError("This doesn't look like the Comprehensive Rules: no '1. Game Concepts' line.")
    body_start = starts[1] if len(starts) > 1 else starts[0]
    glossary_at = next((i for i in range(body_start, len(lines)) if lines[i] == "Glossary"), len(lines))
    credits_at = next((i for i in range(glossary_at, len(lines)) if lines[i] == "Credits"), len(lines))

    rules, current = [], None
    chapter = section = rule = ""
    for line in lines[body_start:glossary_at]:
        if not line:
            continue
        m = EXAMPLE_RE.match(line)
        if m and current:
            current["examples"].append(m.group(1))
            continue
        entry = None
        if (m := SUBRULE_RE.match(line)):
            number = m.group(1)
            entry = {"number": number, "kind": "subrule", "parent": re.sub(r"[a-z]+$", "", number), "text": m.group(2)}
        elif (m := RULE_RE.match(line)):
            rule = m.group(1)
            entry = {"number": rule, "kind": "rule", "parent": rule.split(".")[0], "text": m.group(2)}
        elif (m := SECTION_RE.match(line)):
            section = m.group(1)
            entry = {"number": section, "kind": "section", "parent": section[0], "text": m.group(2)}
        elif (m := CHAPTER_RE.match(line)):
            chapter = m.group(1)
            entry = {"number": chapter, "kind": "chapter", "parent": "", "text": m.group(2)}
        if entry:
            entry["section"] = entry["number"][:3] if entry["kind"] != "chapter" else ""
            entry["examples"] = []
            rules.append(entry)
            current = entry
        elif current:
            current["text"] += "\n" + line   # rare continuation paragraph

    glossary, block = [], []
    for line in lines[glossary_at + 1:credits_at] + [""]:
        if line:
            block.append(line)
        elif block:
            if len(block) >= 2:
                glossary.append({"term": block[0], "definition": "\n".join(block[1:])})
            block = []

    # A number seen twice (e.g. a stray contents line) keeps its first, fuller entry.
    seen, unique = set(), []
    for r in rules:
        if r["number"] not in seen:
            seen.add(r["number"])
            unique.append(r)
    rules = unique
    if not any(r["kind"] == "subrule" for r in rules):
        raise RulesError("No numbered rules were found in the file.")
    return {"effective": effective, "rules": rules, "glossary": glossary}


def save(parsed, source_url=""):
    """Replace the stored rules with a parsed edition."""
    from django.db import transaction

    from .models import GlossaryTerm, Rule, RulesEdition

    with transaction.atomic():
        Rule.objects.all().delete()
        GlossaryTerm.objects.all().delete()
        Rule.objects.bulk_create([
            Rule(number=r["number"], kind=r["kind"], parent=r["parent"], section=r["section"],
                 text=r["text"], examples="\n".join(r["examples"]), order=i)
            for i, r in enumerate(parsed["rules"])
        ], batch_size=500)
        GlossaryTerm.objects.bulk_create([
            GlossaryTerm(term=g["term"], definition=g["definition"], order=i)
            for i, g in enumerate(parsed["glossary"])
        ], batch_size=500)
        RulesEdition.objects.all().delete()
        edition = RulesEdition.objects.create(
            effective=parsed["effective"] or "unknown date", source_url=source_url,
            rule_count=len(parsed["rules"]), term_count=len(parsed["glossary"]))
    return edition


def _get(url):
    try:
        resp = requests.get(url, timeout=60, headers={"User-Agent": "ManaLedger/1.0 (rules reader)"})
    except requests.RequestException as exc:
        raise RulesError(f"Couldn't reach {url}: {exc}") from exc
    if resp.status_code >= 400:
        raise RulesError(f"{url} returned {resp.status_code}.")
    return resp


def latest_url():
    """The current rules .txt link from Wizards' rules page."""
    links = TXT_LINK_RE.findall(_get(RULES_PAGE).text)
    comp = [l for l in links if "comprules" in l.lower()] or links
    if not comp:
        raise RulesError("Couldn't find the rules text file on magic.wizards.com/en/rules. "
                         "Download it yourself and run: python manage.py load_rules --file MagicCompRules.txt")
    return sorted(set(comp))[-1]   # file names end in the date, so the last sorts newest


def load_latest(url=None):
    url = url or latest_url()
    parsed = parse(decode(_get(url).content))
    return save(parsed, url)


def load_file(path):
    with open(path, "rb") as f:
        parsed = parse(decode(f.read()))
    return save(parsed, "")


def keyword_index():
    """{"deathtouch": "702.2", "scry": "701.22", ...}: keyword abilities and actions by name."""
    from .models import Rule

    index = {}
    for number, text in Rule.objects.filter(kind="rule", parent__in=["701", "702"]).values_list("number", "text"):
        title = text.strip()
        if len(title) <= 40 and not title.endswith("."):
            index[title.lower()] = number
    return index
