"""Run with:  python manage.py test collection"""
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from io import StringIO
from pathlib import Path
import tempfile

from django.core.management import call_command

from . import decks, importer, scryfall, search
from .models import Card, CollectionItem, Deck, DeckCard

LEGAL_ALL = {f: "legal" for f in ("commander", "standard", "pioneer", "modern", "legacy", "vintage", "pauper")}


def make_card(name, **kw):
    defaults = dict(
        scryfall_id=f"id-{name}", set_code="tst", set_name="Test Set", collector_number="1",
        rarity="rare", colors="", type_line="Artifact", mana_cost="", cmc=0, oracle_text="",
        color_identity="", legalities=LEGAL_ALL, details_loaded=True, usd=1,
    )
    defaults.update(kw)
    return Card.objects.create(name=name, **defaults)


class SearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.bolt = make_card("Lightning Bolt", colors="R", color_identity="R", type_line="Instant",
                             mana_cost="{R}", cmc=1, oracle_text="Lightning Bolt deals 3 damage to any target.", rarity="uncommon")
        cls.study = make_card("Rhystic Study", colors="U", color_identity="U", type_line="Enchantment",
                              mana_cost="{2}{U}", cmc=3, oracle_text="Whenever an opponent casts a spell, you may draw a card unless that player pays {1}.", rarity="common")
        cls.sheoldred = make_card("Sheoldred, the Apocalypse", colors="B", color_identity="B",
                                  type_line="Legendary Creature — Phyrexian Praetor", mana_cost="{2}{B}{B}", cmc=4,
                                  oracle_text="Deathtouch\nWhenever you draw a card, you gain 2 life.", keywords="Deathtouch", rarity="mythic")
        cls.teferi = make_card("Teferi, Hero of Dominaria", colors="WU", color_identity="WU",
                               type_line="Legendary Planeswalker — Teferi", mana_cost="{3}{W}{U}", cmc=5,
                               oracle_text="+1: Draw a card.", rarity="mythic",
                               legalities={**LEGAL_ALL, "standard": "not_legal"})
        cls.ring = make_card("Sol Ring", type_line="Artifact", mana_cost="{1}", cmc=1, oracle_text="{T}: Add {C}{C}.",
                             legalities={**LEGAL_ALL, "modern": "not_legal", "vintage": "restricted"})

    def names(self, query):
        result = search.parse(query)
        self.assertEqual(result.errors, [], query)
        return sorted(Card.objects.filter(result.q).values_list("name", flat=True))

    def test_plain_words_search_name_type_and_text(self):
        self.assertEqual(self.names("bolt"), ["Lightning Bolt"])
        self.assertEqual(self.names("draw"), ["Rhystic Study", "Sheoldred, the Apocalypse", "Teferi, Hero of Dominaria"])
        self.assertEqual(self.names("praetor"), ["Sheoldred, the Apocalypse"])

    def test_quoted_phrase_and_oracle(self):
        self.assertEqual(self.names('o:"draw a card"'),
                         ["Rhystic Study", "Sheoldred, the Apocalypse", "Teferi, Hero of Dominaria"])
        self.assertEqual(self.names('"any target"'), ["Lightning Bolt"])

    def test_type_and_negation(self):
        self.assertEqual(self.names("t:legendary -t:creature"), ["Teferi, Hero of Dominaria"])

    def test_colors(self):
        self.assertEqual(self.names("c:u"), ["Rhystic Study", "Teferi, Hero of Dominaria"])
        self.assertEqual(self.names("c=u"), ["Rhystic Study"])
        self.assertEqual(self.names("c:azorius"), ["Teferi, Hero of Dominaria"])
        self.assertEqual(self.names("c:c"), ["Sol Ring"])
        self.assertEqual(self.names("c:m"), ["Teferi, Hero of Dominaria"])
        self.assertEqual(self.names("c<=ub"), ["Rhystic Study", "Sheoldred, the Apocalypse", "Sol Ring"])

    def test_identity_for_commander(self):
        self.assertEqual(self.names("id:wu"), ["Rhystic Study", "Sol Ring", "Teferi, Hero of Dominaria"])

    def test_mana_value(self):
        self.assertEqual(self.names("mv<=1"), ["Lightning Bolt", "Sol Ring"])
        self.assertEqual(self.names("mv>=4"), ["Sheoldred, the Apocalypse", "Teferi, Hero of Dominaria"])
        self.assertEqual(self.names("cmc=3"), ["Rhystic Study"])

    def test_rarity_keyword_format_and_commander(self):
        self.assertEqual(self.names("r:m"), ["Sheoldred, the Apocalypse", "Teferi, Hero of Dominaria"])
        self.assertEqual(self.names("k:deathtouch"), ["Sheoldred, the Apocalypse"])
        self.assertNotIn("Sol Ring", self.names("f:modern"))
        self.assertIn("Sol Ring", self.names("f:vintage"))  # restricted still counts as playable
        self.assertEqual(self.names("is:commander"), ["Sheoldred, the Apocalypse"])

    def test_combined_and_apostrophes(self):
        self.assertEqual(self.names("t:instant c:r mv<=1"), ["Lightning Bolt"])
        self.assertEqual(search.parse("Urza's").errors, [])

    def test_errors_are_reported_not_raised(self):
        result = search.parse("mv<=abc c:purple zz:top bolt")
        self.assertEqual(len(result.errors), 3)
        self.assertEqual(result.descriptions, ['name, type or text contains "bolt"'])


