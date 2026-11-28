"""Merchant API: issued cards (API-CONTRACT.md section 12, v2 issuing).

* POST /v1/issuing/cards {spending_limit, single_use?, description?}
       needs an Idempotency-Key; 201 with the card, its number and security code (once).
       The same key again answers 200 with the card and no number.
* GET  /v1/issuing/cards?limit=       the newest `limit` (default 10, at most 100)
* GET  /v1/issuing/cards/{id}         with the bank's spend and its recent authorisations
* POST /v1/issuing/cards/{id}/cancel

Another merchant's card, or a card of the other mode, answers 404.
"""
from rest_framework import status
from rest_framework.response import Response

from apps.audit.models import AuditEvent
from apps.common.errors import invalid, not_found
from apps.internal.query_params import bounded_int
from apps.issuing import service
from apps.issuing.models import IssuedCard
from apps.merchants.api import MerchantAPIView
from apps.payments.idempotency import HEADER, KEY_PATTERN, REPLAYED_HEADER, parse_json_body


def _card(request, card_id: str) -> IssuedCard:
    card = (IssuedCard.objects.select_related("merchant")
            .filter(public_id=card_id[:32], merchant=request.user,
                    livemode=request.auth.livemode).first())
    if card is None:
        raise not_found()
    return card


class CardListView(MerchantAPIView):
    def get(self, request):
        limit = bounded_int(request.query_params.get("limit"), "limit", 10, 1, 100)
        rows = IssuedCard.objects.filter(merchant=request.user, livemode=request.auth.livemode)
        page = list(rows.order_by("-created_at", "-id")[:limit + 1])
        return Response({"object": "list", "has_more": len(page) > limit,
                         "data": [service.to_dict(c) for c in page[:limit]]})

    def post(self, request):
        key = request.headers.get(HEADER, "")
        if not KEY_PATTERN.match(key):
            raise invalid("idempotency_key_missing" if not key else "idempotency_key_invalid",
                          "An Idempotency-Key header of 8 to 255 printable characters is "
                          "required.", HEADER)
        data = service.parse(parse_json_body(request))
        card, secret = service.create(
            request.user, data, key=key, livemode=request.auth.livemode,
            actor_type=AuditEvent.ActorType.MERCHANT_API, actor_id=request.auth.public_id,
            request_id=request.request_id)
        response = Response(service.to_dict(card, secret),
                            status=status.HTTP_201_CREATED if secret else status.HTTP_200_OK)
        if secret is None:
            response[REPLAYED_HEADER] = "true"
        response["Cache-Control"] = "no-store"
        return response


class CardDetailView(MerchantAPIView):
    def get(self, request, card_id: str):
        card = _card(request, card_id)
        return Response(service.to_dict(card, authorisations=service.fetch(card) or []))


class CardCancelView(MerchantAPIView):
    def post(self, request, card_id: str):
        card = service.cancel(_card(request, card_id),
                              actor_type=AuditEvent.ActorType.MERCHANT_API,
                              actor_id=request.auth.public_id, request_id=request.request_id)
        return Response(service.to_dict(card))
