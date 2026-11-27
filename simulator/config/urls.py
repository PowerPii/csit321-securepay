from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from apps.network import urls as network_urls

urlpatterns = [path("healthz", lambda r: JsonResponse({"status": "ok"}))]

# Which API this container serves depends on its network role (SIM_ROLE, settings.py).
if settings.SIM_ROLE in {"all", "acquirer"}:
    urlpatterns.append(path("sim/", include("apps.operations.urls")))
if settings.SIM_ROLE in {"all", "switch"}:
    urlpatterns.append(path("net/", include(network_urls.switch_urls)))
if settings.SIM_ROLE in {"all", "issuer"}:
    urlpatterns.append(path("net/", include(network_urls.issuer_urls)))

if settings.ADMIN_ENABLED and settings.SIM_ROLE in {"all", "acquirer"}:
    urlpatterns.insert(0, path("admin/", admin.site.urls))
