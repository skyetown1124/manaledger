from django.urls import path

from . import views

app_name = "collection"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("cards/", views.cards, name="cards"),
    path("cards/<int:pk>/update/", views.item_update, name="item_update"),
    path("cards/<int:pk>/delete/", views.item_delete, name="item_delete"),
    path("add/", views.add_cards, name="add"),
    path("add/save/", views.add_card, name="add_card"),
    path("decks/", views.deck_list, name="decks"),
    path("decks/new/", views.deck_create, name="deck_create"),
    path("decks/<int:pk>/", views.deck_detail, name="deck"),
    path("decks/<int:pk>/add/", views.deck_add, name="deck_add"),
    path("decks/<int:pk>/basics/", views.deck_add_basics, name="deck_add_basics"),
    path("decks/<int:pk>/settings/", views.deck_update, name="deck_update"),
    path("decks/<int:pk>/delete/", views.deck_delete, name="deck_delete"),
    path("decks/entry/<int:pk>/", views.deck_entry_update, name="deck_entry_update"),
    path("refresh/", views.refresh, name="refresh"),
    path("api/autocomplete/", views.autocomplete, name="autocomplete"),
]
