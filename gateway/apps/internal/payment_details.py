"""Staff payment-detail responses, action availability and correlated timelines.

Callers must obtain the payment through the staff access helpers before building a response.
"""
import json
import re

from django.db.models import Q

from apps.accounts.models import StaffAccount
from apps.audit.models import AuditEvent
from apps.common.timefmt import rfc3339
from apps.events.models import OutboxEvent
from apps.internal.serializers import mask_email, payment_row, screening_to_dict
from apps.payments import settlement
from apps.payments.models import Operation, Payment
from apps.payments.serializers import payment_to_dict
from apps.payments.states import PaymentStatus
from apps.refunds.models import Refund
from apps.refunds.serializers import refund_to_dict
from apps.risk.models import ScreeningEvent


def _payment_audit(payment: Payment):
    """Every audit entry about the payment: by correlation ID (FR-TXN-16), and for entries
    written before correlation IDs existed, by target or the refund's payment_id."""
    return (AuditEvent.objects.filter(
        Q(correlation_id=payment.public_id) | Q(target_id=payment.public_id)
        | Q(target_type="refund", details__payment_id=payment.public_id))
        .order_by("occurred_at", "id"))


# What merchant operators do not see of the fraud screening: the audit details that name
# reason codes and versions, and the fraud team's own review of the case (who looked at it,
# their finding). They see the decision itself and broad reasons (serializers.screening_to_dict).
FRAUD_DETAIL_KEYS = frozenset({"reasons", "model_version", "policy_version", "rules_version",
                               "score", "features", "explanation", "thresholds"})
FRAUD_TEAM_ACTIONS = ("review.",)


def _hidden_from_merchant(event: AuditEvent) -> bool:
    return event.action.startswith(FRAUD_TEAM_ACTIONS)


def _details(event: AuditEvent, merchant_view: bool) -> dict:
    details = event.details or {}
    if merchant_view and isinstance(details, dict):
        return {k: v for k, v in details.items() if k not in FRAUD_DETAIL_KEYS}
    return details


# Audit actions that describe the payment's own history (D4 "Status history").
def timeline(payment: Payment, merchant_view: bool = False) -> list[dict]:
    events = list(_payment_audit(payment))
    staff_ids = {e.actor_id for e in events if e.actor_type == AuditEvent.ActorType.STAFF}
    names = dict(StaffAccount.objects.filter(public_id__in=staff_ids)
                 .values_list("public_id", "user__email"))
    out = []
    for event in events:
        if event.action == "payment.viewed":
            continue  # access log, not history
        if merchant_view and _hidden_from_merchant(event):
            continue
        actor = event.actor_id
        if event.actor_type == AuditEvent.ActorType.STAFF:
            actor = names.get(event.actor_id, event.actor_id)
        out.append({
            "at": rfc3339(event.occurred_at),
            "action": event.action,
            "actor_type": event.actor_type,
            "actor": actor or None,
            "target_type": event.target_type,
            "target_id": event.target_id,
            "details": _details(event, merchant_view),
        })
    return out


def webhooks(payment: Payment) -> list[dict]:
    out = []
    for event in (OutboxEvent.objects.filter(payment=payment)
                  .prefetch_related("attempts").order_by("sequence")):
        attempts = list(event.attempts.all())
        last = attempts[-1] if attempts else None
        out.append({
            "id": event.public_id,
            "type": event.type,
            "sequence": event.sequence,
            "status": event.status,
            "attempts": event.attempt_count,
            "last_http_status": last.http_status if last else None,
            "last_error": (last.error or None) if last else None,
            "next_attempt_at": rfc3339(event.next_attempt_at)
            if event.status == OutboxEvent.Status.PENDING else None,
            "delivered_at": rfc3339(event.delivered_at),
        })
    return out


