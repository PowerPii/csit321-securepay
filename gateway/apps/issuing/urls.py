"""Merchant API (API key): issued cards. Creating one needs an Idempotency-Key."""
from django.urls import path

from apps.issuing.api import CardCancelView, CardDetailView, CardListView

urlpatterns = [
    path("issuing/cards", CardListView.as_view(), name="issued-card-list"),
    path("issuing/cards/<str:card_id>", CardDetailView.as_view(), name="issued-card-detail"),
    path("issuing/cards/<str:card_id>/cancel", CardCancelView.as_view(),
         name="issued-card-cancel"),
]
