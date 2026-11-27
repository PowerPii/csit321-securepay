"""Shopper passkeys, served under /customer/passkeys (docs/v2/PASSKEYS.md)."""
from django.urls import path

from apps.passkeys import views

urlpatterns = [
    path("passkeys", views.PasskeysView.as_view(), name="customer-passkeys"),
    path("passkeys/register/options", views.RegisterOptionsView.as_view(),
         name="customer-passkey-register-options"),
    path("passkeys/register", views.RegisterView.as_view(), name="customer-passkey-register"),
    path("passkeys/sign-in/options", views.SignInOptionsView.as_view(),
         name="customer-passkey-sign-in-options"),
    path("passkeys/sign-in", views.SignInView.as_view(), name="customer-passkey-sign-in"),
    path("passkeys/<str:passkey_id>/remove", views.RemoveView.as_view(),
         name="customer-passkey-remove"),
]
