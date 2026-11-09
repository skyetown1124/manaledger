"""Run with:  python manage.py test collection"""
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from io import StringIO
from pathlib import Path
import tempfile

from django.core.management import call_command

from . import decks, importer, scryfall, search
from .models import Card, CollectionItem, Deck, DeckCard, DeckVersion, DeletedDeck, LinkedProfile, WantItem

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
        self.client.post(url, {"quantity": 9, "board": "side"})  # Modern allows 4; owning 3 doesn't cap it
        entry.refresh_from_db()
        self.assertEqual(entry.quantity, 4)
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
        self.assertContains(page, "This deck already has 1 Sol Ring")
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


# ------------------------------------------------------------------ Format rules

def rules_card(name, type_line="Legendary Creature — Human", text="", identity=""):
    return {"name": name, "type_line": type_line, "oracle_text": text, "quantity": 1,
            "details_loaded": True, "color_identity": identity}


class FormatRuleTests(TestCase):
    def test_copy_limits(self):
        self.assertEqual(decks.copy_limit("commander", "Artifact", ""), 1)
        self.assertEqual(decks.copy_limit("modern", "Instant", ""), 4)
        self.assertIsNone(decks.copy_limit("commander", "Basic Land — Swamp", ""))
        self.assertIsNone(decks.copy_limit("commander", "Basic Snow Land — Island", ""))
        self.assertIsNone(decks.copy_limit("casual", "Instant", ""))
        self.assertIsNone(decks.copy_limit("commander", "Creature — Rat",
                                           "A deck can have any number of cards named Relentless Rats."))
        self.assertEqual(decks.copy_limit("commander", "Creature — Dwarf",
                                          "A deck can have up to seven cards named Seven Dwarves."), 7)
        self.assertEqual(decks.copy_limit("vintage", "Artifact", "", {"vintage": "restricted"}), 1)
        self.assertEqual(decks.copy_limit("legacy", "Artifact", "", {"vintage": "restricted", "legacy": "legal"}), 4)

    def test_legality(self):
        self.assertEqual(decks.legality_problem("commander", "Mana Crypt", {"commander": "banned"}),
                         "Mana Crypt is banned in Commander.")
        self.assertIn("isn't legal", decks.legality_problem("standard", "Sol Ring", {"standard": "not_legal"}))
        self.assertIsNone(decks.legality_problem("vintage", "Sol Ring", {"vintage": "restricted"}))
        self.assertIsNone(decks.legality_problem("casual", "Mana Crypt", {"casual": "banned"}))
        self.assertIsNone(decks.legality_problem("commander", "New Card", {}))  # not loaded yet

    def test_single_commander(self):
        self.assertIsNone(decks.commander_problem([rules_card("Sheoldred")]))
        self.assertIn("isn't a legendary creature", decks.commander_problem([rules_card("Bear", "Creature — Bear")]))
        self.assertIsNone(decks.commander_problem([rules_card(
            "Teferi, Temporal Archmage", "Legendary Planeswalker — Teferi", "Teferi, Temporal Archmage can be your commander.")]))
        self.assertIn("Background", decks.commander_problem([rules_card("Cloakwood Hermit", "Legendary Enchantment — Background")]))
        self.assertIn("one commander", decks.commander_problem([dict(rules_card("A"), quantity=2)]))
        self.assertIn("Add a legendary creature", decks.commander_problem([]))

    def test_pairs(self):
        partner = "Partner (You can have two commanders if both have partner.)"
        a, b = rules_card("Thrasios", text=partner), rules_card("Tymna", text="Lifelink\n" + partner)
        self.assertIsNone(decks.commander_problem([a, b]))
        self.assertIn("can't be commanders together", decks.commander_problem([a, rules_card("Sheoldred")]))

        pir = rules_card("Pir, Imaginative Rascal", text="Partner with Toothy, Imaginary Friend (When this creature enters...)")
        toothy = rules_card("Toothy, Imaginary Friend", text="Partner with Pir, Imaginative Rascal (When this creature enters...)")
        self.assertIsNone(decks.commander_problem([pir, toothy]))
        self.assertIn("can only partner with Toothy", decks.commander_problem([pir, a]))

        f1 = rules_card("Will", text="Friends forever (You can have two commanders if both have friends forever.)")
        f2 = rules_card("Mike", text="Friends forever (You can have two commanders if both have friends forever.)")
        self.assertIsNone(decks.commander_problem([f1, f2]))

        chooser = rules_card("Wilson", text="Choose a Background (You can have a Background as a second commander.)")
        bg = rules_card("Cloakwood Hermit", "Legendary Enchantment — Background")
        self.assertIsNone(decks.commander_problem([chooser, bg]))
        self.assertIsNotNone(decks.commander_problem([rules_card("Sheoldred"), bg]))

        companion = rules_card("Rose Tyler", text="Doctor's companion (You can have two commanders if the other is the Doctor.)")
        doctor = rules_card("The Tenth Doctor", "Legendary Creature — Time Lord Doctor")
        self.assertIsNone(decks.commander_problem([companion, doctor]))

        s1 = rules_card("Ellie", text="Partner—Survivors (You can have two commanders if both have this ability.)")
        s2 = rules_card("Joel", text="Partner—Survivors (You can have two commanders if both have this ability.)")
        self.assertIsNone(decks.commander_problem([s1, s2]))

    def test_checklist_separates_banned(self):
        e = dict(card_id=1, name="Mana Crypt", set_code="x", collector_number="1", board="main", quantity=1,
                 type_line="Artifact", mana_cost="{0}", cmc=0, colors="", color_identity="", oracle_text="",
                 legalities={"commander": "banned"}, details_loaded=True, price=1.0, owned=1, is_basic=False)
        checks = {c["label"]: c for c in decks.analyze("commander", [e])["checks"]}
        self.assertFalse(checks["No cards banned in Commander"]["ok"])
        self.assertIn("Mana Crypt", checks["No cards banned in Commander"]["detail"])

    def test_vintage_restricted_copy_check(self):
        e = dict(card_id=1, name="Sol Ring", set_code="x", collector_number="1", board="main", quantity=2,
                 type_line="Artifact", mana_cost="{1}", cmc=1, colors="", color_identity="", oracle_text="",
                 legalities={"vintage": "restricted"}, details_loaded=True, price=1.0, owned=2, is_basic=False)
        checks = {c["label"]: c for c in decks.analyze("vintage", [e])["checks"]}
        bad = checks["Up to 4 copies of each card, 1 of each restricted card"]
        self.assertFalse(bad["ok"])
        self.assertIn("Sol Ring (2 of 1)", bad["detail"])


class DeckRuleEnforcementTests(TestCase):
    """The deck builder refuses changes the format doesn't allow."""

    def setUp(self):
        partner = "Partner (You can have two commanders if both have partner.)"
        self.sheoldred = make_card("Sheoldred", colors="B", color_identity="B", type_line="Legendary Creature — Praetor")
        self.thrasios = make_card("Thrasios", colors="GU", color_identity="GU",
                                  type_line="Legendary Creature — Merfolk", oracle_text=partner)
        self.tymna = make_card("Tymna", colors="WB", color_identity="WB",
                               type_line="Legendary Creature — Human", oracle_text=partner)
        self.crypt = make_card("Mana Crypt", legalities={**LEGAL_ALL, "commander": "banned", "vintage": "restricted"})
        self.bolt = make_card("Lightning Bolt", colors="R", color_identity="R", type_line="Instant")
        self.ponder = make_card("Ponder", colors="U", color_identity="U", type_line="Sorcery")
        self.island = make_card("Island", type_line="Basic Land — Island", color_identity="U")
        for c in (self.sheoldred, self.thrasios, self.tymna, self.crypt, self.bolt, self.ponder):
            CollectionItem.objects.create(card=c, quantity=6)
        self.edh = Deck.objects.create(name="EDH", format="commander")

    def add(self, deck, card, qty=1, board="main"):
        return self.client.post(reverse("collection:deck_add", args=[deck.pk]),
                                {"card_id": card.pk, "quantity": qty, "board": board}, follow=True)

    def in_deck(self, deck, card):
        return sum(DeckCard.objects.filter(deck=deck, card=card).values_list("quantity", flat=True))

    def test_banned_card_is_refused(self):
        page = self.add(self.edh, self.crypt)
        self.assertContains(page, "Mana Crypt is banned in Commander.")
        self.assertEqual(self.in_deck(self.edh, self.crypt), 0)

    def test_commander_slot_rules(self):
        self.add(self.edh, self.sheoldred, board="commander")
        page = self.add(self.edh, self.thrasios, board="commander")  # no partner on Sheoldred
        self.assertContains(page, "can&#x27;t be commanders together")
        self.assertEqual(self.in_deck(self.edh, self.thrasios), 0)

        pair = Deck.objects.create(name="Partners", format="commander")
        self.add(pair, self.thrasios, board="commander")
        self.add(pair, self.tymna, board="commander")
        self.assertEqual(DeckCard.objects.filter(deck=pair, board="commander").count(), 2)
        page = self.add(pair, self.sheoldred, board="commander")
        self.assertContains(page, "already has two commanders")

    def test_color_identity_is_enforced(self):
        self.add(self.edh, self.sheoldred, board="commander")
        page = self.add(self.edh, self.bolt)
        self.assertContains(page, "outside your commander")
        self.assertEqual(self.in_deck(self.edh, self.bolt), 0)
        self.client.post(reverse("collection:deck_add_basics", args=[self.edh.pk]), {"island": 10})
        self.assertEqual(self.in_deck(self.edh, self.island), 0)  # Islands are blue

    def test_four_copy_limit_and_vintage_restricted(self):
        modern = Deck.objects.create(name="Modern", format="modern")
        self.add(modern, self.bolt, qty=6)
        self.assertEqual(self.in_deck(modern, self.bolt), 4)
        vintage = Deck.objects.create(name="Vintage", format="vintage")
        self.add(vintage, self.crypt, qty=3)
        self.assertEqual(self.in_deck(vintage, self.crypt), 1)

    def test_library_panel_labels(self):
        self.add(self.edh, self.sheoldred, board="commander")
        page = self.client.get(reverse("collection:deck", args=[self.edh.pk]) + "?fit=0")
        self.assertContains(page, "Banned in Commander")
        self.assertContains(page, "Outside your commander&#x27;s colors")


# ------------------------------------------------------------------ Photo import (OCR)

import io as _io
import os as _os
import unittest as _unittest

from django.core.files.uploadedfile import SimpleUploadedFile

from . import ocr


def L(text, top, left=100, height=20):
    return ocr.TextLine(text, left, top, left + 10 * len(text), top + height)


def ocr_card(name, set_code, number):
    return {"name": name, "set_code": set_code, "collector_number": number}


class FakeScryfallForPhotos:
    CARDS = [ocr_card("Sheoldred, the Apocalypse", "dmu", "107"), ocr_card("Look Alike", "omu", "107"),
             ocr_card("Lightning Bolt", "2xm", "117"), ocr_card("Llanowar Elves", "dom", "168")]

    def sets(self):
        return [{"code": "dmu", "size": 281}, {"code": "omu", "size": 99}, {"code": "2xm", "size": 332},
                {"code": "dom", "size": 269}]

    def lookup(self, ids):
        return [c for c in self.CARDS for i in ids
                if c["set_code"] == i["set"] and c["collector_number"] == i["collector_number"]], []

    def fuzzy(self, name):
        best = max(self.CARDS, key=lambda c: ocr.similarity(name, c["name"]))
        return best if ocr.similarity(name, best["name"]) > 0.6 else None

    def search(self, query):
        return [c for c in self.CARDS if c["name"] in query and f'cn:{c["collector_number"]}' in query]


