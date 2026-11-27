"""The switch and issuer endpoints, used only when the nodes run in their own containers.

Requests and replies are signed (signing.py); an unsigned request gets a bare 401. These
routes are mounted only for the matching SIM_ROLE (config/urls.py) and are never public.
"""
import json

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.network import nodes, signing, transport


def _signed(data: dict) -> HttpResponse:
    body = json.dumps(data, separators=(",", ":")).encode()
    response = HttpResponse(body, content_type="application/json")
    response[signing.SIGNATURE_HEADER] = signing.sign(body)
    return response


def _message(request) -> dict | None:
    if not signing.verify(request.body, request.headers.get(signing.SIGNATURE_HEADER)):
        return None
    try:
        data = json.loads(request.body)
    except ValueError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("fields"), dict) else None


@csrf_exempt
@require_POST
def switch(request):
    message = _message(request)
    if message is None:
        return HttpResponse(status=401)
    return _signed(nodes.switch(message, transport.to_issuer))


@csrf_exempt
@require_POST
def issuer(request):
    message = _message(request)
    if message is None:
        return HttpResponse(status=401)
    return _signed(nodes.issuer(message))
