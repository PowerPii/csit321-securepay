"""SecurePay Bank's card-management API (v2 issuing), called only by the gateway.

Mounted under /sim/issuing with the rest of the simulator API, so it needs the gateway's
simulator key and every reply is signed. The card number and security code are in the
reply to the create call only; the bank keeps neither.
"""
import json

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.common.http import ApiError, gateway_only, json_reply
from apps.issuing import service
from apps.issuing.models import IssuedCard


def _card(card_id: str) -> IssuedCard:
    if not service.CARD_ID.match(card_id):
        raise ApiError(400, "invalid_field", "Not a card ID.", "card_id")
    card = IssuedCard.objects.filter(card_id=card_id).first()
    if card is None:
        raise ApiError(404, "not_found", "No such card.", "card_id")
    return card


@csrf_exempt
@require_POST
@gateway_only
def create(request):
    try:
        body = json.loads(request.body or b"null")
    except ValueError:
        raise ApiError(400, "invalid_request", "Body must be JSON.") from None
    card, secret = service.create(service.parse_create(body))
    return json_reply(service.to_dict(card, secret), 201 if secret else 200, signed=True,
                      replayed=secret is None)


@require_GET
@gateway_only
def detail(request, card_id: str):
    return json_reply(service.to_dict(_card(card_id)), signed=True)


@csrf_exempt
@require_POST
@gateway_only
def cancel(request, card_id: str):
    _card(card_id)
    return json_reply(service.to_dict(service.cancel(card_id)), signed=True)
