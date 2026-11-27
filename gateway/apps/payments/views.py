"""Merchant payment endpoints: POST /v1/payments and GET /v1/payments/{id} (UC-53, UC-27)."""
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator, URLValidator
from rest_framework import status
from rest_framework.response import Response

from apps.audit.models import AuditEvent
from apps.audit.models import record as audit
from apps.common.errors import ApiError, invalid, not_found
from apps.fx import currencies
from apps.fx import rates as fx_rates
from apps.merchants.api import MerchantAPIView
from apps.payments import settlement
from apps.payments.idempotency import run_idempotent
from apps.payments.models import Operation, Payment
from apps.payments.serializers import payment_to_dict
from apps.security.limits import current as current_limits

CREATE_FIELDS = ("amount", "currency", "order_reference", "return_url", "customer_email",
                 "description", "line_items")
LINE_ITEM_FIELDS = {"name", "quantity", "unit_amount"}
MAX_LINE_ITEMS = 50
REQUIRED_FIELDS = ("amount", "currency", "order_reference", "return_url")
PRINTABLE_ASCII = re.compile(r"^[\x20-\x7E]+$")


def validate_create(body: dict, merchant) -> dict:
    """Check a create-payment body against CreatePaymentRequest; return clean fields."""
    rules = settings.GATEWAY_RULES
    for name in body:
        if name not in CREATE_FIELDS:
            raise invalid("invalid_field", f"Unknown field '{name}'.", param=name)
    for name in REQUIRED_FIELDS:
        if body.get(name) is None:
            raise invalid("missing_field", f"'{name}' is required.", param=name)

    amount = body["amount"]
    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 1:
        raise invalid("invalid_field", "'amount' must be a positive integer in the currency's "
                      "smallest unit (cents; whole yen or rupiah).", param="amount")
    currency = body["currency"]
    if currency not in currencies.CODES:
        raise invalid("currency_not_supported",
                      f"Supported currencies: {', '.join(currencies.CODES)}.", param="currency")
    if amount > 2_000_000_000:
        raise invalid("amount_too_large", "The amount is too large.", param="amount")
    # v2 multi-currency: the rate is locked now, and the limits apply to the SGD amount.
    fx = fx_rates.lock(currency)
    sgd = fx_rates.to_sgd(amount, currency, fx.rate)
    shown = "" if currency == currencies.SETTLEMENT else f" (this is {_sgd(sgd)})"
    if sgd < rules["MIN_AMOUNT_CENTS"]:
        raise invalid("amount_too_small",
                      f"The minimum amount is {_sgd(rules['MIN_AMOUNT_CENTS'])}{shown}.",
                      param="amount")
    if sgd > rules["MAX_AMOUNT_CENTS"]:
        raise invalid("amount_too_large",
                      f"The maximum amount is {_sgd(rules['MAX_AMOUNT_CENTS'])}{shown}.",
                      param="amount")

    order_reference = body["order_reference"]
    if (not isinstance(order_reference, str) or not 1 <= len(order_reference) <= 64
            or not PRINTABLE_ASCII.match(order_reference)):
        raise invalid("invalid_field", "'order_reference' must be 1 to 64 printable ASCII "
                      "characters.", param="order_reference")

    return_url = body["return_url"]
    if not isinstance(return_url, str) or len(return_url) > 2048:
        raise invalid("invalid_field", "'return_url' must be a URL of at most 2048 characters.",
                      param="return_url")
    try:
        URLValidator(schemes=["http", "https"])(return_url)
    except ValidationError:
        raise invalid("invalid_field", "'return_url' is not a valid URL.",
                      param="return_url") from None
    if not return_url.lower().startswith("https://"):
        raise invalid("return_url_not_https", "'return_url' must use HTTPS.", param="return_url")

    customer_email = body.get("customer_email")
    if customer_email is not None:
        try:
            if not isinstance(customer_email, str) or len(customer_email) > 254:
                raise ValidationError("bad")
            EmailValidator()(customer_email)
        except ValidationError:
            raise invalid("invalid_field", "'customer_email' is not a valid email address.",
                          param="customer_email") from None

    description = body.get("description")
    if description is not None and (not isinstance(description, str) or len(description) > 200):
        raise invalid("invalid_field", "'description' must be text of at most 200 characters.",
                      param="description")

    line_items = validate_line_items(body.get("line_items"), amount)

    # FR-ADM-07: the administrator's gateway-wide limit, then the merchant's own; both
    # before screening.
    if sgd > current_limits().max_amount_cents:
        raise ApiError(422, "amount_above_limit",
                       "The amount exceeds the limit per payment set by the gateway.",
                       param="amount")
    if merchant.max_amount_cents is not None and sgd > merchant.max_amount_cents:
        raise ApiError(422, "amount_above_merchant_limit",
                       "The amount is above the limit set for your account.", param="amount")

    return {
        "amount": amount,
        "currency": currency,
        "fx_rate": fx.rate,
        "fx_rate_sheet": fx.sheet_id,
        "fx_fee_bps": fx.fee_bps,
        "settlement_amount": sgd,
        "order_reference": order_reference,
        "return_url": return_url,
        "customer_email": customer_email or "",
        "description": description or "",
        "line_items": line_items,
    }


