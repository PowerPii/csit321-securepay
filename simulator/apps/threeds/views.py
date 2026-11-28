"""SecurePay Bank's 3-D Secure API (/sim/3ds), called only by the gateway.

The gateway relays the cardholder's code from its checkout: in production the simulator is
private, so the bank's challenge screen is drawn by the checkout. Every reply is signed.
"""
import json

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.common.http import ApiError, gateway_only, json_reply
from apps.threeds import service


def _body(request):
    try:
        return json.loads(request.body or b"null")
    except ValueError:
        raise ApiError(400, "invalid_request", "Body must be JSON.") from None


@csrf_exempt
@require_POST
@gateway_only
def authenticate(request):
    reply, created = service.authenticate(service.parse(_body(request)))
    return json_reply(reply, 201 if created else 200, signed=True)


@require_GET
@gateway_only
def detail(request, three_ds_id: str):
    return json_reply(service.to_dict(service.get(three_ds_id)), signed=True)


@csrf_exempt
@require_POST
@gateway_only
def challenge(request, three_ds_id: str):
    return json_reply(service.challenge(three_ds_id, _body(request)), signed=True)
