from django.contrib import admin

from .models import Card, CollectionItem, CollectionSnapshot, Deck, DeckCard, PriceSnapshot


@admin.register(Card)
class CardAdmin(admin.ModelAdmin):
    list_display = ("name", "set_code", "collector_number", "rarity", "mana_cost", "usd", "usd_foil", "prices_updated")
    search_fields = ("name", "set_name", "set_code", "type_line", "oracle_text")
    list_filter = ("rarity", "set_code", "details_loaded")


class DeckCardInline(admin.TabularInline):
    model = DeckCard
    extra = 0
    autocomplete_fields = ("card",)


@admin.register(Deck)
class DeckAdmin(admin.ModelAdmin):
    list_display = ("name", "format", "updated_at")
    list_filter = ("format",)
    search_fields = ("name",)
    inlines = [DeckCardInline]


@admin.register(CollectionItem)
class CollectionItemAdmin(admin.ModelAdmin):
    list_display = ("card", "finish", "quantity", "purchase_price", "added_at")
    search_fields = ("card__name",)
    list_filter = ("finish",)


@admin.register(PriceSnapshot)
class PriceSnapshotAdmin(admin.ModelAdmin):
    list_display = ("card", "date", "usd", "usd_foil", "usd_etched")
    list_filter = ("date",)


@admin.register(CollectionSnapshot)
class CollectionSnapshotAdmin(admin.ModelAdmin):
    list_display = ("date", "total_value", "card_count")