class DeckRuleTests(TestCase):
    def entry(self, name, qty=1, board="main", **kw):
        e = dict(card_id=name, name=name, set_code="tst", collector_number="1", board=board, quantity=qty,
                 type_line="Instant", mana_cost="{1}", cmc=1, colors="", color_identity="", oracle_text="",
                 legalities=LEGAL_ALL, details_loaded=True, price=1.0, owned=qty, is_basic=False)
        e.update(kw)
        return e

    def failing(self, result):
        return [c["label"] for c in result["checks"] if not c["ok"]]

    def test_modern_deck_of_60_passes(self):
        entries = [self.entry(f"Card {i}", 4) for i in range(11)] + \
                  [self.entry("Mountain", 16, type_line="Basic Land — Mountain", is_basic=True, owned=0)]
        result = decks.analyze("modern", entries)
        self.assertEqual(result["deck_count"], 60)
        self.assertEqual(self.failing(result), [])
        self.assertEqual(result["land_count"], 16)
        self.assertEqual(result["curve"]["values"][1], 44)

    def test_copy_limit_counts_all_printings(self):
        a = self.entry("Lightning Bolt", 3, card_id=1)
        b = self.entry("Lightning Bolt", 2, card_id=2, board="side")
        result = decks.analyze("modern", [a, b])
        self.assertIn("Up to 4 copies of each card", self.failing(result))

    def test_commander_rules(self):
        commander = self.entry("Sheoldred", board="commander", type_line="Legendary Creature — Praetor",
                               color_identity="B", mana_cost="{2}{B}{B}", cmc=4)
        off_color = self.entry("Counterspell", color_identity="U", mana_cost="{U}{U}")
        double = self.entry("Sol Ring", 2)
        result = decks.analyze("commander", [commander, off_color, double])
        failing = self.failing(result)
        self.assertIn("4 of 100 cards (including commander)", failing)
        self.assertIn("Singleton: one copy of each card", failing)
        self.assertIn("Every card fits the commander's colors (B)", failing)
        self.assertEqual(result["identity"], "UB")
        self.assertEqual({s["color"]: s["count"] for s in result["symbols"]}, {"U": 2, "B": 2})

    def test_ownership_check_skips_basic_lands(self):
        result = decks.analyze("casual", [
            self.entry("Island", 20, is_basic=True, owned=0, type_line="Basic Land — Island"),
            self.entry("Ponder", 3, owned=1),
        ])
        bad = [c for c in result["checks"] if not c["ok"]]
        self.assertEqual(len(bad), 1)
        self.assertIn("Ponder (deck uses 3, you own 1)", bad[0]["detail"])

    def test_banned_card_and_export(self):
        result = decks.analyze("modern", [self.entry("Sol Ring", legalities={"modern": "not_legal"})])
        self.assertIn("Every card is legal in Modern", self.failing(result))
        self.assertEqual(result["export"], "Deck\n1 Sol Ring (TST) 1")