class OcrParsingTests(TestCase):
    def test_anchors_from_collector_lines(self):
        lines = [L("Sheoldred, the Apocalypse", 10, height=40), L("4/5", 900, left=500),
                 L("107/281 M", 950), L("OMU • EN Chris Rahn", 975)]
        found = ocr.anchors(lines)
        self.assertEqual([(a.number, a.total, a.rarity, a.set_text) for a in found], [("107", "281", "M", "OMU")])

    def test_new_frame_number_without_total(self):
        found = ocr.anchors([L("0196 M", 950), L("ONE • EN Marta Nael", 975)])
        self.assertEqual([(a.number, a.set_text) for a in found], [("196", "ONE")])

    def test_set_candidates_fix_misreads(self):
        known = FakeScryfallForPhotos().sets()
        self.assertEqual(ocr.set_candidates("DMU", "", known)[0], "dmu")
        self.assertIn("dmu", ocr.set_candidates("OMU", "", known))       # O misread for D
        self.assertIn("dmu", ocr.set_candidates("NMILE", "281", known))  # unreadable, but 281 cards
        self.assertEqual(ocr.set_candidates("", "", known), [])

    def test_name_candidates(self):
        lines = [L("Sheoldred, the Apocalypse", 10, height=40), L("Legendary Creature — Phyrexian Praetor", 400),
                 L("Whenever you draw a card, you gain 2 life.", 500), L("DMU • EN Chris Rahn", 975),
                 L("Lightning Bolt", 20, left=900, height=40)]
        self.assertEqual(ocr.name_candidates(lines), ["Sheoldred, the Apocalypse", "Lightning Bolt"])

    def test_similarity_tolerates_ocr_noise(self):
        self.assertGreater(ocr.similarity("Sheo1dred, the Apocalypse", "Sheoldred, the Apocalypse"), 0.9)
        self.assertGreater(ocr.similarity("Delver of Secrets", "Delver of Secrets // Insectile Aberration"), 0.99)
        self.assertLess(ocr.similarity("Whenever", "Sheoldred, the Apocalypse"), 0.5)


class OcrIdentifyTests(TestCase):
    def identify(self, *photo_lines):
        photos = [(f"photo{i}.jpg", lines) for i, lines in enumerate(photo_lines, 1)]
        return ocr.identify(photos, lambda lines: (lines, (1000, 1000)), FakeScryfallForPhotos())

    def test_number_plus_name_picks_the_right_printing(self):
        result = self.identify([L("Sheoldred, the Apocalypse", 10, height=40), L("107/281 M", 950),
                                L("OMU • EN Chris Rahn", 975)])[0]
        self.assertEqual([(c["set_code"], c["collector_number"]) for c in result.cards], [("dmu", "107")])
        self.assertEqual(result.message, "Found 1 card.")

    def test_old_card_by_name_and_unreadable_photo(self):
        old, blank = self.identify([L("Llanowar Elves", 10, height=40)], [L("zzkq", 10)])
        self.assertEqual(old.cards[0]["name"], "Llanowar Elves")
        self.assertIn("default printing", old.message)
        self.assertEqual(blank.cards, [])
        self.assertIn("Couldn't read a card", blank.message)

    def test_list_text_counts_duplicates(self):
        results = self.identify([L("Lightning Bolt", 10, height=40), L("117/332 U", 950), L("2XM • EN", 975)],
                                [L("Lightning Bolt", 10, height=40), L("117/332 U", 950), L("2XM • EN", 975)])
        self.assertEqual(ocr.to_list_text(results), "2 Lightning Bolt (2XM) 117")


FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"


@_unittest.skipUnless(ocr.status()[0] and _os.path.exists(FONT), "needs Tesseract and the DejaVu fonts")
class OcrRealImageTests(TestCase):
    """Runs the real Tesseract OCR on a generated card-like image."""

    def make_photo(self):
        from PIL import Image, ImageDraw, ImageFont
        big = ImageFont.truetype(FONT, 34)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 15)
        img = Image.new("RGB", (750, 1000), (120, 100, 80))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((60, 60, 690, 940), 18, fill=(30, 30, 30))
        d.rounded_rectangle((82, 82, 668, 880), 18, fill=(214, 206, 190))
        d.rectangle((100, 100, 650, 152), fill=(236, 230, 214))
        d.text((112, 108), "Sheoldred, the Apocalypse", font=big, fill=(10, 10, 10))
        d.text((104, 866), "107/281 M", font=small, fill=(235, 235, 235))
        d.text((104, 886), "DMU • EN  Chris Rahn", font=small, fill=(235, 235, 235))
        return img

    def test_reads_name_and_collector_info(self):
        result = ocr.identify([("card.jpg", self.make_photo())], ocr.read_lines, FakeScryfallForPhotos())[0]
        self.assertEqual([(c["name"], c["set_code"]) for c in result.cards], [("Sheoldred, the Apocalypse", "dmu")])


class PhotoUploadViewTests(TestCase):
    def png(self):
        from PIL import Image
        buf = _io.BytesIO()
        Image.new("RGB", (40, 40), "white").save(buf, "PNG")
        return SimpleUploadedFile("cards.png", buf.getvalue(), content_type="image/png")

    @mock.patch.object(scryfall, "lookup", fake_lookup)
    @mock.patch.object(ocr, "status", lambda: (True, ""))
    def test_photos_fill_the_list_and_run_the_check(self):
        found = ocr.PhotoResult("cards.png", cards=[{"name": "Lightning Bolt", "set_code": "2xm", "collector_number": "117"}],
                                message="Found 1 card.")
        with mock.patch.object(ocr, "identify", return_value=[found]):
            page = self.client.post(reverse("collection:import"),
                                    {"action": "photos", "cards": "2 Counterspell", "photos": [self.png()]})
        self.assertEqual(page.context["text"], "2 Counterspell\n1 Lightning Bolt (2XM) 117")
        self.assertContains(page, "What was read")
        self.assertContains(page, "Add 3 cards to my library")
        self.assertEqual(CollectionItem.objects.count(), 0)

    @mock.patch.object(ocr, "status", lambda: (True, ""))
    def test_non_image_files_are_skipped(self):
        bad = SimpleUploadedFile("notes.txt", b"hello", content_type="text/plain")
        page = self.client.post(reverse("collection:import"), {"action": "photos", "photos": [bad]})
        self.assertContains(page, "isn&#x27;t a photo the app can open")

    @mock.patch.object(ocr, "status", lambda: (False, "Photo import needs the free Tesseract OCR program."))
    def test_explains_when_tesseract_is_missing(self):
        page = self.client.get(reverse("collection:import"))
        self.assertContains(page, "Photo import needs the free Tesseract OCR program.")
        self.assertNotContains(page, 'id="photo-form"')


# ------------------------------------------------------------------ Playtest

class PlaytestViewTests(TestCase):
    def test_deck_data_for_the_table(self):
        commander = make_card("Sheoldred", colors="B", color_identity="B", type_line="Legendary Creature — Praetor",
                              image_url="https://cards.scryfall.io/normal/front/x.jpg")
        swamp = make_card("Swamp", type_line="Basic Land — Swamp")
        push = make_card("Fatal Push", type_line="Instant")
        side = make_card("Duress", type_line="Sorcery")
        deck = Deck.objects.create(name="Test", format="commander")
        DeckCard.objects.create(deck=deck, card=commander, board="commander")
        DeckCard.objects.create(deck=deck, card=swamp, board="main", quantity=30)
        DeckCard.objects.create(deck=deck, card=push, board="main")
        DeckCard.objects.create(deck=deck, card=side, board="side")

        page = self.client.get(reverse("collection:deck_playtest", args=[deck.pk]))
        self.assertEqual(page.status_code, 200)
        data = page.context["playtest"]
        self.assertEqual(len(data["cards"]), 31)            # one entry per copy, sideboard left out
        self.assertEqual([c["name"] for c in data["commanders"]], ["Sheoldred"])
        self.assertTrue(data["commanders"][0]["commander"])
        self.assertEqual((data["life"], data["freeMulligan"]), (40, True))
        self.assertContains(page, 'id="playtest-data"')
        self.assertContains(page, "playtest-engine.js")

        self.assertContains(self.client.get(reverse("collection:deck", args=[deck.pk])), "Playtest")

    def test_empty_deck_explains_what_to_do(self):
        deck = Deck.objects.create(name="Empty", format="modern")
        page = self.client.get(reverse("collection:deck_playtest", args=[deck.pk]))
        self.assertContains(page, "Nothing to shuffle yet")
        self.assertEqual(page.context["playtest"]["life"], 20)


# ------------------------------------------------------------------ Commander brackets

from . import brackets, spellbook


def bentry(name, text="", gc=False, qty=1, board="main"):
    return {"name": name, "oracle_text": text, "game_changer": gc, "quantity": qty, "board": board}


class BracketRuleTests(TestCase):
    def test_game_changer_list_is_current(self):
        self.assertEqual(len(brackets.GAME_CHANGERS), 53)
        self.assertIn("Farewell", brackets.GAME_CHANGERS)            # added Feb 2026
        self.assertNotIn("Food Chain", brackets.GAME_CHANGERS)       # removed Oct 2025
        overlap = [n for n in brackets.IMPACT_CARDS if n in brackets.GAME_CHANGERS]
        self.assertEqual(overlap, [])                                # impact list is non-GC only

    def test_plain_deck_is_core(self):
        r = brackets.estimate([bentry("Sol Ring"), bentry("Arcane Signet"), bentry("Llanowar Elves")])
        self.assertEqual((r["bracket"], r["computed"], r["impact_score"]), (2, 1, 1))
        self.assertTrue(r["low_note"])
        # Exhibition is a choice the player makes
        self.assertEqual(brackets.estimate([bentry("Sol Ring")], intended=1)["bracket"], 1)

    def test_game_changer_counts(self):
        one = brackets.estimate([bentry("Rhystic Study")])                      # by name
        self.assertEqual(one["bracket"], 3)
        four = brackets.estimate([bentry(n) for n in ["Rhystic Study", "Cyclonic Rift", "Mana Vault"]]
                                 + [bentry("New Spoiler", gc=True)])               # by Scryfall flag
        self.assertEqual(four["bracket"], 4)
        self.assertIn("4 Game Changers", four["reasons"][0])

    def test_mass_land_denial_and_extra_turns(self):
        self.assertEqual(brackets.estimate([bentry("Armageddon")])["bracket"], 4)
        self.assertEqual(brackets.estimate([bentry("Mystery Card", "Destroy all lands.")])["bracket"], 4)
        self.assertEqual(brackets.estimate([bentry("Two Lands", "Each player sacrifices two lands.")])["bracket"], 2)
        one_turn = brackets.estimate([bentry("Time Warp", "Target player takes an extra turn after this one.")])
        self.assertEqual(one_turn["bracket"], 2)
        self.assertEqual(one_turn["extra_turns"], ["Time Warp"])
        two = brackets.estimate([bentry("Time Warp", "Target player takes an extra turn after this one."),
                                 bentry("Temporal Manipulation", "Take an extra turn after this one.")])
        self.assertEqual(two["bracket"], 4)

    def test_two_card_combos(self):
        fast = brackets.estimate([bentry("Isochron Scepter"), bentry("Dramatic Reversal")])
        self.assertEqual(fast["bracket"], 4)
        slow = brackets.estimate([bentry("Exquisite Blood"), bentry("Sanguine Bond")])
        self.assertEqual(slow["bracket"], 3)
        self.assertEqual(slow["combos"][0]["cards"], ["Exquisite Blood", "Sanguine Bond"])

    def test_impact_score_forces_a_higher_bracket(self):
        # No Game Changers, no combos, but packed with fast mana, tutors and free counters.
        names = ["Lotus Petal", "Mox Opal", "City of Traitors", "Dark Ritual", "Simian Spirit Guide",
                 "Demonic Consultation", "Grim Tutor", "Pact of Negation", "Grand Abolisher", "Necrodominance",
                 "Mystic Remora", "Esper Sentinel"]
        r = brackets.estimate([bentry(n) for n in names], intended=2)
        self.assertEqual(r["impact_score"], 44)   # 4+4+4+3+3+4+4+4+4+4+3+3
        self.assertEqual((r["computed"], r["bracket"]), (4, 4))
        self.assertTrue(r["plays_above"])
        self.assertIn("Impact score 44", r["reasons"][0])
        upgraded = brackets.estimate([bentry(n) for n in ["Mystic Remora", "Sylvan Library", "Eladamri's Call",
                                                           "Green Sun's Zenith", "Birds of Paradise", "Noble Hierarch",
                                                           "Swan Song"]])
        self.assertEqual((upgraded["impact_score"], upgraded["bracket"]), (18, 3))

    def test_intended_bracket(self):
        r = brackets.estimate([bentry("Rhystic Study")], intended=4)
        self.assertEqual((r["bracket"], r["plays_above"]), (4, False))      # built for a stronger table
        r = brackets.estimate([bentry("Rhystic Study")], intended=2)
        self.assertEqual((r["bracket"], r["plays_above"]), (3, True))       # forced up to 3

    def test_spellbook_tag_is_a_floor(self):
        r = brackets.estimate([bentry("Sol Ring")], spellbook={"bracketTag": "R", "combos": []})
        self.assertEqual(r["bracket"], 4)
        self.assertIn("Ruthless", r["reasons"][0])

    def test_names_with_accents_and_faces(self):
        r = brackets.estimate([bentry("Lim-Dul's Vault"), bentry("Birgi, God of Storytelling // Harnfel, Horn of Bounty")])
        self.assertEqual({c["name"] for c in r["impact_cards"]},
                         {"Lim-Dul's Vault", "Birgi, God of Storytelling // Harnfel, Horn of Bounty"})