def trail(payment: Payment, screening, ops: list[Operation], refunds: list[Refund],
          hooks: list[dict], merchant_view: bool = False) -> list[dict]:
    """FR-TXN-16: one timeline of everything linked by the payment's correlation ID: the
    screening decision, processing operations, refunds, notifications (webhooks and
    emails) and audit entries, oldest first. Email addresses are never included."""
    from apps.events.models import OutboundEmail

    rows = [{"at": payment.created_at, "kind": "payment", "id": payment.public_id,
             "title": "Payment created", "status": "created", "request_id": None}]
    if screening is not None:
        rows.append({"at": screening.occurred_at, "kind": "screening",
                     "id": screening.public_id, "title": f"Screening: {screening.outcome}",
                     "status": screening.outcome, "request_id": None,
                     "detail": (f"score {screening.score:.4f}"
                                if screening.score is not None and not merchant_view
                                else None)})
    for op in ops:
        rows.append({"at": op.created_at, "kind": "operation", "id": op.public_id,
                     "title": f"{op.type.capitalize()} sent to the processor",
                     "status": op.status, "request_id": None})
    for refund in refunds:
        rows.append({"at": refund.created_at, "kind": "refund", "id": refund.public_id,
                     "title": f"Refund {refund.reference}", "status": refund.status,
                     "request_id": None})
    events = {e.public_id: e for e in payment.events.all()}
    for hook in hooks:
        rows.append({"at": events[hook["id"]].created_at, "kind": "webhook",
                     "id": hook["id"], "title": f"Webhook {hook['type']}",
                     "status": hook["status"], "request_id": None,
                     "detail": f"{hook['attempts']} attempt(s)"})
    for email in OutboundEmail.objects.filter(payment=payment).order_by("created_at", "id"):
        rows.append({"at": email.created_at, "kind": "email", "id": email.public_id,
                     "title": f"Email to customer: {email.get_kind_display()}",
                     "status": email.status, "request_id": None,
                     "detail": email_outcome(email)})
    for event in _payment_audit(payment):
        if merchant_view and _hidden_from_merchant(event):
            continue
        rows.append({"at": event.occurred_at, "kind": "audit", "id": str(event.pk),
                     "title": event.action, "status": None,
                     "request_id": event.request_id or None,
                     "actor_type": event.actor_type})
    order = {"payment": 0, "screening": 1, "operation": 2, "refund": 3, "webhook": 4,
             "email": 5, "audit": 6}
    rows.sort(key=lambda r: (r["at"], order[r["kind"]]))
    for row in rows:
        row["at"] = rfc3339(row["at"])
        row["correlation_id"] = payment.public_id
    return rows


NODES = ("acquirer", "switch", "issuer")
_CODE = re.compile(r"^[0-9]{2}$")
_MTI = re.compile(r"^[0-9]{4}$")
_ECI = re.compile(r"^[0-9]{2}$")
_THREE_DS_ID = re.compile(r"^3ds_[0-9A-Za-z]{24}$")


def network_trace(op: Operation) -> dict | None:
    """v2: how the operation crossed the card network, from the simulator's signed reply.

    contracts/NETWORK-CONTRACT.md section 5. Read from the evidence already kept for every
    answered operation, so nothing new is stored. Only the known fields are passed on, and
    anything malformed (or a reply from before v2) is simply no trace.
    """
    try:
        net = json.loads(op.evidence_body).get("network") if op.evidence_body else None
    except (ValueError, AttributeError):
        return None
    if not isinstance(net, dict) or not isinstance(net.get("trace"), list):
        return None
    hops = []
    for hop in net["trace"]:
        if not (isinstance(hop, dict) and hop.get("node") in NODES
                and _MTI.match(str(hop.get("mti_in"))) and _MTI.match(str(hop.get("mti_out")))
                and _CODE.match(str(hop.get("response_code")))
                and isinstance(hop.get("elapsed_ms"), int)):
            return None
        hops.append({k: hop[k] for k in ("node", "mti_in", "mti_out", "response_code",
                                         "elapsed_ms")})
    if not hops or not _CODE.match(str(net.get("response_code"))):
        return None
    three_ds = net.get("three_ds")  # v2: the 0100 carried the bank's 3-D Secure proof
    if not (isinstance(three_ds, dict) and _ECI.match(str(three_ds.get("eci")))
            and _THREE_DS_ID.match(str(three_ds.get("three_ds_id")))):
        three_ds = None
    return {"stan": str(net.get("stan", "")), "rrn": str(net.get("rrn", "")),
            "auth_code": net.get("auth_code") if isinstance(net.get("auth_code"), str) else None,
            "response_code": net["response_code"],
            "response_text": str(net.get("response_text", "")), "trace": hops,
            "three_ds": {"three_ds_id": three_ds["three_ds_id"], "eci": three_ds["eci"]}
            if three_ds else None}


def three_d_secure(payment: Payment) -> dict | None:
    """v2: the card's bank challenged the shopper (docs/v2/THREE-DS.md). Never the code."""
    from apps.checkout.models import VerificationChallenge

    challenge = (VerificationChallenge.objects.filter(payment=payment)
                 .exclude(three_ds_id="").first())
    if challenge is None:
        return None
    return {"bank": "SecurePay Bank", "status": challenge.three_ds_status or "pending",
            "eci": challenge.three_ds_eci or None,
            "wrong_codes": challenge.wrong_attempts}


