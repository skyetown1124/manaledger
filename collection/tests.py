"""Run with:  python manage.py test collection"""
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from . import decks, scryfall, search
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
