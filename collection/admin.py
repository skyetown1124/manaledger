from django.contrib import admin

from .models import Card, CollectionItem, CollectionSnapshot, PriceSnapshot


@admin.register(Card)
class CardAdmin(admin.ModelAdmin):
    list_display = ("name", "set_code", "collector_number", "rarity", "usd", "usd_foil", "prices_updated")
    search_fields = ("name", "set_name", "set_code")
    list_filter = ("rarity", "set_code")


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
