"""Simulator API views (contracts/openapi/simulator.yaml). Called only by the gateway.

Fault rules (SIMULATOR-CONTRACT.md section 8) are checked here: one request rule per new
operation, after the replay check so a replay never uses one up, then any notify rules
once the operation is recorded. The test cards behave like built-in rules.
"""
import base64
import time
from datetime import datetime

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.common.http import ApiError, error_reply, gateway_only, json_reply
from apps.faults import notify
from apps.faults import service as faults
from apps.faults.models import FaultRule
from apps.network import acquirer
from apps.operations import service
from apps.operations.models import OperationRecord
from apps.operations.test_cards import Behaviour, behaviour_for

CONFLICT_MESSAGES = {
    "operation_retired": "This operation_id was reported as no_record and can never be "
                         "processed.",
    "operation_id_conflict": "This operation_id was already used with a different request.",
}


def _hold_then_hang_up() -> HttpResponse:
    """Stand-in for "never replies": hold the connection, then end it with no record reply.

    Django cannot drop a connection, so after the hold we send an unsigned 504. The gateway
    has given up long before (10 s, proposal S1) and an unsigned reply is never evidence.
    """
    time.sleep(settings.SIM_HOLD_SECONDS)
    return HttpResponse(status=504)


def _existing_reply(request, exc: service.Existing) -> HttpResponse:
    data = service.to_dict(exc.record)
    if exc.conflict_code:
        return error_reply(request, 409, exc.conflict_code, CONFLICT_MESSAGES[exc.conflict_code],
                           "operation_id", operation=data)
    return json_reply(data, signed=True, replayed=True)


def _sleep_ms(rule: FaultRule) -> None:
    time.sleep((rule.delay_ms or 0) / 1000)


def _before_recording(rule: FaultRule | None) -> HttpResponse | None:
    """drop_request answers for the operation; delay_request waits, then records."""
    if rule is None:
        return None
    if rule.action == FaultRule.Action.DROP_REQUEST:
        return _hold_then_hang_up()  # lost request: nothing recorded
    if rule.action == FaultRule.Action.DELAY_REQUEST:
        _sleep_ms(rule)  # a status query meanwhile retires the ID (409 operation_retired)
    return None


def _reply_recorded(record: OperationRecord, context: dict, rule: FaultRule | None
                    ) -> HttpResponse:
    data = service.to_dict(record)
    notify.schedule(data, faults.take_notify_rules(context))
    if rule is not None and rule.action == FaultRule.Action.DROP_RESPONSE:
        return _hold_then_hang_up()  # lost response: recorded, never answered
    if rule is not None and rule.action == FaultRule.Action.DELAY_RESPONSE:
        _sleep_ms(rule)
    return json_reply(data, signed=True)


@csrf_exempt
@require_POST
@gateway_only
def authorise(request):
    body = service.parse_authorise(request.body)
    request_fingerprint = service.fingerprint(body)
    context = faults.context_for(body, OperationRecord.Type.AUTHORISE)
    context["card_last4"] = body["card"]["number"][-4:]
    try:
        service.check_existing(body["operation_id"], request_fingerprint)
        behaviour = behaviour_for(body["card"]["number"])
        if behaviour is Behaviour.DROP_REQUEST:
            return _hold_then_hang_up()  # lost request: nothing recorded
        rule = faults.take_request_rule(context)
        early = _before_recording(rule)
        if early is not None:
            return early
        decision = acquirer.authorise(body)  # 0100 to the switch, 0110 back from the issuer
        record = service.record_authorise(body, decision, request_fingerprint)
    except service.Existing as exc:
        return _existing_reply(request, exc)

    if rule is None and record.status == OperationRecord.Status.APPROVED:
        if behaviour is Behaviour.TIMEOUT:
            rule = FaultRule(action=FaultRule.Action.DROP_RESPONSE)
        elif behaviour is Behaviour.SLOW:  # replies after the gateway gave up
            rule = FaultRule(action=FaultRule.Action.DELAY_RESPONSE,
                             delay_ms=int(settings.SIM_HOLD_SECONDS * 1000))
    return _reply_recorded(record, context, rule)