class ScryfallParseTests(TestCase):
    def test_double_faced_card(self):
        raw = {
            "id": "abc", "name": "Delver of Secrets // Insectile Aberration", "set": "isd", "set_name": "Innistrad",
            "collector_number": "51", "rarity": "common", "cmc": 1, "color_identity": ["U"],
            "type_line": "Creature — Human Wizard // Creature — Human Insect", "legalities": {"modern": "legal"},
            "card_faces": [
                {"mana_cost": "{U}", "oracle_text": "At the beginning of your upkeep, look at the top card.",
                 "colors": ["U"], "power": "1", "toughness": "1"},
                {"mana_cost": "", "oracle_text": "Flying", "colors": ["U"], "power": "3", "toughness": "2"},
            ],
            "prices": {"usd": "0.25"},
        }
        card = scryfall.parse_card(raw)
        self.assertEqual(card["mana_cost"], "{U}")
        self.assertEqual(card["oracle_text"], "At the beginning of your upkeep, look at the top card.\n//\nFlying")
        self.assertEqual((card["power"], card["toughness"], card["colors"]), ("1", "1", "U"))
        self.assertEqual(card["legalities"], {"modern": "legal"})


class DeckViewTests(TestCase):
    def setUp(self):
        self.sheoldred = make_card("Sheoldred, the Apocalypse", colors="B", color_identity="B",
                                   type_line="Legendary Creature — Phyrexian Praetor", mana_cost="{2}{B}{B}", cmc=4)
        self.bolt = make_card("Lightning Bolt", colors="R", color_identity="R", type_line="Instant",
                              mana_cost="{R}", cmc=1, oracle_text="Lightning Bolt deals 3 damage to any target.")
        self.island = make_card("Island", type_line="Basic Land — Island", usd=0.1)
        CollectionItem.objects.create(card=self.sheoldred, quantity=1)
        CollectionItem.objects.create(card=self.bolt, quantity=2, finish="nonfoil")
        CollectionItem.objects.create(card=self.bolt, quantity=1, finish="foil")
        self.deck = Deck.objects.create(name="Test", format="modern")

    def add(self, card, qty, board="main"):
        return self.client.post(reverse("collection:deck_add", args=[self.deck.pk]),
                                {"card_id": card.pk, "quantity": qty, "board": board})

    def test_pages_render(self):
        self.add(self.bolt, 2)
        for url in [reverse("collection:dashboard"), reverse("collection:cards"), reverse("collection:decks"),
                    reverse("collection:deck", args=[self.deck.pk]),
                    reverse("collection:cards") + "?q=o:damage+t:instant",
                    reverse("collection:deck", args=[self.deck.pk]) + "?q=c:r"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_add_is_capped_at_copies_owned(self):
        self.add(self.bolt, 10)  # you own 3 across finishes
        self.assertEqual(DeckCard.objects.get(deck=self.deck, card=self.bolt).quantity, 3)
        self.add(self.bolt, 1, board="side")  # none left
        self.assertFalse(DeckCard.objects.filter(deck=self.deck, board="side").exists())

    def test_unowned_card_cannot_be_added_but_basics_can(self):
        unowned = make_card("Counterspell", type_line="Instant")
        self.add(unowned, 1)
        self.assertFalse(DeckCard.objects.filter(card=unowned).exists())
        self.add(self.island, 20)
        self.assertEqual(DeckCard.objects.get(card=self.island).quantity, 20)

    def test_basic_lands_form_reuses_saved_printing(self):
        with mock.patch.object(scryfall, "named") as named:
            self.client.post(reverse("collection:deck_add_basics", args=[self.deck.pk]), {"island": 12})
            named.assert_not_called()
        self.assertEqual(DeckCard.objects.get(card=self.island).quantity, 12)

    def test_moving_and_removing_entries(self):
        self.add(self.bolt, 2)
        entry = DeckCard.objects.get(deck=self.deck, card=self.bolt)
        url = reverse("collection:deck_entry_update", args=[entry.pk])
        self.client.post(url, {"quantity": 2, "board": "side"})
        entry.refresh_from_db()
        self.assertEqual(entry.board, "side")
        self.client.post(url, {"quantity": 9, "board": "side"})  # capped at 3 owned
        entry.refresh_from_db()
        self.assertEqual(entry.quantity, 3)
        self.client.post(url, {"quantity": 0})
        self.assertFalse(DeckCard.objects.filter(pk=entry.pk).exists())

    def test_delete_needs_confirmation(self):
        url = reverse("collection:deck_delete", args=[self.deck.pk])
        self.client.post(url)
        self.assertTrue(Deck.objects.filter(pk=self.deck.pk).exists())
        self.client.post(url, {"confirm": "yes"})
        self.assertFalse(Deck.objects.filter(pk=self.deck.pk).exists())
        self.assertTrue(CollectionItem.objects.filter(card=self.bolt).exists())

    def test_commander_fit_filter(self):
        deck = Deck.objects.create(name="Sheoldred", format="commander")
        DeckCard.objects.create(deck=deck, card=self.sheoldred, board="commander")
        page = self.client.get(reverse("collection:deck", args=[deck.pk]))
        self.assertNotContains(page, "Lightning Bolt</a>")  # red card hidden for a mono-black commander
        page = self.client.get(reverse("collection:deck", args=[deck.pk]) + "?fit=0")
        self.assertContains(page, "Lightning Bolt</a>")


# ------------------------------------------------------------------ Import

def fake_card(name, set_code, number, finishes=("nonfoil",), usd="1.00", foil=None):
    from decimal import Decimal
    return {
        "scryfall_id": f"{set_code}-{number}", "name": name, "set_code": set_code, "set_name": set_code.upper() + " set",
        "collector_number": number, "rarity": "common", "colors": "", "type_line": "Instant", "released_at": None,
        "image_url": "", "scryfall_uri": "", "usd": Decimal(usd) if usd else None,
        "usd_foil": Decimal(foil) if foil else None, "usd_etched": None, "mana_cost": "{R}", "cmc": 1.0,
        "oracle_text": "", "keywords": "", "power": "", "toughness": "", "loyalty": "", "color_identity": "",
        "legalities": {}, "finishes": list(finishes),
    }


CATALOG = [
    fake_card("Lightning Bolt", "2xm", "117", usd="1.85"),
    fake_card("Lightning Bolt", "m10", "146", usd="2.50"),
    fake_card("Sol Ring", "cmm", "410", finishes=("nonfoil", "foil"), usd="1.20", foil="6.20"),
    fake_card("Delver of Secrets // Insectile Aberration", "isd", "51", usd="0.30"),
    fake_card("Counterspell", "mh2", "267", usd="1.20"),
]


def fake_lookup(identifiers):
    """Behaves like scryfall.lookup against a tiny catalog."""
    found, missing = [], []
    for ident in identifiers:
        hit = None
        for c in CATALOG:
            if "collector_number" in ident:
                ok = c["set_code"] == ident["set"] and c["collector_number"] == ident["collector_number"]
            elif "set" in ident:
                ok = c["set_code"] == ident["set"] and importer.names_match(ident["name"], c["name"])
            else:
                ok = importer.names_match(ident["name"], c["name"])
            if ok:
                hit = c
                break
        (found if hit else missing).append(hit or ident)
    return found, missing


def fake_get_many(ids):
    return [c for c in CATALOG if c["scryfall_id"] in ids], []


class ImportParseTests(TestCase):
    def test_line_formats(self):
        lines, problems = importer.parse_lines(
            "Deck\n"
            "4 Lightning Bolt (2XM) 117\n"
            "4x Counterspell [MH2]\n"
            "Sol Ring\n"
            "1 Sol Ring (CMM) 410 *F*\n"
            "2 Sol Ring (CMM) 410 foil\n"
            "1 Sol Ring (CMM) 410 *E*\n"
            "# a comment\n"
            "\n"
            "Sideboard\n"
            "0 Lightning Bolt\n"
        )
        got = [(l.quantity, l.name, l.set_code, l.collector_number, l.finish) for l in lines]
        self.assertEqual(got, [
            (4, "Lightning Bolt", "2xm", "117", "nonfoil"),
            (4, "Counterspell", "mh2", "", "nonfoil"),
            (1, "Sol Ring", "", "", "nonfoil"),
            (1, "Sol Ring", "cmm", "410", "foil"),
            (2, "Sol Ring", "cmm", "410", "foil"),
            (1, "Sol Ring", "cmm", "410", "etched"),
        ])
        self.assertEqual([p.number for p in problems], [11])  # quantity 0

    def test_names_match_handles_faces_and_apostrophes(self):
        self.assertTrue(importer.names_match("delver of secrets", "Delver of Secrets // Insectile Aberration"))
        self.assertTrue(importer.names_match("Urza’s Saga", "Urza's Saga"))
        self.assertFalse(importer.names_match("Delver", "Delver of Secrets // Insectile Aberration"))


class ImportResolveTests(TestCase):
    def test_matches_problems_and_merging(self):
        result = importer.resolve(
            "4 Lightning Bolt (2XM) 117\n"
            "1 Lightning Bolt (M10)\n"
            "1 Delver of Secrets\n"
            "2 Lightning Bolt (2XM) 117\n"
            "1 Counterspell (2XM) 117\n"        # wrong name for that number
            "1 Lightning Blot\n"                # typo
            "1 Sol Ring (CMM) 410 *F*\n"
            "1 Counterspell (MH2) 267 *F*\n",   # no foil printing in the catalog
            fake_lookup,
        )
        self.assertEqual([p.number for p in result.problems], [5, 6])
        self.assertIn("is Lightning Bolt, not \"Counterspell\"", result.problems[0].message)
        self.assertIn("no card named \"Lightning Blot\"", result.problems[1].message)

        by_line = {m.line.number: m for m in result.matches}
        self.assertEqual(by_line[2].card["collector_number"], "146")
        self.assertEqual(by_line[3].card["name"], "Delver of Secrets // Insectile Aberration")
        self.assertEqual((by_line[7].finish, str(by_line[7].price)), ("foil", "6.20"))
        self.assertEqual(by_line[8].finish, "nonfoil")
        self.assertIn("no foil version", by_line[8].note)

        items = {(i["id"], i["finish"]): i["quantity"] for i in result.items()}
        self.assertEqual(items[("2xm-117", "nonfoil")], 6)
        self.assertEqual(result.copies, 10)  # 4 + 1 + 1 + 2 + 1 + 1


@mock.patch.object(scryfall, "lookup", fake_lookup)
@mock.patch.object(scryfall, "get_many", fake_get_many)
class ImportViewTests(TestCase):
    def test_check_then_add(self):
        url = reverse("collection:import")
        self.assertEqual(self.client.get(url).status_code, 200)

        page = self.client.post(url, {"action": "check", "cards": "4 Lightning Bolt (2XM) 117\n1 Sol Ring (CMM) 410 *F*\nNope Card"})
        self.assertContains(page, "Lines to fix (1)")
        self.assertContains(page, "Add 5 cards to my library")
        self.assertEqual(CollectionItem.objects.count(), 0)  # checking adds nothing

        items = page.context["items_json"]
        done = self.client.post(url, {"action": "add", "items": items})
        self.assertRedirects(done, reverse("collection:cards"))
        self.assertEqual(CollectionItem.objects.get(card__scryfall_id="2xm-117").quantity, 4)
        self.assertEqual(CollectionItem.objects.get(card__scryfall_id="cmm-410", finish="foil").quantity, 1)

        self.client.post(url, {"action": "add", "items": items})  # importing again adds more copies
        self.assertEqual(CollectionItem.objects.get(card__scryfall_id="2xm-117").quantity, 8)

    def test_bad_items_are_rejected(self):
        self.client.post(reverse("collection:import"), {"action": "add", "items": "not json"})
        self.assertEqual(CollectionItem.objects.count(), 0)

    def test_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cards.txt"
            path.write_text("4 Lightning Bolt (2XM) 117\n2 Counterspell\n", encoding="utf-8")
            out = StringIO()
            call_command("import_cards", str(path), "--check", stdout=out)
            self.assertIn("Nothing was added", out.getvalue())
            self.assertEqual(CollectionItem.objects.count(), 0)
            call_command("import_cards", str(path), stdout=StringIO())
            self.assertEqual(sum(CollectionItem.objects.values_list("quantity", flat=True)), 6)


# ------------------------------------------------------------------ Commander singleton

class CommanderSingletonTests(TestCase):
    def setUp(self):
        self.ring_a = make_card("Sol Ring", scryfall_id="ring-a", set_code="cmm")
        self.ring_b = make_card("Sol Ring", scryfall_id="ring-b", set_code="c21")
        self.rats = make_card("Relentless Rats", colors="B", color_identity="B", type_line="Creature — Rat",
                              oracle_text="A deck can have any number of cards named Relentless Rats.")
        self.swamp = make_card("Swamp", type_line="Basic Land — Swamp")
        CollectionItem.objects.create(card=self.ring_a, quantity=2)
        CollectionItem.objects.create(card=self.ring_b, quantity=1)
        CollectionItem.objects.create(card=self.rats, quantity=5)
        self.deck = Deck.objects.create(name="EDH", format="commander")

    def add(self, card, qty, deck=None):
        deck = deck or self.deck
        return self.client.post(reverse("collection:deck_add", args=[deck.pk]),
                                {"card_id": card.pk, "quantity": qty, "board": "main"}, follow=True)

    def count(self, name, deck=None):
        return sum(DeckCard.objects.filter(deck=deck or self.deck, card__name=name).values_list("quantity", flat=True))

    def test_only_one_copy_per_name(self):
        self.add(self.ring_a, 3)
        self.assertEqual(self.count("Sol Ring"), 1)
        page = self.add(self.ring_a, 1)
        self.assertContains(page, "Sol Ring is already in this deck")
        self.add(self.ring_b, 1)  # a different printing of the same card
        self.assertEqual(self.count("Sol Ring"), 1)

    def test_basics_and_any_number_cards_are_exempt(self):
        self.add(self.swamp, 30)
        self.add(self.rats, 5)
        self.assertEqual(self.count("Swamp"), 30)
        self.assertEqual(self.count("Relentless Rats"), 5)

    def test_editing_quantity_is_capped(self):
        self.add(self.ring_a, 1)
        entry = DeckCard.objects.get(deck=self.deck, card=self.ring_a)
        self.client.post(reverse("collection:deck_entry_update", args=[entry.pk]), {"quantity": 2, "board": "main"})
        entry.refresh_from_db()
        self.assertEqual(entry.quantity, 1)

    def test_other_formats_still_allow_multiples(self):
        modern = Deck.objects.create(name="Modern", format="modern")
        self.add(self.ring_a, 2, deck=modern)
        self.assertEqual(self.count("Sol Ring", deck=modern), 2)

    def test_library_panel_shows_card_is_in_deck(self):
        self.add(self.ring_a, 1)
        page = self.client.get(reverse("collection:deck", args=[self.deck.pk]))
        self.assertContains(page, "In deck · one copy allowed", count=2)  # both Sol Ring printings


class CompositionTests(TestCase):
    def entry(self, type_line, qty=1, cmc=0):
        return {"type_line": type_line, "quantity": qty, "cmc": cmc}

    def test_cmc_by_type(self):
        playing = [
            self.entry("Creature — Elf", 2, cmc=1),
            self.entry("Creature — Dragon", 1, cmc=6),
            self.entry("Instant", 1, cmc=2),
            self.entry("Sorcery", 1, cmc=9),       # lands in the 7+ column
            self.entry("Basic Land — Forest", 10),
        ]
        c = decks.composition(playing, 15)
        by_label = {r["label"]: r for r in c["rows"]}
        self.assertEqual(by_label["Creatures"]["avg_cmc"], 2.67)   # (1+1+6) / 3
        self.assertIsNone(by_label["Lands"]["avg_cmc"])
        self.assertEqual(c["avg_cmc"], 3.8)                        # (1+1+6+2+9) / 5 spells
        self.assertEqual([g["label"] for g in c["grid"]], ["Creatures", "Instants", "Sorceries"])
        self.assertEqual([cell["count"] for cell in c["grid"][0]["cells"]], [0, 2, 0, 0, 0, 0, 1, 0])
        self.assertEqual(c["grid_totals"], [0, 2, 1, 0, 0, 0, 1, 1])

    def test_split_by_main_type(self):
        playing = [
            self.entry("Legendary Creature — Phyrexian Praetor"),
            self.entry("Artifact Creature — Golem", 2),
            self.entry("Instant", 3),
            self.entry("Sorcery", 2),
            self.entry("Artifact"),
            self.entry("Enchantment Creature — God"),
            self.entry("Artifact Land"),
            self.entry("Basic Land — Swamp", 10),
        ]
        c = decks.composition(playing, 21)
        rows = {r["label"]: (r["count"], r["pct"]) for r in c["rows"]}
        self.assertEqual(rows, {
            "Creatures": (4, 19.0), "Instants": (3, 14.3), "Sorceries": (2, 9.5),
            "Artifacts": (1, 4.8), "Lands": (11, 52.4),
        })
        self.assertEqual((c["spells"], c["lands"]), (10, 11))
        self.assertEqual({m["label"]: m["count"] for m in c["multi"]},
                         {"Artifact Creature": 2, "Enchantment Creature": 1, "Artifact Land": 1})
        self.assertEqual([r["label"] for r in c["rows"]][0], "Creatures")  # deck-list order

    def test_card_types(self):
        self.assertEqual(decks.card_types("Legendary Artifact Creature — Golem"), ["Artifact", "Creature"])
        self.assertEqual(decks.card_types("Creature — Human Wizard // Creature — Human Insect"), ["Creature"])
        self.assertEqual(decks.card_types("Kindred Instant — Elf"), ["Kindred", "Instant"])