class SpellbookTests(TestCase):
    SAMPLE = {
        "bracketTag": "S",
        "cards": [{"card": {"name": "Rhystic Study"}, "quantity": 1, "banned": False, "gameChanger": True,
                   "massLandDenial": False, "extraTurn": False}],
        "combos": [
            {"combo": {"id": "123-456", "uses": [{"card": {"name": "Exquisite Blood"}}, {"card": {"name": "Sanguine Bond"}}],
                       "produces": [{"feature": {"name": "Infinite lifeloss"}}]},
             "relevant": True, "definitelyTwoCard": True, "speed": 3},
            {"combo": {"id": "9", "uses": [{"card": {"name": "A"}}, {"card": {"name": "B"}}, {"card": {"name": "C"}}],
                       "produces": [{"feature": {"name": "Infinite mana"}}]},
             "relevant": True, "definitelyTwoCard": False, "speed": 4},
        ],
    }

    def test_summary(self):
        s = spellbook.summarize(self.SAMPLE)
        self.assertEqual(s["bracketTag"], "S")
        self.assertEqual(s["gameChangers"], ["Rhystic Study"])
        self.assertEqual(s["combos"][0]["cards"], ["Exquisite Blood", "Sanguine Bond"])   # two-card first
        self.assertTrue(s["combos"][0]["two_card"])
        self.assertEqual(s["combos"][0]["url"], "https://commanderspellbook.com/combo/123-456/")

    def test_check_button_saves_the_result(self):
        card = make_card("Sheoldred", type_line="Legendary Creature — Praetor")
        deck = Deck.objects.create(name="EDH", format="commander")
        DeckCard.objects.create(deck=deck, card=card, board="commander")
        with mock.patch.object(spellbook, "estimate", return_value=spellbook.summarize(self.SAMPLE)) as call:
            self.client.post(reverse("collection:deck_spellbook", args=[deck.pk]))
        call.assert_called_once_with(["Sheoldred"], [])
        deck.refresh_from_db()
        self.assertEqual(deck.spellbook_result["bracketTag"], "S")
        page = self.client.get(reverse("collection:deck", args=[deck.pk]))
        self.assertContains(page, "Exquisite Blood + Sanguine Bond")
        self.assertContains(page, "Spicy")

    def test_check_failure_is_explained(self):
        deck = Deck.objects.create(name="EDH", format="commander")
        DeckCard.objects.create(deck=deck, card=make_card("Sol Ring"), board="main")
        with mock.patch.object(spellbook, "estimate", side_effect=spellbook.SpellbookError("Couldn't reach Commander Spellbook.")):
            page = self.client.post(reverse("collection:deck_spellbook", args=[deck.pk]), follow=True)
        self.assertContains(page, "Combos weren&#x27;t checked.")


class BracketPageTests(TestCase):
    def test_deck_page_and_intended_bracket(self):
        deck = Deck.objects.create(name="EDH", format="commander")
        for name in ["Rhystic Study", "Mystic Remora", "Sol Ring"]:
            DeckCard.objects.create(deck=deck, card=make_card(name), board="main")
        page = self.client.get(reverse("collection:deck", args=[deck.pk]))
        self.assertContains(page, "Plays like Bracket 3: Upgraded")
        self.assertContains(page, "Mystic Remora")

        self.client.post(reverse("collection:deck_bracket", args=[deck.pk]), {"target_bracket": "2"})
        deck.refresh_from_db()
        self.assertEqual(deck.target_bracket, 2)
        page = self.client.get(reverse("collection:deck", args=[deck.pk]))
        self.assertContains(page, "You marked this deck as Bracket 2")
        self.assertContains(page, "Fits Bracket 2 (Core)")                  # checklist warning
        self.assertContains(self.client.get(reverse("collection:decks")), "(meant for 2)")

    def test_other_formats_have_no_bracket(self):
        deck = Deck.objects.create(name="Modern", format="modern")
        page = self.client.get(reverse("collection:deck", args=[deck.pk]))
        self.assertIsNone(page.context["bracket"])
        self.assertNotContains(page, 'id="bracket"')

    def test_scryfall_flag_is_saved(self):
        from .scryfall import parse_card
        card = parse_card({"id": "x", "name": "Farewell", "game_changer": True, "prices": {}})
        self.assertTrue(card["game_changer"])


# ------------------------------------------------------------------ Comprehensive Rules and rulings

from . import rules as rulesmod
from .models import GlossaryTerm, Rule, Ruling, RulesEdition

SAMPLE_RULES = "\r\n".join([
    "﻿Magic: The Gathering Comprehensive Rules", " ",
    "These rules are effective as of September 25, 2026.", " ",
    "Introduction", " ", "This document is the ultimate authority for Magic.", " ",
    "Contents", " ",
    "1. Game Concepts", "100. General", "7. Additional Rules", "702. Keyword Abilities", "Glossary", "Credits", " ",
    "1. Game Concepts", " ",
    "100. General", " ",
    "100.1. These Magic rules apply to any Magic game with two or more players.", " ",
    "100.1a A two-player game is a game that begins with only two players.", " ",
    "100.1b A multiplayer game is a game that begins with more than two players. See section 800.", " ",
    "7. Additional Rules", " ",
    "702. Keyword Abilities", " ",
    "702.2. Deathtouch", " ",
    "702.2a Deathtouch is a static ability.", " ",
    "702.2b A creature with toughness greater than 0 that's been dealt damage by a source with deathtouch "
    "since the last time state-based actions were checked is destroyed. See rule 704.5h.", " ",
    "Example: A 1/1 creature with deathtouch blocks a 5/5 creature. The 5/5 creature is destroyed.", " ",
    "702.19. Trample", " ",
    "702.19a Trample is a static ability that modifies the rules for assigning an attacking creature's combat damage.", " ",
    "Glossary", " ",
    "Commander", "A designated legendary creature card that leads a Commander deck. See rule 903.3.", " ",
    "Deathtouch", "A keyword ability that causes damage dealt by an object to be especially effective.",
    "See rule 702.2.", " ",
    "Credits", " ", "Magic: The Gathering Original Game Design: Richard Garfield",
])


class RulesParserTests(TestCase):
    def test_parse(self):
        parsed = rulesmod.parse(SAMPLE_RULES)
        self.assertEqual(parsed["effective"], "September 25, 2026")
        by_number = {r["number"]: r for r in parsed["rules"]}
        self.assertEqual([r["number"] for r in parsed["rules"]],
                         ["1", "100", "100.1", "100.1a", "100.1b", "7", "702", "702.2", "702.2a", "702.2b",
                          "702.19", "702.19a"])     # the contents list isn't counted twice
        self.assertEqual(by_number["702.2b"]["parent"], "702.2")
        self.assertEqual(by_number["702.2"]["parent"], "702")
        self.assertEqual(by_number["702"]["kind"], "section")
        self.assertEqual(by_number["702.2b"]["section"], "702")
        self.assertEqual(by_number["702.2b"]["examples"],
                         ["A 1/1 creature with deathtouch blocks a 5/5 creature. The 5/5 creature is destroyed."])
        self.assertEqual([g["term"] for g in parsed["glossary"]], ["Commander", "Deathtouch"])
        self.assertEqual(parsed["glossary"][1]["definition"],
                         "A keyword ability that causes damage dealt by an object to be especially effective.\nSee rule 702.2.")

    def test_decoding(self):
        self.assertEqual(rulesmod.decode("﻿rule’s".encode("utf-8")), "rule’s")
        self.assertEqual(rulesmod.decode("rule’s".encode("cp1252")), "rule’s")

    def test_not_a_rules_file(self):
        with self.assertRaises(rulesmod.RulesError):
            rulesmod.parse("Hello\nThis is not the rules.")

    def test_finds_latest_link(self):
        page = mock.Mock(text='<a href="https://media.wizards.com/2026/downloads/MagicCompRules%2020260925.pdf">PDF</a>'
                              '<a href="https://media.wizards.com/2026/downloads/MagicCompRules%2020260925.txt">TXT</a>'
                              '<a href="https://media.wizards.com/2025/downloads/MagicCompRules%2020250919.txt">old</a>')
        with mock.patch.object(rulesmod, "_get", return_value=page):
            self.assertEqual(rulesmod.latest_url(),
                             "https://media.wizards.com/2026/downloads/MagicCompRules%2020260925.txt")

    def test_finds_escaped_links(self):
        json_page = mock.Mock(text='{"href":"https:\\/\\/media.wizards.com\\/2026\\/downloads\\/MagicCompRules%2020260925.txt"}')
        unicode_page = mock.Mock(text='"https:\\u002F\\u002Fmedia.wizards.com\\u002F2026\\u002Fdownloads\\u002FMagicCompRules%2020260925.txt"')
        relative_page = mock.Mock(text='<a href="//media.wizards.com/2026/downloads/MagicCompRules%2020260925.txt">TXT</a>')
        want = "https://media.wizards.com/2026/downloads/MagicCompRules%2020260925.txt"
        for page in (json_page, unicode_page, relative_page):
            with mock.patch.object(rulesmod, "_get", return_value=page):
                self.assertEqual(rulesmod.latest_url(), want)

    def test_falls_back_to_known_link(self):
        with mock.patch.object(rulesmod, "_get", return_value=mock.Mock(text="<div id=app></div>")):
            self.assertEqual(rulesmod.latest_url(), rulesmod.KNOWN_TXT_URL)
        with mock.patch.object(rulesmod, "_get", side_effect=rulesmod.RulesError("blocked")):
            self.assertEqual(rulesmod.latest_url(), rulesmod.KNOWN_TXT_URL)

    def test_load_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rules.txt"
            path.write_bytes(SAMPLE_RULES.encode("utf-8-sig"))
            out = StringIO()
            call_command("load_rules", "--file", str(path), stdout=out)
        self.assertIn("effective September 25, 2026: 12 rules and 2 glossary terms", out.getvalue())
        self.assertEqual(Rule.objects.count(), 12)
        call_command("load_rules", "--file", str(path), stdout=StringIO()) if path.exists() else None
        self.assertEqual(RulesEdition.objects.count(), 1)


class RulesPageTests(TestCase):
    def setUp(self):
        rulesmod.save(rulesmod.parse(SAMPLE_RULES), "https://example.test/rules.txt")

    def test_contents_and_search(self):
        page = self.client.get(reverse("collection:rules"))
        self.assertContains(page, 'href="/rules/702/"')
        self.assertContains(page, "Keyword Abilities")
        self.assertContains(page, "effective September 25, 2026")

        page = self.client.get(reverse("collection:rules"), {"q": "deathtouch destroyed"})
        self.assertEqual(page.context["total"], 1)                      # only 702.2b has both words
        self.assertContains(page, "<mark>deathtouch</mark>")
        self.assertContains(page, 'href="/rules/704.5h/"')               # rule references become links
        self.assertEqual([t.term for t in page.context["terms"]], ["Deathtouch"])

        page = self.client.get(reverse("collection:rules"), {"q": '"static ability"'})
        self.assertEqual(page.context["total"], 2)

    def test_number_jumps_to_rule(self):
        self.assertRedirects(self.client.get(reverse("collection:rules"), {"q": "702.2"}),
                             reverse("collection:rule", args=["702.2"]))
        self.assertRedirects(self.client.get(reverse("collection:rule", args=["702.2b"])),
                             reverse("collection:rule", args=["702.2"]) + "#r702.2b", fetch_redirect_response=False)

    def test_rule_section_and_chapter_pages(self):
        page = self.client.get(reverse("collection:rule", args=["702.2"]))
        self.assertContains(page, 'id="r702.2b"')
        self.assertContains(page, "A 1/1 creature with deathtouch")
        self.assertEqual(page.context["next"], "702.19")
        section = self.client.get(reverse("collection:rule", args=["702"]))
        self.assertContains(section, "702.19a")
        chapter = self.client.get(reverse("collection:rule", args=["7"]))
        self.assertContains(chapter, "Keyword Abilities")

    def test_missing_rule_and_glossary(self):
        page = self.client.get(reverse("collection:rule", args=["999.9"]), follow=True)
        self.assertContains(page, "no rule 999.9")
        glossary = self.client.get(reverse("collection:glossary"))
        self.assertContains(glossary, 'id="letter-C"')
        self.assertContains(glossary, 'href="/rules/903.3/"')

    def test_load_button(self):
        with mock.patch.object(rulesmod, "load_latest", side_effect=rulesmod.RulesError("Couldn't reach magic.wizards.com.")):
            page = self.client.post(reverse("collection:rules_load"), follow=True)
        self.assertContains(page, "The rules weren&#x27;t loaded.")

    def test_empty_state(self):
        RulesEdition.objects.all().delete()
        self.assertContains(self.client.get(reverse("collection:rules")), "Download the latest rules")