@require_GET
@gateway_only
def operation_status(request, operation_id: str):
    if not service.OPERATION_ID.match(operation_id):
        raise ApiError(400, "invalid_field", "Not an operation ID.", "operation_id")
    op_type = request.GET.get("type")
    if op_type is not None and op_type not in OperationRecord.Type.values:
        raise ApiError(400, "invalid_field", "'type' must be authorise, capture, cancel or "
                       "refund.", "type")
    if settings.SIM_TEST_CONTROLS:
        existing = OperationRecord.objects.filter(operation_id=operation_id).first()
        context = ({"operation_type": op_type} if existing is None else
                   faults.context_for(existing.__dict__, existing.type))
        if faults.take_status_rule(context) is not None:
            # Unsigned and nothing retired: the gateway counts it as a check with no answer.
            raise ApiError(503, "service_unavailable", "Status queries are failing (fault).")
    record = service.status_query(operation_id, op_type)
    return json_reply(service.to_dict(record), signed=True)


def _cursor(pk: int) -> str:
    return base64.urlsafe_b64encode(f"o:{pk}".encode()).decode().rstrip("=")


def _after_pk(cursor: str) -> int:
    try:
        text = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        kind, _, pk = text.partition(":")
        if kind != "o":
            raise ValueError
        return int(pk)
    except (ValueError, UnicodeDecodeError):
        raise ApiError(400, "invalid_field", "'cursor' is not valid.", "cursor") from None


@require_GET
@gateway_only
def operation_list(request):
    """Records oldest first, `no_record` entries included; never retires an ID (FR-TXN-11)."""
    records = OperationRecord.objects.select_related("authorisation").order_by("pk")
    if after := request.GET.get("recorded_after"):
        try:
            moment = datetime.fromisoformat(after)
        except ValueError:
            raise ApiError(400, "invalid_field", "'recorded_after' must be an RFC 3339 time.",
                           "recorded_after") from None
        if moment.tzinfo is None:
            raise ApiError(400, "invalid_field", "'recorded_after' needs a time zone.",
                           "recorded_after")
        records = records.filter(recorded_at__gt=moment)
    if payment_id := request.GET.get("payment_id"):
        if not service.PAYMENT_ID.match(payment_id):
            raise ApiError(400, "invalid_field", "Not a payment ID.", "payment_id")
        records = records.filter(payment_id=payment_id)
    if cursor := request.GET.get("cursor"):
        records = records.filter(pk__gt=_after_pk(cursor))
    try:
        limit = int(request.GET.get("limit", "100"))
    except ValueError:
        limit = 0
    if not 1 <= limit <= 500:
        raise ApiError(400, "invalid_field", "'limit' must be 1 to 500.", "limit")
    page = list(records[:limit + 1])
    more = len(page) > limit
    page = page[:limit]
    return json_reply({"data": [service.to_dict(r) for r in page],
                       "next_cursor": _cursor(page[-1].pk) if more else None}, signed=True)


def _settlement_context(body: dict, op_type: str) -> dict:
    auth = (OperationRecord.objects.filter(operation_id=body["authorisation_id"])
            .values("payment_id", "merchant_id", "card_last4").first()) or {}
    return faults.context_for(auth, op_type)


def _settle(request, parse, op_type):
    body = parse(request.body)
    request_fingerprint = service.fingerprint(body)
    context = _settlement_context(body, op_type)
    try:
        service.check_existing(body["operation_id"], request_fingerprint)
        rule = faults.take_request_rule(context)
        early = _before_recording(rule)
        if early is not None:
            return early
        if op_type == OperationRecord.Type.REFUND:
            record = service.record_refund(body, request_fingerprint)
        else:
            record = service.record_settlement(body, op_type, request_fingerprint)
    except service.Existing as exc:
        return _existing_reply(request, exc)
    return _reply_recorded(record, context, rule)


@csrf_exempt
@require_POST
@gateway_only
def capture(request):
    return _settle(request, service.parse_capture, OperationRecord.Type.CAPTURE)


@csrf_exempt
@require_POST
@gateway_only
def cancel(request):
    return _settle(request, service.parse_cancel, OperationRecord.Type.CANCEL)


@csrf_exempt
@require_POST
@gateway_only
def refund(request):
    return _settle(request, service.parse_refund, OperationRecord.Type.REFUND)