def _sgd(cents: int) -> str:
    return currencies.format_amount(cents, currencies.SETTLEMENT)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_line_items(items, amount: int) -> list[dict] | None:
    """Optional order lines shown at checkout; they must add up to the amount exactly."""
    if items is None:
        return None
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_LINE_ITEMS:
        raise invalid("invalid_field", f"'line_items' must be a list of 1 to {MAX_LINE_ITEMS} "
                      "items.", param="line_items")
    clean = []
    for i, item in enumerate(items):
        param = f"line_items[{i}]"
        if not isinstance(item, dict) or set(item) != LINE_ITEM_FIELDS:
            raise invalid("invalid_field", "Each line item needs exactly name, quantity and "
                          "unit_amount.", param=param)
        name, quantity, unit = item["name"], item["quantity"], item["unit_amount"]
        if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 100
                or any(ord(c) < 32 or ord(c) == 127 for c in name)):
            raise invalid("invalid_field", "'name' must be 1 to 100 characters of text.",
                          param=f"{param}.name")
        if not _is_int(quantity) or not 1 <= quantity <= 999:
            raise invalid("invalid_field", "'quantity' must be 1 to 999.",
                          param=f"{param}.quantity")
        if not _is_int(unit) or not 0 <= unit <= 2_000_000_000:
            raise invalid("invalid_field", "'unit_amount' must be 0 or more, in the payment "
                          "currency's smallest unit.",
                          param=f"{param}.unit_amount")
        clean.append({"name": name.strip(), "quantity": quantity, "unit_amount": unit})
    total = sum(item["quantity"] * item["unit_amount"] for item in clean)
    if total != amount:
        raise invalid("line_items_total_mismatch",
                      f"The line items add up to {total} but 'amount' is {amount}.",
                      param="line_items")
    return clean


class PaymentListView(MerchantAPIView):
    def post(self, request):
        merchant, api_key = request.user, request.auth

        def create(body):
            from apps.redteam import service as red_team

            label = red_team.label_from_headers(request, merchant, api_key)  # v2, test only
            fields = validate_create(body, merchant)
            payment = Payment.create_for_merchant(merchant, livemode=api_key.livemode, **fields)
            red_team.record(label, payment)
            audit(
                "payment.created",
                actor_type=AuditEvent.ActorType.MERCHANT_API,
                actor_id=api_key.public_id,
                merchant_id=merchant.public_id,
                target_type="payment",
                target_id=payment.public_id,
                request_id=request.request_id,
                details={"amount": payment.amount, "currency": payment.currency,
                         "settlement_amount": payment.settlement_amount,
                         "order_reference": payment.order_reference},
            )
            return Response(payment_to_dict(payment), status=status.HTTP_201_CREATED)

        return run_idempotent(request, create)


class PaymentDetailView(MerchantAPIView):
    def get(self, request, payment_id: str):
        # Another merchant's payment is indistinguishable from one that does not exist.
        payment = Payment.objects.filter(public_id=payment_id, merchant=request.user,
                                         livemode=request.auth.livemode).first()
        if payment is None:
            raise not_found()
        return Response(payment_to_dict(payment))



class _SettlementView(MerchantAPIView):
    """POST /v1/payments/{id}/capture and /cancel (UC-24, contract section 3).

    200 with the payment when the simulator confirmed it; 202 with `pending_operation` when
    it did not answer (reconciliation finishes it). Runs non-atomically so the operation is
    committed before the simulator is called.
    """

    op_type: str = ""

    def post(self, request, payment_id: str):
        merchant, api_key = request.user, request.auth
        key = request.headers.get("Idempotency-Key", "")

        def run(body):
            actor = settlement.Actor(AuditEvent.ActorType.MERCHANT_API, api_key.public_id,
                                     request.request_id)
            operation, created = settlement.start(payment_id, self.op_type, actor,
                                                  merchant=merchant, body=body,
                                                  request_key=key,
                                                  livemode=api_key.livemode)
            payment, operation = settlement.settle(operation, created, request.request_id)
            if operation.status == Operation.Status.SUCCEEDED:
                return Response(payment_to_dict(payment), status=status.HTTP_200_OK)
            if operation.status in Operation.OPEN_STATUSES:
                return Response(payment_to_dict(payment), status=status.HTTP_202_ACCEPTED)
            if operation.status == Operation.Status.NOT_PROCESSED:
                raise ApiError(409, "operation_not_processed",
                               "The processor has no record of this request, so nothing "
                               "changed. Try again with a new Idempotency-Key.")
            raise ApiError(409, "processor_rejected",
                           f"The processor refused the {self.op_type}. Nothing changed; "
                           "check the payment and contact support if it persists.")

        return run_idempotent(request, run, atomic=False)


class PaymentCaptureView(_SettlementView):
    op_type = Operation.Type.CAPTURE


class PaymentCancelView(_SettlementView):
    op_type = Operation.Type.CANCEL