class CardRulingsTests(TestCase):
    RULINGS = [{"source": "wotc", "published_at": "2022-09-09",
                "comment": "If Sheoldred leaves the battlefield, its abilities still apply. See rule 603.10."}]

    def setUp(self):
        self.card = make_card("Sheoldred, the Apocalypse", keywords="Deathtouch",
                              type_line="Legendary Creature — Phyrexian Praetor", oracle_text="Deathtouch")

    def test_rulings_are_fetched_once_and_cached(self):
        rulesmod.save(rulesmod.parse(SAMPLE_RULES))
        url = reverse("collection:card", args=[self.card.pk])
        with mock.patch.object(scryfall, "rulings", return_value=self.RULINGS) as fetch:
            page = self.client.get(url)
            self.client.get(url)                       # fresh: not fetched again
            self.assertEqual(fetch.call_count, 1)
            self.client.get(url + "?refresh=1")        # asked to check again
            self.assertEqual(fetch.call_count, 2)
        self.assertContains(page, "its abilities still apply")
        self.assertContains(page, 'href="/rules/603.10/"')
        self.assertContains(page, "Keyword rules")      # Deathtouch -> 702.2
        self.assertContains(page, 'href="/rules/702.2/"')
        self.assertEqual(Ruling.objects.filter(card=self.card).count(), 1)

    def test_scryfall_down(self):
        with mock.patch.object(scryfall, "rulings", side_effect=scryfall.ScryfallError("Couldn't reach Scryfall.")):
            page = self.client.get(reverse("collection:card", args=[self.card.pk]))
        self.assertContains(page, "Couldn't load rulings from Scryfall.")

    def test_card_names_link_to_the_card_page(self):
        CollectionItem.objects.create(card=self.card, quantity=1)
        with mock.patch.object(scryfall, "rulings", return_value=[]):
            self.assertContains(self.client.get(reverse("collection:cards")), f'href="/cards/{self.card.pk}/"')
            self.assertContains(self.client.get(reverse("collection:card", args=[self.card.pk])), "no official rulings")

    def test_keyword_index(self):
        rulesmod.save(rulesmod.parse(SAMPLE_RULES))
        self.assertEqual(rulesmod.keyword_index(), {"deathtouch": "702.2", "trample": "702.19"})


# ------------------------------------------------------------------ Deck list import, shopping list, precons

class DeckListParseTests(TestCase):
    def test_sections_and_tags(self):
        text = (
            "Commander\n1 Atraxa, Praetors' Voice (2X2) 190\n\n"
            "Deck\n1 Sol Ring (CMM) 410\n4x Lightning Bolt\n"
            "1 Arcane Signet *CMDR*\n"
            "1 Counterspell [Sideboard]\n"
            "SB: 2 Delver of Secrets\n\n"
            "Sideboard (2)\n1 Negate\n\n"
            "Maybeboard\n1 Mana Crypt\n\n"
            "Deck:\n1 Brainstorm [Draw{noDeck}]\n"
        )
        lines, problems = importer.parse_lines(text)
        boards = {l.name: l.board for l in lines}
        self.assertEqual(problems, [])
        self.assertEqual(boards["Atraxa, Praetors' Voice"], "commander")
        self.assertEqual(boards["Sol Ring"], "main")
        self.assertEqual(boards["Arcane Signet"], "commander")
        self.assertEqual(boards["Counterspell"], "side")
        self.assertEqual(boards["Delver of Secrets"], "side")
        self.assertEqual(boards["Negate"], "side")
        self.assertEqual(boards["Brainstorm"], "main")   # a new "Deck:" header goes back to main
        self.assertNotIn("Mana Crypt", boards)            # maybeboard skipped

    def test_no_headers_means_main(self):
        lines, _ = importer.parse_lines("1 Sol Ring\n2 Lightning Bolt")
        self.assertEqual({l.board for l in lines}, {"main"})


class NeededCardsTests(TestCase):
    def entry(self, name, qty, owned, price, basic=False):
        return {"name": name, "quantity": qty, "owned": owned, "price": price, "is_basic": basic,
                "card_id": 1, "set_code": "tst", "collector_number": "1"}

    def test_needs_and_buy_list(self):
        from decimal import Decimal
        entries = [
            self.entry("Sol Ring", 1, 0, Decimal("1.50")),
            self.entry("Lightning Bolt", 4, 1, Decimal("2.00")),
            self.entry("Counterspell", 1, 3, Decimal("1.00")),       # owned enough
            self.entry("Forest", 30, 0, Decimal("0.10"), basic=True),  # basics never needed
        ]
        needs = decks.needed_cards(entries)
        self.assertEqual([(n["name"], n["need"], n["cost"]) for n in needs],
                         [("Lightning Bolt", 3, Decimal("6.00")), ("Sol Ring", 1, Decimal("1.50"))])
        self.assertEqual(decks.buy_list_text(needs), "3 Lightning Bolt\n1 Sol Ring")

    def test_ownership_is_information_not_an_issue(self):
        ring = make_card("Sol Ring")
        deck = Deck.objects.create(name="Test", format="casual")
        DeckCard.objects.create(deck=deck, card=ring, quantity=1)
        page = self.client.get(reverse("collection:deck", args=[deck.pk]))
        self.assertContains(page, "Cards you need")
        self.assertContains(page, "1 Sol Ring")
        self.assertEqual(page.context["stats"]["issues"], 0)

    def test_any_printing_counts_as_owned(self):
        old = make_card("Sol Ring", scryfall_id="old-ring", set_code="c13")
        new = make_card("Sol Ring", scryfall_id="new-ring", set_code="cmm")
        CollectionItem.objects.create(card=old, quantity=1)
        deck = Deck.objects.create(name="Test", format="casual")
        DeckCard.objects.create(deck=deck, card=new, quantity=1)
        page = self.client.get(reverse("collection:deck", args=[deck.pk]))
        self.assertEqual(page.context["stats"]["needs"], [])

    def test_collect_needed(self):
        ring = make_card("Sol Ring")
        deck = Deck.objects.create(name="Test", format="casual")
        DeckCard.objects.create(deck=deck, card=ring, quantity=2)
        url = reverse("collection:deck_collect_needed", args=[deck.pk])
        self.client.post(url)  # not confirmed
        self.assertEqual(CollectionItem.objects.count(), 0)
        self.client.post(url, {"confirm": "yes"})
        self.assertEqual(CollectionItem.objects.get(card=ring).quantity, 2)
        page = self.client.get(reverse("collection:deck", args=[deck.pk]))
        self.assertNotContains(page, "Cards you need")


