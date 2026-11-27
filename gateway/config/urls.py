from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView

from apps.common.views import HealthView, ReadinessView
from apps.events.mail_events import ResendWebhookView
from apps.status.views import PublicStatusView

urlpatterns = [
    path("healthz", HealthView.as_view(), name="health"),
    path("readyz", ReadinessView.as_view(), name="ready"),
    path("status.json", PublicStatusView.as_view(), name="public-status"),
    path("webhooks/resend", ResendWebhookView.as_view(), name="resend-webhook"),
    path("v1/", include("apps.payments.urls")),
    path("v1/", include("apps.refunds.urls")),
    path("v1/", include("apps.ledger.urls")),
    path("v1/", include("apps.fx.urls")),
    path("checkout/", include("apps.checkout.urls")),
    path("guest/status/", include("apps.guest.urls")),
    path("customer/", include("apps.customers.urls")),
    path("internal/", include("apps.internal.urls")),
]

# The OpenAPI schema lists every route, the staff API's included, so it is only served in
# local development (DEBUG), never on the public server.
if settings.DEBUG:
    urlpatterns.insert(0, path("api/schema/", SpectacularAPIView.as_view(), name="schema"))

if settings.ADMIN_ENABLED:
    urlpatterns.insert(0, path("admin/", admin.site.urls))
