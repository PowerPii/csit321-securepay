from django.urls import path

from apps.network import views

switch_urls = [path("switch", views.switch, name="net-switch")]
issuer_urls = [path("issuer", views.issuer, name="net-issuer")]
