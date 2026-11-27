"""Merchant API (API key): balance and payouts. POST needs an Idempotency-Key."""
from django.urls import path

from apps.ledger.api import BalanceView, PayoutDetailView, PayoutListView

urlpatterns = [
    path("balance", BalanceView.as_view(), name="balance"),
    path("payouts", PayoutListView.as_view(), name="payout-list"),
    path("payouts/<str:payout_id>", PayoutDetailView.as_view(), name="payout-detail"),
]
