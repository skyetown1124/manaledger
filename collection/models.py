from decimal import Decimal

from django.db import models


class Card(models.Model):
    """One printing of a Magic card, as Scryfall describes it."""

    RARITY_ORDER = ["mythic", "rare", "uncommon", "common", "special", "bonus"]

    scryfall_id = models.CharField(max_length=36, unique=True)
    name = models.CharField(max_length=200)
    set_code = models.CharField(max_length=10)
    set_name = models.CharField(max_length=120)
    collector_number = models.CharField(max_length=20)
    rarity = models.CharField(max_length=20)
    colors = models.CharField(max_length=10, blank=True, help_text="Color letters, e.g. 'WU'. Blank = colorless.")
    type_line = models.CharField(max_length=200, blank=True)
    released_at = models.DateField(null=True, blank=True)
    image_url = models.URLField(max_length=500, blank=True)
    scryfall_uri = models.URLField(max_length=500, blank=True)

    # Rules details, used by card search and the deck builder.
    mana_cost = models.CharField(max_length=100, blank=True, help_text="e.g. '{2}{U}{U}'")
    cmc = models.FloatField(default=0, verbose_name="mana value")
    oracle_text = models.TextField(blank=True)
    keywords = models.CharField(max_length=400, blank=True, help_text="Comma-separated, e.g. 'Flying, Ward'")
    power = models.CharField(max_length=10, blank=True)
    toughness = models.CharField(max_length=10, blank=True)
    loyalty = models.CharField(max_length=10, blank=True)
    color_identity = models.CharField(max_length=10, blank=True)
    legalities = models.JSONField(default=dict, blank=True, help_text="Format -> 'legal', 'not_legal', 'banned' or 'restricted'")
    details_loaded = models.BooleanField(default=False, help_text="False until Scryfall has sent this card's text.")

    usd = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    usd_foil = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    usd_etched = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    prices_updated = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name", "set_code"]

    @property
    def is_basic_land(self):
        return "Basic" in self.type_line and "Land" in self.type_line

    @property
    def best_price(self):
        """Non-foil price, falling back to foil, then etched."""
        for price in (self.usd, self.usd_foil, self.usd_etched):
            if price is not None:
                return price
        return None

    def __str__(self):
        return f"{self.name} ({self.set_code.upper()} #{self.collector_number})"

    def price_for(self, finish):
        """Market price in USD for a finish, or None when Scryfall has no price."""
        return {"nonfoil": self.usd, "foil": self.usd_foil, "etched": self.usd_etched}.get(finish)


class CollectionItem(models.Model):
    """Copies of one printing (in one finish) that you own."""

    FINISH_CHOICES = [
        ("nonfoil", "Non-foil"),
        ("foil", "Foil"),
        ("etched", "Etched foil"),
    ]

    card = models.ForeignKey(Card, on_delete=models.CASCADE, related_name="items")
    finish = models.CharField(max_length=10, choices=FINISH_CHOICES, default="nonfoil")
    quantity = models.PositiveIntegerField(default=1)
    purchase_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="What you paid per copy (optional).",
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["card__name"]
        constraints = [
            models.UniqueConstraint(fields=["card", "finish"], name="one_item_per_card_finish"),
        ]

    def __str__(self):
        return f"{self.quantity} x {self.card} [{self.finish}]"

    @property
    def unit_price(self):
        return self.card.price_for(self.finish)

    @property
    def total_value(self):
        price = self.unit_price
        return (price or Decimal("0")) * self.quantity


class PriceSnapshot(models.Model):
    """A card's Scryfall prices on one day. Builds up price history over time."""

    card = models.ForeignKey(Card, on_delete=models.CASCADE, related_name="snapshots")
    date = models.DateField()
    usd = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    usd_foil = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    usd_etched = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(fields=["card", "date"], name="one_snapshot_per_card_day"),
        ]

    def price_for(self, finish):
        return {"nonfoil": self.usd, "foil": self.usd_foil, "etched": self.usd_etched}.get(finish)


class Deck(models.Model):
    FORMAT_CHOICES = [
        ("commander", "Commander"),
        ("standard", "Standard"),
        ("pioneer", "Pioneer"),
        ("modern", "Modern"),
        ("legacy", "Legacy"),
        ("vintage", "Vintage"),
        ("pauper", "Pauper"),
        ("casual", "Casual (no rules)"),
    ]

    name = models.CharField(max_length=100)
    format = models.CharField(max_length=20, choices=FORMAT_CHOICES, default="commander")
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name


class DeckCard(models.Model):
    """Copies of one printing in one part of a deck."""

    BOARD_CHOICES = [
        ("commander", "Commander"),
        ("main", "Main deck"),
        ("side", "Sideboard"),
    ]

    deck = models.ForeignKey(Deck, on_delete=models.CASCADE, related_name="entries")
    card = models.ForeignKey(Card, on_delete=models.CASCADE, related_name="deck_entries")
    board = models.CharField(max_length=10, choices=BOARD_CHOICES, default="main")
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["card__name"]
        constraints = [
            models.UniqueConstraint(fields=["deck", "card", "board"], name="one_entry_per_card_board"),
        ]

    def __str__(self):
        return f"{self.quantity} x {self.card.name} ({self.board}) in {self.deck}"


class CollectionSnapshot(models.Model):
    """Your whole collection's value on one day, saved each time prices refresh."""

    date = models.DateField(unique=True)
    total_value = models.DecimalField(max_digits=12, decimal_places=2)
    card_count = models.PositiveIntegerField()

    class Meta:
        ordering = ["date"]

    def __str__(self):
        return f"{self.date}: ${self.total_value}"
