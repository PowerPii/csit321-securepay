"""Issued cards on the staff dashboard (v2 issuing, docs/v2/ISSUING.md).

Merchant operators see and make their own merchant's cards; administrators see every
merchant's cards, read-only. Fraud analysts are refused. Every call takes `mode=test|live`.

* GET  issuing/cards?mode=&page=&page_size=
* POST issuing/cards {spending_limit, single_use?, description?, mode?}
       operator only; needs an Idempotency-Key header (the dashboard sends a new one per
       form). 201 with the number and security code, shown once.
* GET  issuing/cards/{id}        with the bank's recent authorisations
* POST issuing/cards/{id}/cancel operator only
"""
from rest_framework import exceptions, status
from rest_framework.response import Response

from apps.accounts.models import Role
from apps.common.errors import invalid, not_found
from apps.common.views import body_or_empty
from apps.internal.access import StaffAPIView
from apps.internal.query_params import bounded_int
from apps.issuing import service
from apps.issuing.models import IssuedCard
from apps.merchants.models import parse_mode
from apps.payments.idempotency import HEADER, KEY_PATTERN

OPERATOR_OR_ADMIN = (Role.MERCHANT_OPERATOR, Role.ADMINISTRATOR)


def _row(card: IssuedCard, **extra) -> dict:
    return {**service.to_dict(card, **extra), "merchant_id": card.merchant.public_id,
            "merchant_name": card.merchant.name}


class _Base(StaffAPIView):
    roles = OPERATOR_OR_ADMIN

    def cards(self):
        rows = IssuedCard.objects.select_related("merchant")
        if self.staff.is_operator:
            rows = rows.filter(merchant_id=self.staff.merchant_id)
        return rows

    def only_operator(self):
        if not self.staff.is_operator:
            raise exceptions.PermissionDenied("Only the merchant can make or cancel its cards.",
                                              code="role_not_allowed")

    def card(self, card_id: str) -> IssuedCard:
        card = self.cards().filter(public_id=card_id[:32]).first()
        if card is None:
            raise not_found()
        return card


class CardListView(_Base):
    def get(self, request):
        params = request.query_params
        rows = self.cards().filter(livemode=parse_mode(params.get("mode")))
        page = bounded_int(params.get("page"), "page", 1, 1, 100_000)
        size = bounded_int(params.get("page_size"), "page_size", 25, 1, 100)
        return Response({"object": "list", "count": rows.count(), "page": page,
                         "page_size": size,
                         "data": [_row(c) for c in rows[(page - 1) * size: page * size]]})

    def post(self, request):
        self.only_operator()
        key = request.headers.get(HEADER, "")
        if not KEY_PATTERN.match(key):
            raise invalid("idempotency_key_missing", "Send an Idempotency-Key header.", HEADER)
        body = body_or_empty(request)
        card, secret = service.create(
            self.staff.merchant, service.parse(body), key=key,
            livemode=parse_mode(body.get("mode")), actor_type="staff",
            actor_id=self.staff.public_id, request_id=getattr(request, "request_id", ""))
        response = Response(_row(card, secret=secret),
                            status=status.HTTP_201_CREATED if secret else status.HTTP_200_OK)
        response["Cache-Control"] = "no-store"
        return response


class CardDetailView(_Base):
    def get(self, request, card_id: str):
        card = self.card(card_id)
        authorisations = service.fetch(card)
        return Response({**_row(card, authorisations=authorisations or []),
                         "bank_reachable": authorisations is not None})


class CardCancelView(_Base):
    def post(self, request, card_id: str):
        self.only_operator()
        card = service.cancel(self.card(card_id), actor_type="staff",
                              actor_id=self.staff.public_id,
                              request_id=getattr(request, "request_id", ""))
        return Response(_row(card))