@mock.patch.object(scryfall, "lookup", fake_lookup)
@mock.patch.object(scryfall, "get_many", fake_get_many)
class DeckImportViewTests(TestCase):
    LIST = "Commander\n1 Sol Ring (CMM) 410\n\nDeck\n4 Lightning Bolt (2XM) 117\n1 Counterspell\n\nSideboard\n1 Delver of Secrets\n"

    def test_check_then_add_without_owning(self):
        deck = Deck.objects.create(name="Imported", format="casual")
        url = reverse("collection:deck_import", args=[deck.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        page = self.client.post(url, {"action": "check", "cards": self.LIST})
        self.assertContains(page, "Add 7 cards to Imported")
        self.assertEqual(deck.entries.count(), 0)
        done = self.client.post(url, {"action": "add", "items": page.context["items_json"]})
        self.assertRedirects(done, reverse("collection:deck", args=[deck.pk]))
        boards = {(e.card.name, e.board): e.quantity for e in deck.entries.select_related("card")}
        self.assertEqual(boards[("Sol Ring", "commander")], 1)
        self.assertEqual(boards[("Lightning Bolt", "main")], 4)
        self.assertEqual(boards[("Delver of Secrets // Insectile Aberration", "side")], 1)
        self.assertEqual(CollectionItem.objects.count(), 0)  # not owned: they go on the shopping list
        page = self.client.get(reverse("collection:deck", args=[deck.pk]))
        self.assertEqual(page.context["stats"]["need_copies"], 7)

    def test_replace_and_add_to_collection(self):
        deck = Deck.objects.create(name="Imported", format="casual")
        DeckCard.objects.create(deck=deck, card=make_card("Old Card"), quantity=1)
        url = reverse("collection:deck_import", args=[deck.pk])
        page = self.client.post(url, {"action": "check", "cards": "2 Lightning Bolt (2XM) 117"})
        self.client.post(url, {"action": "add", "items": page.context["items_json"], "replace": "1", "collect": "1"})
        self.assertEqual([e.card.name for e in deck.entries.all()], ["Lightning Bolt"])
        self.assertEqual(CollectionItem.objects.get(card__scryfall_id="2xm-117").quantity, 2)

    def test_new_deck_with_pasted_list(self):
        page = self.client.post(reverse("collection:deck_create"),
                                {"name": "From paste", "format": "casual", "cards": "1 Sol Ring"})
        self.assertContains(page, "Add 1 card to From paste")
        self.assertTrue(Deck.objects.filter(name="From paste").exists())

    def test_bad_items(self):
        deck = Deck.objects.create(name="Imported", format="casual")
        self.client.post(reverse("collection:deck_import", args=[deck.pk]), {"action": "add", "items": "[{\"x\": 1}]"})
        self.assertEqual(deck.entries.count(), 0)


DECKLIST_JSON = {"data": [
    {"code": "CMM", "fileName": "SomeCommanderDeck_CMM", "name": "Some Commander Deck",
     "releaseDate": "2023-08-04", "type": "Commander Deck"},
    {"code": "M10", "fileName": "SomeIntroPack_M10", "name": "Some Intro Pack",
     "releaseDate": "2009-07-17", "type": "Intro Pack"},
]}

DECK_JSON = {"data": {
    "code": "CMM", "name": "Some Commander Deck", "type": "Commander Deck",
    "commander": [{"name": "Sol Ring", "count": 1, "setCode": "CMM", "number": "410", "isFoil": True,
                   "identifiers": {"scryfallId": "cmm-410"}}],
    "mainBoard": [
        {"name": "Lightning Bolt", "count": 3, "setCode": "2XM", "number": "117", "isFoil": False,
         "identifiers": {"scryfallId": "2xm-117"}},
        {"name": "Counterspell", "count": 1, "setCode": "MH2", "number": "267", "isFoil": False,
         "identifiers": {}},  # no Scryfall id: found by set and number
    ],
    "sideBoard": [],
}}


@mock.patch.object(scryfall, "lookup", fake_lookup)
@mock.patch.object(scryfall, "get_many", fake_get_many)
class PreconTests(TestCase):
    def fake_get(self, url):
        return DECKLIST_JSON if url.endswith("DeckList.json") else DECK_JSON

    def test_catalog_and_build(self):
        from . import precons
        with mock.patch.object(precons, "_get_json", side_effect=self.fake_get) as get:
            page = self.client.get(reverse("collection:precons"))
            self.assertContains(page, "Some Commander Deck")
            self.assertNotContains(page, "Some Intro Pack")  # Commander decks by default
            page = self.client.get(reverse("collection:precons") + "?type=")
            self.assertContains(page, "Some Intro Pack")
            self.assertEqual(get.call_count, 1)  # catalog cached for a week

            done = self.client.post(reverse("collection:precon_build"),
                                    {"file_name": "SomeCommanderDeck_CMM", "own": "1"})
        deck = Deck.objects.get(name="Some Commander Deck")
        self.assertRedirects(done, reverse("collection:deck", args=[deck.pk]))
        self.assertEqual(deck.format, "commander")
        entries = {(e.card.name, e.board): e.quantity for e in deck.entries.select_related("card")}
        self.assertEqual(entries, {("Sol Ring", "commander"): 1, ("Lightning Bolt", "main"): 3,
                                   ("Counterspell", "main"): 1})
        self.assertEqual(CollectionItem.objects.get(card__scryfall_id="cmm-410").finish, "foil")
        self.assertEqual(sum(CollectionItem.objects.values_list("quantity", flat=True)), 5)

    def test_build_without_owning(self):
        from . import precons
        with mock.patch.object(precons, "_get_json", side_effect=self.fake_get):
            self.client.get(reverse("collection:precons"))
            self.client.post(reverse("collection:precon_build"), {"file_name": "SomeIntroPack_M10"})
        self.assertEqual(Deck.objects.get().format, "casual")
        self.assertEqual(CollectionItem.objects.count(), 0)

    def test_mtgjson_down(self):
        from . import precons
        with mock.patch.object(precons, "_get_json", side_effect=precons.PreconError("Couldn't reach MTGJSON: timeout")):
            page = self.client.get(reverse("collection:precons"))
        self.assertContains(page, "Couldn&#x27;t reach MTGJSON")


# ------------------------------------------------------------------ Database address, backup and restore

class DatabaseUrlTests(TestCase):
    def test_neon_style_address(self):
        from manaledger import dburl
        db = dburl.parse("postgresql://skye:p%40ss@ep-cool-name-123.us-east-2.aws.neon.tech/neondb"
                         "?sslmode=require&channel_binding=require")
        self.assertEqual(db["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual((db["HOST"], db["NAME"], db["USER"], db["PASSWORD"], db["PORT"]),
                         ("ep-cool-name-123.us-east-2.aws.neon.tech", "neondb", "skye", "p@ss", ""))
        self.assertEqual(db["OPTIONS"], {"sslmode": "require"})
        self.assertNotIn("p@ss", dburl.describe(db))

    def test_ssl_added_for_hosted_but_not_local(self):
        from manaledger import dburl
        self.assertEqual(dburl.parse("postgres://u:p@db.example.supabase.co:5432/postgres")["OPTIONS"],
                         {"sslmode": "require"})
        self.assertEqual(dburl.parse("postgres://u:p@db.example.supabase.co:5432/postgres")["PORT"], "5432")
        self.assertEqual(dburl.parse("postgres://u:p@localhost/test")["OPTIONS"], {})

    def test_bad_addresses(self):
        from manaledger import dburl
        for bad in ("mysql://u:p@h/db", "postgresql://u:p@host", "nonsense"):
            with self.assertRaises(ValueError):
                dburl.parse(bad)
        self.assertEqual(dburl.parse("sqlite:///tmp/x.sqlite3")["NAME"], "/tmp/x.sqlite3")


class BackupTests(TestCase):
    def setUp(self):
        self.ring = make_card("Sol Ring")
        CollectionItem.objects.create(card=self.ring, quantity=2, finish="foil")
        deck = Deck.objects.create(name="Backup deck", format="casual")
        DeckCard.objects.create(deck=deck, card=self.ring, quantity=1)

    def test_download_and_restore_round_trip(self):
        download = self.client.get(reverse("collection:backup_download"))
        self.assertIn("attachment; filename=\"manaledger-backup-", download["Content-Disposition"])
        raw = download.content

        # Change things after the backup, then restore it.
        CollectionItem.objects.all().delete()
        Deck.objects.create(name="Made later")
        make_card("Later Card")
        from django.core.files.uploadedfile import SimpleUploadedFile
        page = self.client.post(reverse("collection:backup"),
                                {"confirm": "yes", "backup": SimpleUploadedFile("b.json", raw)}, follow=True)
        self.assertContains(page, "Restored")
        self.assertEqual(CollectionItem.objects.get().quantity, 2)
        self.assertEqual(list(Deck.objects.values_list("name", flat=True)), ["Backup deck"])
        self.assertFalse(Card.objects.filter(name="Later Card").exists())
        self.assertEqual(DeckCard.objects.get().card.name, "Sol Ring")
        Deck.objects.create(name="New after restore")  # ids keep working

    def test_bad_file_changes_nothing(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from . import backup
        for raw in (b"not json", b'{"x": 1}', b'[{"model": "collection.card", "pk": 1, "fields": {"bogus": 1}}]'):
            self.client.post(reverse("collection:backup"),
                             {"confirm": "yes", "backup": SimpleUploadedFile("b.json", raw)})
            self.assertEqual(CollectionItem.objects.get().quantity, 2)
        with self.assertRaises(backup.BackupError):
            backup.restore(b'[{"model": "collection.rule", "pk": 1, "fields": {}}]')

    def test_restore_needs_confirmation(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        raw = self.client.get(reverse("collection:backup_download")).content
        CollectionItem.objects.all().delete()
        self.client.post(reverse("collection:backup"), {"backup": SimpleUploadedFile("b.json", raw)})
        self.assertEqual(CollectionItem.objects.count(), 0)

    def test_page_shows_where_data_lives(self):
        page = self.client.get(reverse("collection:backup"))
        self.assertContains(page, "Local file (db.sqlite3)")
        self.assertContains(page, "Keep your data permanently")

    def test_loaddata_reads_backups(self):
        raw = self.client.get(reverse("collection:backup_download")).content
        CollectionItem.objects.all().delete()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backup.json"
            path.write_bytes(raw)
            call_command("loaddata", str(path), verbosity=0)
        self.assertEqual(CollectionItem.objects.get().quantity, 2)


# ------------------------------------------------------------------ Archidekt

ARCHIDEKT_LIST = {"count": 3, "next": None, "results": [
    {"id": 111, "name": "Sol Ring Tribal", "size": 100, "updatedAt": "2026-10-30T12:00:00Z", "deckFormat": 3,
     "owner": {"id": 9, "username": "SkyeTown"}},
    {"id": 222, "name": "Burn", "size": 60, "updatedAt": "2026-09-01T12:00:00Z", "deckFormat": 2,
     "owner": {"id": 9, "username": "skyetown"}},
    {"id": 333, "name": "Someone else's", "size": 60, "updatedAt": "2026-09-01T12:00:00Z", "deckFormat": 2,
     "owner": {"id": 10, "username": "skyetown2"}},
]}

ARCHIDEKT_DECK = {
    "id": 111, "name": "Sol Ring Tribal", "deckFormat": 3,
    "categories": [
        {"name": "Commander", "includedInDeck": True, "isPremier": True},
        {"name": "Ramp", "includedInDeck": True, "isPremier": False},
        {"name": "Removal", "includedInDeck": True, "isPremier": False},
        {"name": "Sideboard", "includedInDeck": False, "isPremier": False},
        {"name": "Maybeboard", "includedInDeck": False, "isPremier": False},
    ],
    "cards": [
        {"quantity": 1, "categories": ["Commander"], "modifier": "Foil", "deletedAt": None,
         "card": {"uid": "cmm-410", "collectorNumber": "410", "edition": {"editioncode": "cmm"},
                  "oracleCard": {"name": "Sol Ring"}}},
        {"quantity": 4, "categories": ["Removal", "Ramp"], "modifier": "Normal", "deletedAt": None,
         "card": {"uid": "2xm-117", "collectorNumber": "117", "edition": {"editioncode": "2xm"},
                  "oracleCard": {"name": "Lightning Bolt"}}},
        {"quantity": 1, "categories": ["Removal"], "modifier": "Normal", "deletedAt": None,
         "card": {"uid": "", "collectorNumber": "267", "edition": {"editioncode": "mh2"},
                  "oracleCard": {"name": "Counterspell"}}},
        {"quantity": 1, "categories": [], "modifier": "Normal", "deletedAt": None,
         "card": {"uid": "", "oracleCard": {"name": "Delver of Secrets"}}},
        {"quantity": 1, "categories": ["Maybeboard"], "modifier": "Normal", "deletedAt": None,
         "card": {"uid": "m10-146", "oracleCard": {"name": "Lightning Bolt"}}},
        {"quantity": 1, "categories": ["Ramp"], "modifier": "Normal", "deletedAt": "2026-10-01T00:00:00Z",
         "card": {"uid": "m10-146", "oracleCard": {"name": "Lightning Bolt"}}},
    ],
}


def fake_archidekt_get(url, params=None):
    if "/v3/" in url:
        assert params["ownerUsername"] == "skyetown"
        return ARCHIDEKT_LIST
    if url.endswith("/111/"):
        return ARCHIDEKT_DECK
    from . import archidekt
    raise archidekt.ArchidektError("That deck is private on Archidekt. Make it public or unlisted there, then try again.")


class ArchidektParseTests(TestCase):
    def test_usernames_and_links(self):
        from . import archidekt
        self.assertEqual(archidekt.clean_username("https://archidekt.com/u/skyetown"), "skyetown")
        self.assertEqual(archidekt.clean_username("  @skyetown "), "skyetown")
        with self.assertRaises(archidekt.ArchidektError):
            archidekt.clean_username("not a name!")
        self.assertEqual(archidekt.deck_id("https://archidekt.com/decks/123456/my_deck"), 123456)
        self.assertEqual(archidekt.deck_id("123456"), 123456)
        self.assertIsNone(archidekt.deck_id("https://moxfield.com/decks/abc"))

    def test_user_decks_keeps_only_that_user(self):
        from . import archidekt
        with mock.patch.object(archidekt, "_get_json", side_effect=fake_archidekt_get):
            decks = archidekt.user_decks("skyetown")
        self.assertEqual([(d["id"], d["format_name"]) for d in decks], [(111, "Commander"), (222, "Modern")])

    def test_fetch_deck_boards(self):
        from . import archidekt
        with mock.patch.object(archidekt, "_get_json", side_effect=fake_archidekt_get):
            deck = archidekt.fetch_deck(111)
        self.assertEqual(deck["format"], "commander")
        rows = [(r["name"], r["board"], r["quantity"], r["finish"]) for r in deck["rows"]]
        self.assertEqual(rows, [("Sol Ring", "commander", 1, "foil"), ("Lightning Bolt", "main", 4, "nonfoil"),
                                ("Counterspell", "main", 1, "nonfoil"), ("Delver of Secrets", "main", 1, "nonfoil")])


@mock.patch.object(scryfall, "lookup", fake_lookup)
@mock.patch.object(scryfall, "get_many", fake_get_many)
class ArchidektViewTests(TestCase):
    def setUp(self):
        from . import archidekt
        patcher = mock.patch.object(archidekt, "_get_json", side_effect=fake_archidekt_get)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_link_list_import_and_update(self):
        url = reverse("collection:archidekt")
        self.assertContains(self.client.get(url), "Link account")
        self.client.post(url, {"action": "link", "username": "https://archidekt.com/u/skyetown"})
        page = self.client.get(url)
        self.assertContains(page, "Sol Ring Tribal")
        self.assertNotContains(page, "Someone else")

        done = self.client.post(reverse("collection:archidekt_import"), {"deck": "111"})
        deck = Deck.objects.get(source="archidekt")
        self.assertRedirects(done, reverse("collection:deck", args=[deck.pk]))
        self.assertEqual((deck.name, deck.format, deck.source_id), ("Sol Ring Tribal", "commander", "111"))
        entries = {(e.card.name, e.board): e.quantity for e in deck.entries.select_related("card")}
        self.assertEqual(entries, {("Sol Ring", "commander"): 1, ("Lightning Bolt", "main"): 4,
                                   ("Counterspell", "main"): 1,
                                   ("Delver of Secrets // Insectile Aberration", "main"): 1})
        self.assertEqual(CollectionItem.objects.count(), 0)
        self.assertContains(self.client.get(url), "imported")

        # Importing again points at the existing deck instead of making a copy.
        self.client.post(reverse("collection:archidekt_import"), {"deck": "https://archidekt.com/decks/111/x"})
        self.assertEqual(Deck.objects.count(), 1)

        # Local edits are replaced by Archidekt's list on update (only when confirmed).
        DeckCard.objects.filter(deck=deck, card__name="Lightning Bolt").update(quantity=1)
        sync = reverse("collection:archidekt_sync", args=[deck.pk])
        self.client.post(sync)
        self.assertEqual(DeckCard.objects.get(deck=deck, card__name="Lightning Bolt").quantity, 1)
        self.client.post(sync, {"confirm": "yes"})
        self.assertEqual(DeckCard.objects.get(deck=deck, card__name="Lightning Bolt").quantity, 4)
        self.assertContains(self.client.get(reverse("collection:deck", args=[deck.pk])), "Update from Archidekt")

        self.client.post(url, {"action": "unlink"})
        self.assertFalse(LinkedProfile.objects.exists())
        self.assertTrue(Deck.objects.filter(pk=deck.pk).exists())

    def test_import_owned(self):
        self.client.post(reverse("collection:archidekt_import"), {"deck": "111", "own": "1"})
        self.assertEqual(CollectionItem.objects.get(card__scryfall_id="cmm-410").finish, "foil")

    def test_private_deck(self):
        page = self.client.post(reverse("collection:archidekt_import"), {"deck": "999"}, follow=True)
        self.assertContains(page, "private on Archidekt")
        self.assertEqual(Deck.objects.count(), 0)  # nothing half-made


# ------------------------------------------------------------------ Suggestions from your collection

def sc(name, text="", type_line="Artifact", ci="", cmc=2, gc=False, **kw):
    """A card dict as suggest.py sees it."""
    return dict({"name": name, "oracle_text": text, "type_line": type_line, "color_identity": ci, "cmc": cmc,
                 "game_changer": gc, "legalities": {"commander": "legal"}, "is_basic": False, "card_id": hash(name) % 10000,
                 "board": "main", "quantity": 1, "price": 1.0, "owned": 1, "in_decks": [], "mana_cost": ""}, **kw)


class SuggestLogicTests(TestCase):
    def test_roles(self):
        from . import suggest
        self.assertEqual(suggest.roles_of(sc("Arcane Signet", "{T}: Add one mana of any color in your commander's color identity.")), {"ramp"})
        self.assertEqual(suggest.roles_of(sc("Divination", "Draw two cards.", "Sorcery")), {"draw"})
        self.assertEqual(suggest.roles_of(sc("Doom Blade", "Destroy target nonblack creature.", "Instant")), {"interaction"})
        self.assertEqual(suggest.roles_of(sc("Damnation", "Destroy all creatures. They can't be regenerated.", "Sorcery")), {"wipe"})
        self.assertEqual(suggest.roles_of(sc("Demonic Tutor", "Search your library for a card, put that card into your hand, then shuffle.", "Sorcery")), {"tutor"})
        self.assertEqual(suggest.roles_of(sc("Cultivate", "Search your library for up to two basic land cards, reveal those cards, put one onto the battlefield tapped and the other into your hand, then shuffle.", "Sorcery")), {"ramp"})
        self.assertEqual(suggest.roles_of(sc("Command Tower", "{T}: Add one mana...", "Land")), {"land"})

    def test_themes_and_tribe(self):
        from . import suggest
        deck = [sc("Meren of Clan Nel Toth", "Whenever another creature you control dies, you get an experience counter.", "Legendary Creature — Human Shaman", "BG", board="commander")]
        deck += [sc(f"Zombie {i}", "Sacrifice another creature: draw a card.", "Creature — Zombie", "B") for i in range(6)]
        themes, tribe = suggest.deck_themes(deck)
        self.assertIn("sacrifice", themes)
        self.assertEqual(tribe, "Zombie")

    def test_game_changers_left_out_at_bracket_2_allowed_at_3(self):
        from . import suggest
        deck = [sc("Commander Guy", "", "Legendary Creature — Elf", "G", board="commander")]
        rhystic = sc("Rhystic Study", "Whenever an opponent casts a spell, you may draw a card unless that player pays {1}.", "Enchantment", "U")
        crop = sc("Crop Rotation", "Search your library for a land card.", "Instant", "G", gc=True)
        fork = sc("Natural Order", "Search your library for a green creature card and put it onto the battlefield.", "Sorcery", "G", gc=True)
        bracket2 = suggest.candidate_pool([rhystic, crop, fork], deck, "commander", 2)
        self.assertEqual(bracket2, [])  # off-color Rhystic, and no Game Changers in Bracket 2
        bracket3 = suggest.candidate_pool([crop, fork], deck, "commander", 3)
        self.assertEqual({c["name"] for c in bracket3}, {"Crop Rotation", "Natural Order"})

    def test_swaps_bring_an_over_bracket_deck_down(self):
        from . import suggest
        deck = [sc("Commander Guy", "", "Legendary Creature — Elf", "G", board="commander")]
        deck += [sc(n, "{T}: Add {C}{C}.", gc=True) for n in ("Mana Vault", "Grim Monolith", "Ancient Tomb")]
        deck += [sc("Plain Rock", "{T}: Add {G}.")]
        collection = [sc("Mind Stone", "{T}: Add {C}. {1}, {T}, Sacrifice Mind Stone: Draw a card."),
                      sc("Fellwar Stone", "{T}: Add one mana of any color that a land an opponent controls could produce."),
                      sc("Thought Vessel", "{T}: Add {C}.")]
        result = suggest.suggestions(deck, collection, "commander", intended=2)
        self.assertEqual(result["bracket"]["computed"], 3)
        self.assertEqual(len(result["swaps"]), 3)
        self.assertTrue(all(w["add"] for w in result["swaps"]))
        self.assertEqual(len({w["add"]["name"] for w in result["swaps"]}), 3)  # no replacement used twice
        self.assertEqual(result["budget"]["game_changers"], 0)

    def test_short_roles_rank_first(self):
        from . import suggest
        deck = [sc("Commander Guy", "", "Legendary Creature — Elf", "G", board="commander")]
        deck += [sc(f"Rock {i}", "{T}: Add {G}.") for i in range(12)]   # plenty of ramp, no draw
        collection = [sc("Harmonize", "Draw three cards.", "Sorcery", "G", cmc=4),
                      sc("Another Rock", "{T}: Add {G}.")]
        result = suggest.suggestions(deck, collection, "commander")
        roles = [g["role"] for g in result["groups"]]
        self.assertEqual(roles[0], "draw")          # no draw at all: listed first
        self.assertNotIn("ramp", roles)             # 12 ramp cards is enough: no ramp section
        self.assertIn("Harmonize", [c["name"] for c in result["groups"][0]["cards"]])

    def test_commander_ideas(self):
        from . import suggest
        col = [sc("Golgari Boss", "", "Legendary Creature — Elf", "BG"),
               sc("Mono Red Boss", "", "Legendary Creature — Goblin", "R"),
               sc("Elf A", "", "Creature — Elf", "G"), sc("Zombie B", "", "Creature — Zombie", "B"),
               sc("Goblin C", "", "Creature — Goblin", "R")]
        ideas = suggest.commander_ideas(col)
        self.assertEqual([i["card"]["name"] for i in ideas], ["Golgari Boss", "Mono Red Boss"])
        self.assertEqual(ideas[0]["nonland"], 2)


class SuggestViewTests(TestCase):
    def test_pages_and_start_deck(self):
        boss = make_card("Golgari Boss", type_line="Legendary Creature — Elf", color_identity="BG")
        signet = make_card("Golgari Signet", type_line="Artifact", color_identity="BG",
                           oracle_text="{1}, {T}: Add {B}{G}.", mana_cost="{2}", cmc=2)
        bolt = make_card("Lightning Bolt", type_line="Instant", color_identity="R",
                         oracle_text="Lightning Bolt deals 3 damage to any target.")
        for c in (boss, signet, bolt):
            CollectionItem.objects.create(card=c, quantity=1)

        ideas = self.client.get(reverse("collection:commander_ideas"))
        self.assertContains(ideas, "Golgari Boss")
        done = self.client.post(reverse("collection:deck_from_commander"), {"card_id": boss.pk, "target_bracket": "2"})
        deck = Deck.objects.get(name="Golgari Boss")
        self.assertRedirects(done, reverse("collection:deck_suggestions", args=[deck.pk]))
        self.assertEqual((deck.format, deck.target_bracket), ("commander", 2))

        page = self.client.get(reverse("collection:deck_suggestions", args=[deck.pk]))
        self.assertContains(page, "Keeping Golgari Boss at")
        self.assertContains(page, "Golgari Signet")
        self.assertNotContains(page, "Lightning Bolt")  # off-color

        url = reverse("collection:deck_suggestions", args=[deck.pk])
        added = self.client.post(reverse("collection:deck_add", args=[deck.pk]),
                                 {"card_id": signet.pk, "board": "main", "next": url})
        self.assertRedirects(added, url)
        self.assertTrue(deck.entries.filter(card=signet).exists())

    def test_cut_removes_every_copy_but_not_the_commander(self):
        boss = make_card("Boss", type_line="Legendary Creature — Elf")
        ring = make_card("Sol Ring")
        deck = Deck.objects.create(name="D", format="commander")
        DeckCard.objects.create(deck=deck, card=boss, board="commander")
        DeckCard.objects.create(deck=deck, card=ring, board="main")
        DeckCard.objects.create(deck=deck, card=ring, board="side")
        self.client.post(reverse("collection:deck_cut", args=[deck.pk]), {"name": "Sol Ring"})
        self.client.post(reverse("collection:deck_cut", args=[deck.pk]), {"name": "Boss"})
        self.assertEqual(list(deck.entries.values_list("card__name", flat=True)), ["Boss"])

    def test_non_commander_deck(self):
        bolt = make_card("Lightning Bolt", type_line="Instant", color_identity="R", colors="R",
                         oracle_text="Lightning Bolt deals 3 damage to any target.")
        shock = make_card("Shock", type_line="Instant", color_identity="R", colors="R",
                          oracle_text="Shock deals 2 damage to any target.")
        CollectionItem.objects.create(card=shock, quantity=4)
        deck = Deck.objects.create(name="Burn", format="modern")
        DeckCard.objects.create(deck=deck, card=bolt, quantity=4)
        page = self.client.get(reverse("collection:deck_suggestions", args=[deck.pk]))
        self.assertContains(page, "Shock")
        self.assertNotContains(page, "Game Changers")


class AssetVersionTests(TestCase):
    def test_scripts_carry_a_version(self):
        page = self.client.get(reverse("collection:dashboard"))
        self.assertRegex(page.content.decode(), r'/static/collection/style\.css\?v=\d+')
        self.assertRegex(page.content.decode(), r'/static/collection/app\.js\?v=\d+')


# ------------------------------------------------------------------ Want list

@mock.patch.object(scryfall, "lookup", fake_lookup)
@mock.patch.object(scryfall, "get_many", fake_get_many)
class WantListTests(TestCase):
    def test_add_by_name_merges_and_tracks_price(self):
        url = reverse("collection:want_add")
        self.client.post(url, {"name": "Sol Ring", "quantity": "1", "max_price": "2.00"})
        self.client.post(url, {"name": "Sol Ring", "quantity": "2"})
        item = WantItem.objects.get()
        self.assertEqual((item.quantity, item.any_printing, str(item.max_price)), (3, True, "2.00"))
        self.assertTrue(item.is_deal)            # $1.20 is under $2.00
        page = self.client.get(reverse("collection:wants"))
        self.assertContains(page, "At or under your price")
        self.assertContains(page, "3 Sol Ring")  # shopping list

    def test_add_exact_printing_and_unknown_name(self):
        self.client.post(reverse("collection:want_add"), {"name": "Lightning Bolt", "set": "m10"})
        item = WantItem.objects.get()
        self.assertEqual((item.card.set_code, item.any_printing), ("m10", False))
        with mock.patch.object(scryfall, "fuzzy", return_value=None):
            page = self.client.post(reverse("collection:want_add"), {"name": "Nonsense Card"}, follow=True)
        self.assertContains(page, "has no card named")
        self.assertEqual(WantItem.objects.count(), 1)

    def test_got_it_moves_to_collection(self):
        self.client.post(reverse("collection:want_add"), {"name": "Sol Ring", "quantity": "3"})
        item = WantItem.objects.get()
        self.client.post(reverse("collection:want_got", args=[item.pk]), {"quantity": "2", "finish": "foil", "price_paid": "5"})
        self.assertEqual(WantItem.objects.get().quantity, 1)
        owned = CollectionItem.objects.get(card__name="Sol Ring")
        self.assertEqual((owned.quantity, owned.finish, str(owned.purchase_price)), (2, "foil", "5.00"))
        self.client.post(reverse("collection:want_got", args=[item.pk]), {"finish": "foil"})
        self.assertFalse(WantItem.objects.exists())
        self.assertEqual(CollectionItem.objects.get(card__name="Sol Ring").quantity, 3)

    def test_update_and_remove(self):
        self.client.post(reverse("collection:want_add"), {"name": "Sol Ring"})
        item = WantItem.objects.get()
        self.client.post(reverse("collection:want_update", args=[item.pk]),
                         {"quantity": "4", "max_price": "", "priority": "1", "finish": "foil"})
        item.refresh_from_db()
        self.assertEqual((item.quantity, item.priority, item.finish, item.max_price), (4, 1, "foil", None))
        self.client.post(reverse("collection:want_update", args=[item.pk]), {"remove": "1"})
        self.assertFalse(WantItem.objects.exists())

    def test_paste_a_list(self):
        page = self.client.post(reverse("collection:want_import"),
                                {"cards": "2 Lightning Bolt (2XM) 117\n1 Sol Ring *F*\n1 Nope Card", "priority": "1"}, follow=True)
        self.assertContains(page, "Added 3 cards")
        self.assertContains(page, "Skipped")
        bolt = WantItem.objects.get(card__name="Lightning Bolt")
        self.assertEqual((bolt.quantity, bolt.any_printing, bolt.priority), (2, False, 1))
        self.assertEqual(WantItem.objects.get(card__name="Sol Ring").finish, "foil")

    def test_from_deck_needs_and_card_page(self):
        ring = make_card("Sol Ring")
        deck = Deck.objects.create(name="D", format="casual")
        DeckCard.objects.create(deck=deck, card=ring, quantity=2)
        self.client.post(reverse("collection:deck_want_needed", args=[deck.pk]))
        self.client.post(reverse("collection:deck_want_needed", args=[deck.pk]))  # again: no duplicates
        item = WantItem.objects.get()
        self.assertEqual((item.quantity, item.deck), (2, deck))
        page = self.client.get(reverse("collection:card", args=[ring.pk]))
        self.assertContains(page, "2 wanted")
        self.client.post(reverse("collection:want_add"), {"card_id": ring.pk})
        self.assertEqual(WantItem.objects.count(), 2)  # not for a deck: its own row

    def test_want_cards_get_price_refreshes(self):
        from . import services
        self.client.post(reverse("collection:want_add"), {"name": "Sol Ring"})
        with mock.patch.object(scryfall, "get_many", return_value=([], [])) as get_many:
            services.refresh_prices()
        self.assertIn("cmm-410", get_many.call_args[0][0])


# ------------------------------------------------------------------ Proxies

class ProxyTests(TestCase):
    def setUp(self):
        self.crypt = make_card("Mana Crypt", usd=150)
        self.ring = make_card("Sol Ring", usd=1)
        self.bolt = make_card("Lightning Bolt", type_line="Instant", usd=2)
        self.deck = Deck.objects.create(name="Proxied", format="casual")
        self.crypt_e = DeckCard.objects.create(deck=self.deck, card=self.crypt, quantity=1)
        self.ring_e = DeckCard.objects.create(deck=self.deck, card=self.ring, quantity=1)
        self.bolt_e = DeckCard.objects.create(deck=self.deck, card=self.bolt, quantity=4)
        CollectionItem.objects.create(card=self.bolt, quantity=1)

    def needs(self):
        page = self.client.get(reverse("collection:deck", args=[self.deck.pk]))
        return {n["name"]: n["need"] for n in page.context["stats"]["needs"]}

    def test_proxies_come_off_the_needs(self):
        self.assertEqual(self.needs(), {"Mana Crypt": 1, "Sol Ring": 1, "Lightning Bolt": 3})
        url = reverse("collection:deck_entry_update", args=[self.crypt_e.pk])
        self.client.post(url, {"quantity": 1, "board": "main", "proxy_form": "1", "proxy": "1"})
        self.client.post(reverse("collection:deck_entry_update", args=[self.bolt_e.pk]),
                         {"quantity": 4, "board": "main", "proxy_form": "1", "proxies": "2"})
        self.assertEqual(self.needs(), {"Sol Ring": 1, "Lightning Bolt": 1})
        page = self.client.get(reverse("collection:deck", args=[self.deck.pk]))
        self.assertEqual(page.context["stats"]["proxy_count"], 3)
        # Untick it again; saving an unowned card keeps it in the deck.
        self.client.post(url, {"quantity": 1, "board": "main", "proxy_form": "1"})
        self.crypt_e.refresh_from_db()
        self.assertEqual(self.crypt_e.proxies, 0)

    def test_proxy_buttons(self):
        self.client.post(reverse("collection:deck_proxy", args=[self.deck.pk]), {"name": "Mana Crypt"})
        self.assertEqual(self.needs(), {"Sol Ring": 1, "Lightning Bolt": 3})
        self.client.post(reverse("collection:deck_proxy", args=[self.deck.pk]))
        self.assertEqual(self.needs(), {})
        self.bolt_e.refresh_from_db()
        self.assertEqual(self.bolt_e.proxies, 3)  # you own one real Bolt
        page = self.client.get(reverse("collection:deck", args=[self.deck.pk]))
        self.assertContains(page, "You own or proxy every card")

    def test_print_sheet(self):
        self.client.post(reverse("collection:deck_proxy", args=[self.deck.pk]))
        url = reverse("collection:deck_proxies", args=[self.deck.pk])
        page = self.client.get(url + "?style=text")
        self.assertEqual(page.context["count"], 5)      # Crypt, Ring, 3 Bolts
        self.assertContains(page, 'class="proxy-card proxy-text"', count=5)
        everything = self.client.get(url + "?all=1")
        self.assertEqual(everything.context["count"], 6)
        self.assertEqual(len(everything.context["pages"]), 1)


# ------------------------------------------------------------------ Deck views (stacks / text / table) and covers

class DeckViewModeTests(TestCase):
    def setUp(self):
        self.boss = make_card("Golgari Boss", type_line="Legendary Creature — Elf", color_identity="BG",
                              image_url="https://cards.scryfall.io/normal/front/a/b/boss.jpg")
        self.ring = make_card("Sol Ring", image_url="https://cards.scryfall.io/normal/front/c/d/ring.jpg", usd=2)
        self.deck = Deck.objects.create(name="Views", format="commander")
        DeckCard.objects.create(deck=self.deck, card=self.boss, board="commander")
        self.ring_e = DeckCard.objects.create(deck=self.deck, card=self.ring, board="main")
        self.url = reverse("collection:deck", args=[self.deck.pk])

    def test_views_and_remembered_choice(self):
        page = self.client.get(self.url)                       # default: stacks
        self.assertContains(page, 'class="stk-card')
        page = self.client.get(self.url + "?view=text")
        self.assertContains(page, 'class="txt-row')
        page = self.client.get(self.url)                       # remembered
        self.assertContains(page, 'class="txt-row')
        page = self.client.get(self.url + "?view=table")
        self.assertContains(page, 'class="data deck-table"')
        self.assertContains(self.client.get(self.url + "?view=bogus"), 'class="data deck-table"')

    def test_stack_buttons_change_copies(self):
        page = self.client.get(self.url + "?view=stacks")
        self.assertContains(page, 'name="quantity" value="2"')  # the + button for Sol Ring
        self.client.post(reverse("collection:deck_entry_update", args=[self.ring_e.pk]), {"quantity": "0"})
        self.assertFalse(DeckCard.objects.filter(pk=self.ring_e.pk).exists())

    def test_cover_defaults_to_commander_and_can_change(self):
        decks = self.client.get(reverse("collection:decks"))
        self.assertContains(decks, "/art_crop/front/a/b/boss.jpg")
        self.client.post(reverse("collection:deck_cover", args=[self.deck.pk]), {"card_id": self.ring.pk})
        self.deck.refresh_from_db()
        self.assertEqual(self.deck.cover_card, self.ring)
        self.assertContains(self.client.get(reverse("collection:decks")), "/art_crop/front/c/d/ring.jpg")
        other = make_card("Not In Deck")
        self.client.post(reverse("collection:deck_cover", args=[self.deck.pk]), {"card_id": other.pk})
        self.deck.refresh_from_db()
        self.assertIsNone(self.deck.cover_card)                 # not in the deck: back to the default

    def test_tile_shows_colors_and_menu(self):
        decks = self.client.get(reverse("collection:decks"))
        self.assertContains(decks, "ad-color-b")
        self.assertContains(decks, "ad-color-g")
        self.assertContains(decks, "View as stacks")


# ------------------------------------------------------------------ Deck versions and "Show changes"

class DeckVersionTests(TestCase):
    def setUp(self):
        self.ring = make_card("Sol Ring")
        self.bolt = make_card("Lightning Bolt", type_line="Instant")
        self.crypt = make_card("Mana Crypt")
        self.negate = make_card("Negate", type_line="Instant")
        for c in (self.ring, self.bolt, self.crypt, self.negate):
            CollectionItem.objects.create(card=c, quantity=4)
        self.deck = Deck.objects.create(name="Versions", format="casual")
        self.ring_e = DeckCard.objects.create(deck=self.deck, card=self.ring, quantity=1)
        self.bolt_e = DeckCard.objects.create(deck=self.deck, card=self.bolt, quantity=2)
        self.crypt_e = DeckCard.objects.create(deck=self.deck, card=self.crypt, quantity=1)
        self.negate_e = DeckCard.objects.create(deck=self.deck, card=self.negate, quantity=1, board="side")
        self.url = reverse("collection:deck", args=[self.deck.pk])

    def age_versions(self, minutes=45):
        from datetime import timedelta
        from django.utils import timezone
        DeckVersion.objects.update(created_at=timezone.now() - timedelta(minutes=minutes))

    def test_first_visit_saves_a_baseline_and_edits_show_as_changes(self):
        self.client.get(self.url)
        self.assertEqual(self.deck.versions.count(), 1)
        # Edit: one more Bolt, Crypt out, Negate moves to the main deck, a new Ring... er, new card.
        self.client.post(reverse("collection:deck_entry_update", args=[self.bolt_e.pk]), {"quantity": 3})
        self.client.post(reverse("collection:deck_entry_update", args=[self.crypt_e.pk]), {"quantity": 0})
        self.client.post(reverse("collection:deck_entry_update", args=[self.negate_e.pk]), {"quantity": 1, "board": "main"})
        fresh = make_card("Brainstorm", type_line="Instant")
        CollectionItem.objects.create(card=fresh, quantity=1)
        self.client.post(reverse("collection:deck_add", args=[self.deck.pk]), {"card_id": fresh.pk, "board": "main"})
        self.assertEqual(self.deck.versions.count(), 1)   # same editing session: no new version

        page = self.client.get(self.url + "?view=table&changes=1")
        ch = page.context["changes"]
        self.assertEqual((ch["added"], ch["changed"], ch["removed_count"]), (1, 2, 1))
        rows = [e for g in page.context["stats"]["groups"] for e in g["entries"]]
        statuses = {e["name"]: (e.get("change") or {}).get("status") for e in rows}
        self.assertEqual(statuses["Lightning Bolt"], "more")
        self.assertEqual(statuses["Brainstorm"], "new")
        self.assertEqual(statuses["Negate"], "moved")
        self.assertEqual(statuses["Sol Ring"], None)
        self.assertContains(page, "Moved from sideboard")
        self.assertContains(page, "<s>1 Mana Crypt</s>", html=True)
        for view in ("stacks", "text"):
            self.assertContains(self.client.get(self.url + f"?view={view}&changes=1"), 'chg-badge chg-new')

    def test_new_session_saves_a_version_first(self):
        self.client.get(self.url)
        self.age_versions()
        self.client.post(reverse("collection:deck_entry_update", args=[self.bolt_e.pk]), {"quantity": 3})
        self.assertEqual(self.deck.versions.count(), 1)   # list hadn't changed since the baseline: no duplicate
        self.age_versions()
        self.client.post(reverse("collection:deck_entry_update", args=[self.bolt_e.pk]), {"quantity": 4})
        self.assertEqual(self.deck.versions.count(), 2)   # saved the 3-Bolt list before this edit
        page = self.client.get(self.url + "?changes=1")
        self.assertEqual(page.context["changes"]["by_entry"][self.bolt_e.pk], {"status": "more", "delta": 1, "was": 3})

    def test_save_restore_and_put_back(self):
        self.client.post(reverse("collection:deck_version_save", args=[self.deck.pk]), {"label": "Before upgrades"})
        saved = self.deck.versions.get(label="Before upgrades")
        self.client.post(reverse("collection:deck_cut", args=[self.deck.pk]), {"name": "Mana Crypt"})
        self.assertFalse(self.deck.entries.filter(card=self.crypt).exists())

        self.client.post(reverse("collection:deck_put_back", args=[self.deck.pk]),
                         {"card_id": self.crypt.pk, "board": "main", "quantity": 1})
        self.assertTrue(self.deck.entries.filter(card=self.crypt).exists())

        self.client.post(reverse("collection:deck_cut", args=[self.deck.pk]), {"name": "Lightning Bolt"})
        restore = reverse("collection:deck_version_restore", args=[self.deck.pk, saved.pk])
        self.client.post(restore)  # not confirmed
        self.assertFalse(self.deck.entries.filter(card=self.bolt).exists())
        self.client.post(restore, {"confirm": "yes"})
        self.assertEqual(self.deck.entries.get(card=self.bolt).quantity, 2)
        self.assertTrue(self.deck.versions.filter(label="Before restoring an older version").exists())

    def test_import_saves_a_named_version(self):
        from . import versions
        versions.checkpoint(self.deck, "Before importing a list")
        self.assertTrue(self.deck.versions.filter(label="Before importing a list").exists())

    def test_nothing_to_compare_yet(self):
        page = self.client.get(self.url + "?changes=1")
        self.assertIsNone(page.context["changes"])
        self.assertContains(page, "No earlier version")


class DeckVersionPrintingTests(TestCase):
    def test_two_printings_of_one_card_compare_together(self):
        from . import versions
        a = make_card("Rhystic Study", scryfall_id="rs-pcy", set_code="pcy")
        b = make_card("Rhystic Study", scryfall_id="rs-jmp", set_code="jmp")
        deck = Deck.objects.create(name="P", format="casual")
        ea = DeckCard.objects.create(deck=deck, card=a, quantity=1)
        DeckCard.objects.create(deck=deck, card=b, quantity=1)
        v = versions.snapshot(deck)
        page = self.client.get(reverse("collection:deck", args=[deck.pk]) + f"?changes=1&since={v.pk}")
        ch = page.context["changes"]                 # a version you pick is compared even if nothing changed
        self.assertEqual((ch["added"], ch["changed"], ch["removed_count"]), (0, 0, 0))
        from .views import _deck_entries
        entries = _deck_entries(Deck.objects.prefetch_related("entries__card").get(pk=deck.pk))
        self.assertEqual(versions.diff(entries, v)["by_entry"], {})
        ea.quantity = 2
        ea.save()
        entries = _deck_entries(Deck.objects.prefetch_related("entries__card").get(pk=deck.pk))
        d = versions.diff(entries, v)
        self.assertEqual(list(d["by_entry"].values()), [{"status": "more", "delta": 1, "was": 2}])


class LibraryLiveSearchTests(TestCase):
    def test_partial_returns_only_results_and_add_returns_to_the_page(self):
        bolt = make_card("Lightning Bolt", type_line="Instant")
        CollectionItem.objects.create(card=bolt, quantity=4)
        deck = Deck.objects.create(name="Live", format="casual")
        url = reverse("collection:deck", args=[deck.pk])
        part = self.client.get(url + "?view=table&q=bolt&partial=library")
        html = part.content.decode()
        self.assertIn("Lightning Bolt", html)
        self.assertNotIn("<html", html)                  # just the results, not the whole page
        self.assertIn(f'name="next" value="{url}?view=table&amp;q=bolt"', html)   # no partial flag in the way back
        full = self.client.get(url + "?view=table&q=bolt")
        self.assertContains(full, 'id="library-results"')


class DeckTrimTests(TestCase):
    def setUp(self):
        self.deck = Deck.objects.create(name="Trim", format="casual")
        self.entries = [DeckCard.objects.create(deck=self.deck, card=make_card(n), quantity=q)
                        for n, q in (("Keep Me", 1), ("Cut Me", 2), ("Cut Me Too", 1), ("Not Shown", 1))]
        self.url = reverse("collection:deck_trim", args=[self.deck.pk])

    def test_removes_only_unticked_cards_that_were_shown(self):
        keep, cut1, cut2, hidden = self.entries
        self.client.get(reverse("collection:deck", args=[self.deck.pk]))       # baseline version
        shown = [keep.pk, cut1.pk, cut2.pk]                                    # "Not Shown" wasn't on the page
        page = self.client.post(self.url, {"shown": shown, "keep": [keep.pk]}, follow=True)
        self.assertContains(page, "Removed 3 cards")
        self.assertEqual(sorted(self.deck.entries.values_list("card__name", flat=True)), ["Keep Me", "Not Shown"])
        self.assertTrue(self.deck.versions.filter(label="Before removing 3 cards").exists())

    def test_nothing_unticked_or_nothing_sent_removes_nothing(self):
        ids = [e.pk for e in self.entries]
        self.client.post(self.url, {"shown": ids, "keep": ids})
        self.client.post(self.url, {})                                       # a page that sent no boxes
        self.assertEqual(self.deck.entries.count(), 4)

    def test_other_decks_are_safe(self):
        other = Deck.objects.create(name="Other", format="casual")
        theirs = DeckCard.objects.create(deck=other, card=make_card("Theirs"), quantity=1)
        self.client.post(self.url, {"shown": [theirs.pk]})
        self.assertTrue(DeckCard.objects.filter(pk=theirs.pk).exists())

    def test_table_view_has_keep_boxes(self):
        page = self.client.get(reverse("collection:deck", args=[self.deck.pk]) + "?view=table")
        self.assertContains(page, 'name="keep"', count=4)
        self.assertContains(page, "Remove unticked cards")


class DeckViewControlsTests(TestCase):
    def test_every_view_has_plus_minus_and_keep(self):
        deck = Deck.objects.create(name="Controls", format="casual")
        ring = DeckCard.objects.create(deck=deck, card=make_card("Sol Ring"), quantity=1)
        DeckCard.objects.create(deck=deck, card=make_card("Lightning Bolt", type_line="Instant"), quantity=2)
        url = reverse("collection:deck", args=[deck.pk])
        for view in ("stacks", "text", "table"):
            page = self.client.get(url + f"?view={view}")
            self.assertContains(page, 'name="keep"', count=2, msg_prefix=view)
            self.assertContains(page, 'name="shown"', count=2, msg_prefix=view)
            self.assertContains(page, "Remove unticked cards", msg_prefix=view)
            self.assertContains(page, 'name="quantity" value="3"', msg_prefix=view)   # + for the Bolts
            self.assertContains(page, 'name="quantity" value="0"', msg_prefix=view)   # − for the Ring removes it
        # a − press on a single copy takes the card out
        self.client.post(reverse("collection:deck_entry_update", args=[ring.pk]), {"quantity": "0"})
        self.assertFalse(DeckCard.objects.filter(pk=ring.pk).exists())


class DeckAlphabeticalTests(TestCase):
    def test_sections_list_cards_a_to_z(self):
        deck = Deck.objects.create(name="Abc", format="casual")
        for name, cmc in (("Zap", 1), ("ancestral recall", 1), ("Bolt", 1), ("Atog", 2), ("Mox", 0)):
            DeckCard.objects.create(deck=deck, card=make_card(name, type_line="Instant" if cmc == 1 else "Artifact", cmc=cmc))
        page = self.client.get(reverse("collection:deck", args=[deck.pk]))
        for group in page.context["stats"]["groups"]:
            names = [e["name"] for e in group["entries"]]
            self.assertEqual(names, sorted(names, key=str.casefold), group["label"])
        instants = next(g for g in page.context["stats"]["groups"] if g["label"] == "Instants")
        self.assertEqual([e["name"] for e in instants["entries"]], ["ancestral recall", "Bolt", "Zap"])


class DeckDeleteTests(TestCase):
    def setUp(self):
        self.ring = make_card("Sol Ring", type_line="Artifact")
        self.bolt = make_card("Lightning Bolt", type_line="Instant")
        CollectionItem.objects.create(card=self.ring, quantity=1)
        self.deck = Deck.objects.create(name="Burn", format="modern", description="Go face", cover_card=self.bolt,
                                        source="archidekt", source_id="123", source_url="https://archidekt.com/decks/123")
        DeckCard.objects.create(deck=self.deck, card=self.bolt, quantity=4)
        DeckCard.objects.create(deck=self.deck, card=self.ring, quantity=1, board="side", proxies=1)
        from . import versions
        versions.snapshot(self.deck, label="Before the tournament")
        self.want = WantItem.objects.create(card=self.bolt, quantity=2, deck=self.deck)
        self.other = Deck.objects.create(name="Other", format="casual")

    def test_delete_keeps_a_copy_and_put_back_restores_everything(self):
        self.client.post(reverse("collection:deck_delete", args=[self.deck.pk]), {"confirm": "yes"})
        self.assertFalse(Deck.objects.filter(name="Burn").exists())
        gone = DeletedDeck.objects.get()
        self.assertEqual((gone.name, gone.size), ("Burn", 4))
        page = self.client.get(reverse("collection:decks"))
        self.assertContains(page, "Recently deleted")
        self.assertContains(page, "Put back")
        self.want.refresh_from_db()
        self.assertIsNone(self.want.deck)  # the want stays, unlinked

        resp = self.client.post(reverse("collection:deck_undelete", args=[gone.pk]))
        deck = Deck.objects.get(name="Burn")
        self.assertRedirects(resp, reverse("collection:deck", args=[deck.pk]))
        self.assertEqual((deck.format, deck.description, deck.cover_card, deck.source_id), ("modern", "Go face", self.bolt, "123"))
        side = deck.entries.get(board="side")
        self.assertEqual((side.card, side.quantity, side.proxies), (self.ring, 1, 1))
        self.assertEqual(deck.entries.get(board="main").quantity, 4)
        self.assertEqual(list(deck.versions.values_list("label", flat=True)), ["Before the tournament"])
        self.want.refresh_from_db()
        self.assertEqual(self.want.deck, deck)
        self.assertFalse(DeletedDeck.objects.exists())

    def test_delete_several_needs_confirmation(self):
        url = reverse("collection:deck_delete_many")
        self.client.post(url, {"deck": [self.deck.pk, self.other.pk]})
        self.assertEqual(Deck.objects.count(), 2)
        resp = self.client.post(url, {"deck": [self.deck.pk, self.other.pk], "confirm": "yes"}, follow=True)
        self.assertEqual(Deck.objects.count(), 0)
        self.assertEqual(DeletedDeck.objects.count(), 2)
        self.assertContains(resp, "Deleted 2 decks")
        self.assertTrue(CollectionItem.objects.filter(card=self.ring).exists())

    def test_delete_for_good_and_old_ones_are_cleared(self):
        from datetime import timedelta
        from django.utils import timezone
        from . import trash
        gone = trash.delete_deck(self.deck)
        purge = reverse("collection:deck_purge", args=[gone.pk])
        self.client.post(purge)
        self.assertTrue(DeletedDeck.objects.filter(pk=gone.pk).exists())
        self.client.post(purge, {"confirm": "yes"})
        self.assertFalse(DeletedDeck.objects.filter(pk=gone.pk).exists())

        old = trash.delete_deck(self.other)
        DeletedDeck.objects.filter(pk=old.pk).update(deleted_at=timezone.now() - timedelta(days=trash.KEEP_DAYS + 1))
        trash.purge()
        self.assertFalse(DeletedDeck.objects.exists())

    def test_put_back_skips_cards_no_longer_known(self):
        from . import trash
        gone = trash.delete_deck(self.deck)
        self.ring.delete()
        deck, skipped = trash.restore(gone)
        self.assertEqual(skipped, 1)
        self.assertEqual(list(deck.entries.values_list("card__name", flat=True)), ["Lightning Bolt"])

    def test_decks_page_has_delete_controls(self):
        page = self.client.get(reverse("collection:decks"))
        self.assertContains(page, "Delete deck…")
        self.assertContains(page, "Select decks")
        self.assertContains(page, reverse("collection:deck_delete", args=[self.deck.pk]))


class DeckCollectTests(TestCase):
    def setUp(self):
        self.ring = make_card("Sol Ring", type_line="Artifact")
        self.ring2 = make_card("Sol Ring", type_line="Artifact", set_code="c21", scryfall_id="id-sol-ring-c21")
        self.bolt = make_card("Lightning Bolt", type_line="Instant")
        self.outsider = make_card("Counterspell", type_line="Instant")
        CollectionItem.objects.create(card=self.bolt, quantity=1)
        self.deck = Deck.objects.create(name="Mix", format="casual")
        DeckCard.objects.create(deck=self.deck, card=self.ring, quantity=1)
        DeckCard.objects.create(deck=self.deck, card=self.bolt, quantity=3)
        self.url = reverse("collection:deck_card_collect", args=[self.deck.pk])

    def test_adds_copies_and_fills_matching_wants(self):
        want_any = WantItem.objects.create(card=self.ring2, any_printing=True, quantity=1)
        foil_only = WantItem.objects.create(card=self.ring, any_printing=False, finish="foil", quantity=1)
        resp = self.client.post(self.url, {"card_id": self.ring.pk, "quantity": 1, "finish": "nonfoil"}, follow=True)
        item = CollectionItem.objects.get(card=self.ring)
        self.assertEqual((item.quantity, item.finish), (1, "nonfoil"))
        self.assertFalse(WantItem.objects.filter(pk=want_any.pk).exists())
        self.assertTrue(WantItem.objects.filter(pk=foil_only.pk).exists())  # wrong finish, untouched
        self.assertContains(resp, "Took 1 off your want list")

    def test_adds_to_an_existing_stack(self):
        self.client.post(self.url, {"card_id": self.bolt.pk, "quantity": 2})
        self.assertEqual(CollectionItem.objects.get(card=self.bolt, finish="nonfoil").quantity, 3)

    def test_only_cards_in_the_deck(self):
        self.client.post(self.url, {"card_id": self.outsider.pk, "quantity": 1})
        self.assertFalse(CollectionItem.objects.filter(card=self.outsider).exists())

    def test_every_view_offers_it_with_the_shortfall(self):
        base = reverse("collection:deck", args=[self.deck.pk])
        table = self.client.get(base + "?view=table")
        self.assertContains(table, "Add to collection")
        self.assertContains(table, 'name="quantity" min="1" max="999" value="2"')  # 3 used, own 1
        self.assertContains(table, "Got 2")  # Cards you need list
        self.assertContains(self.client.get(base + "?view=stacks"), 'aria-label="Add 2 Lightning Bolt to my collection"')
        text = self.client.get(base + "?view=text")
        self.assertContains(text, 'id="txt-collect-form"')
        self.assertContains(text, 'data-collect="2"')
