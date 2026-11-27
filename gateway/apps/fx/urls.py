from django.urls import path

from apps.fx.api import RatesView

urlpatterns = [path("fx_rates", RatesView.as_view(), name="fx-rates")]
