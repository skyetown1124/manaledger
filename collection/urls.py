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
    path("refresh/", views.refresh, name="refresh"),
    path("api/autocomplete/", views.autocomplete, name="autocomplete"),
]