def operation_to_dict(op: Operation) -> dict:
    return {
        "id": op.public_id,
        "type": op.type,
        "status": op.status,
        "amount": op.amount,
        "simulator_reference": op.simulator_reference or None,
        "reconciliation_checks": op.reconciliation_checks,
        "last_checked_at": rfc3339(op.last_checked_at),
        "next_check_at": rfc3339(op.next_check_at)
        if op.status == Operation.Status.UNRESOLVED else None,
        "escalated_at": rfc3339(op.escalated_at),
        "created_at": rfc3339(op.created_at),
        "completed_at": rfc3339(op.completed_at),
        "network": network_trace(op),
    }


def allowed_actions(staff: StaffAccount, payment: Payment, ops: list[Operation],
                    refundable: int) -> dict:
    open_settlement = any(op.type in settlement.SETTLEMENT_TYPES
                          and op.status in Operation.OPEN_STATUSES for op in ops)
    unresolved = any(op.status == Operation.Status.UNRESOLVED for op in ops)
    authorised = payment.status == PaymentStatus.AUTHORISED and not open_settlement
    return {
        "capture": staff.is_operator and authorised,
        "cancel": staff.is_operator and authorised,
        "refund": (staff.is_operator and payment.status == PaymentStatus.CAPTURED
                   and refundable > 0 and not unresolved),
        "recheck": staff.is_admin and unresolved,
        "review": not staff.is_operator and payment.screening_decision in ("verify", "block"),
    }


def payment_detail(staff: StaffAccount, payment: Payment) -> dict:
    data = payment_to_dict(payment)
    ops = list(payment.operations.order_by("created_at", "id"))
    screening = ScreeningEvent.objects.filter(payment=payment).first()
    refunds = list(Refund.objects.filter(payment=payment).select_related("payment"))
    refund_rows = [refund_to_dict(r) for r in refunds]
    hooks = webhooks(payment)
    data.update({
        "merchant": {"id": payment.merchant.public_id, "name": payment.merchant.name},
        "customer": mask_email(payment.customer_email),
        "description": payment.description or None,
        "screening": screening_to_dict(screening, merchant_view=staff.is_operator),
        "screening_decision": payment.screening_decision or None,
        "verification": payment_row(payment)["risk"]["verification"]
        if payment.screening_decision else None,
        "three_d_secure": three_d_secure(payment),
        "operations": [operation_to_dict(op) for op in ops],
        "refunds": refund_rows,
        "webhooks": hooks,
        "timeline": timeline(payment, merchant_view=staff.is_operator),
        "correlation_id": payment.public_id,
        "trail": trail(payment, screening, ops, refunds, hooks,
                       merchant_view=staff.is_operator),
        "actions": allowed_actions(staff, payment, ops, data["refundable_amount"]),
    })
    return data


def email_outcome(email) -> str:
    """One line an operator can act on: was the email delivered, bounced, accepted, still
    retrying, refused, or never sent? Accepted means the provider (or the demo inbox) took it;
    delivered and bounced come later from the provider's report (apps.events.mail_events).
    Neither proves the customer read it. Never includes the address, subject or body."""
    from apps.events.models import OutboundEmail as E

    where = {E.Route.PROVIDER: "the mail provider", E.Route.CAPTURE: "the demo inbox"}.get(
        email.route, "")
    tries = f"{email.attempt_count} attempt{'s' if email.attempt_count != 1 else ''}"
    error = f", last error {email.last_error}" if email.last_error else ""
    if email.status == E.Status.DELIVERED:
        return f"Delivered to the recipient's mail server ({tries})"
    if email.status == E.Status.BOUNCED:
        kind = f" ({email.bounce_type})" if email.bounce_type else ""
        return f"Bounced: the recipient's mail server refused it{kind}"
    if email.status == E.Status.SENT:
        return f"Accepted by {where or 'the mail server'} ({tries})"
    if email.status == E.Status.PENDING:
        return f"Retrying ({tries}{error})" if email.attempt_count else "Queued"
    if email.status == E.Status.FAILED and email.last_error == "ProviderFailed":
        return "Not delivered: the mail provider could not send it"
    if email.status == E.Status.FAILED:
        return f"Not delivered: gave up ({tries}{error})"
    if email.status == E.Status.DISCARDED:
        return (f"Expired before it could be sent ({tries}{error})" if email.attempt_count
                else "Expired before it could be sent")
    return "Not sent: no permitted route for this recipient"
