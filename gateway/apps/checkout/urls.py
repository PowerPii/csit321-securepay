"""Hosted checkout API, used only by frontend/checkout. Session-bound; the browser never sets amount."""
from django.urls import path

from apps.checkout import views

urlpatterns = [
    path("<str:session_id>", views.SessionView.as_view(), name="checkout-session"),
    path("<str:session_id>/card", views.CardView.as_view(), name="checkout-card"),
    path("<str:session_id>/submit", views.SubmitView.as_view(), name="checkout-submit"),
    path("<str:session_id>/verify", views.VerifyView.as_view(), name="checkout-verify"),
    path("<str:session_id>/verify/resend", views.ResendView.as_view(),
         name="checkout-verify-resend"),
    path("<str:session_id>/verify/passkey/options", views.PasskeyOptionsView.as_view(),
         name="checkout-verify-passkey-options"),
    path("<str:session_id>/verify/passkey", views.PasskeyVerifyView.as_view(),
         name="checkout-verify-passkey"),
    path("<str:session_id>/result", views.ResultView.as_view(), name="checkout-result"),
]
