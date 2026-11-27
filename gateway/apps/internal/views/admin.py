"""Administrator pages: merchant approval (FR-ADM-01, FR-MER-03, FR-ADM-13) and the
read-only audit log (FR-ADM-10, FR-ADM-11).

A merchant is either created here by an administrator, or registers itself (FR-MER-01). A
self-registered merchant can be approved only once its contact email is verified; approving
it notifies its operator, who then generates the API key in the dashboard (D8), so the key is
never shown to the administrator. Approving a merchant an administrator created issues the
API key and a fresh signing secret and returns both once, as before. Rejecting gives a reason
and tells a self-registered merchant (FR-ADM-01). Only the key's digest and the encrypted
secret are kept.
"""
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from rest_framework import status
from rest_framework.response import Response

from apps.accounts import mail
from apps.accounts.models import StaffAccount
from apps.audit.models import AuditEvent
from apps.audit.models import record as audit
from apps.common.errors import ApiError, invalid, not_found
from apps.common.timefmt import rfc3339
from apps.common.views import body_or_empty
from apps.internal.access import ADMIN, StaffAPIView
from apps.internal.query_params import bounded_int, local_zone, parse_date
from apps.merchants.models import ApiKey, Merchant


def merchant_to_dict(merchant: Merchant) -> dict:
    active = {k.livemode: k for k in merchant.api_keys.all() if k.revoked_at is None}
    key, live_key = active.get(False), active.get(True)
    return {
        "id": merchant.public_id,
        "name": merchant.name,
        "status": merchant.status,
        "status_reason": merchant.status_reason or None,
        "webhook_url": merchant.webhook_url or None,
        "webhook_url_approved": merchant.webhook_url_approved,
        "max_amount_cents": merchant.max_amount_cents,
        "api_key": ({"id": key.public_id, "hint": key.hint + "…",
                     "last_used_at": rfc3339(key.last_used_at)} if key else None),
        "live_api_key": ({"id": live_key.public_id, "hint": live_key.hint + "…",
                          "last_used_at": rfc3339(live_key.last_used_at)}
                         if live_key else None),
        "created_at": rfc3339(merchant.created_at),
        "self_registered": merchant.self_registered,
        "registration": ({
            "registration_number": merchant.registration_number,
            "website": merchant.website,
            "contact_name": merchant.contact_name,
            "contact_email": merchant.contact_email,
            "email_verified_at": rfc3339(merchant.email_verified_at),
        } if merchant.self_registered else None),
    }


def _tell_operators(merchant: Merchant, subject: str, body: str) -> None:
    addresses = set(StaffAccount.objects.filter(merchant=merchant)
                    .values_list("user__email", flat=True))
    if merchant.contact_email:
        addresses.add(merchant.contact_email)
    for address in sorted(addresses):
        mail.notice(address, subject, body)


def _webhook_url(value) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or len(value) > 2048:
        raise invalid("invalid_field", "The webhook URL is too long.", param="webhook_url")
    try:
        URLValidator(schemes=["https"])(value)
    except ValidationError:
        raise invalid("invalid_field", "The webhook URL must be an https:// address.",
                      param="webhook_url") from None
    return value


def _audit(view, action, merchant, **details):
    audit(action, actor_type=AuditEvent.ActorType.STAFF, actor_id=view.staff.public_id,
          merchant_id=merchant.public_id, target_type="merchant",
          target_id=merchant.public_id, request_id=view.request.request_id, details=details)


class MerchantListView(StaffAPIView):
    roles = ADMIN

    def get(self, request):
        merchants = Merchant.objects.prefetch_related("api_keys").order_by("-created_at")
        return Response({"object": "list", "data": [merchant_to_dict(m) for m in merchants]})

    def post(self, request):
        body = body_or_empty(request)
        name = body.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise invalid("invalid_field", "Enter a business name of up to 120 characters.",
                          param="name")
        limit = body.get("max_amount_cents")
        if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool)
                                  or not 50 <= limit <= 1_000_000):
            raise invalid("invalid_field", "The limit must be 50 to 1000000 cents.",
                          param="max_amount_cents")
        merchant = Merchant(name=name.strip(), webhook_url=_webhook_url(body.get("webhook_url")),
                            max_amount_cents=limit)
        merchant.save()
        _audit(self, "merchant.created", merchant)
        return Response(merchant_to_dict(merchant), status=status.HTTP_201_CREATED)


def _get(merchant_id: str, lock=False) -> Merchant:
    merchants = Merchant.objects.select_for_update() if lock else Merchant.objects
    merchant = merchants.filter(public_id=merchant_id).first()
    if merchant is None:
        raise not_found()
    return merchant


class MerchantApproveView(StaffAPIView):
    """Pending -> active. Returns the API key and signing secret once."""

    roles = ADMIN

    def post(self, request, merchant_id: str):
        with transaction.atomic():
            merchant = _get(merchant_id, lock=True)
            if merchant.status != Merchant.Status.PENDING:
                raise ApiError(409, "merchant_not_pending",
                               f"Only a pending merchant can be approved; this one is "
                               f"{merchant.status}.")
            if merchant.self_registered and merchant.email_verified_at is None:
                raise ApiError(409, "email_not_verified", "The merchant has not verified its "
                               "contact email yet, so it cannot be approved.")
            merchant.status = Merchant.Status.ACTIVE
            credentials = None
            if merchant.self_registered:
                merchant.save(update_fields=["status", "updated_at"])
                _audit(self, "merchant.approved", merchant)
                _tell_operators(
                    merchant, f"{merchant.name} is approved",
                    f"Hello,\n\nAn administrator has approved {merchant.name}. Sign in at "
                    f"{mail.dashboard_link('login')} and open API keys to generate your API key "
                    "and webhook signing secret.")
            else:
                secret = merchant.rotate_signing_secret()
                merchant.save()
                api_key, plaintext = ApiKey.issue(merchant)
                _audit(self, "merchant.approved", merchant)
                _audit(self, "api_key.issued", merchant, api_key_id=api_key.public_id)
                credentials = {"api_key": plaintext, "signing_secret": secret}
        data = merchant_to_dict(Merchant.objects.prefetch_related("api_keys").get(pk=merchant.pk))
        if credentials:
            data["credentials"] = credentials
        return Response(data)


class MerchantRejectView(StaffAPIView):
    roles = ADMIN

    def post(self, request, merchant_id: str):
        reason = (request.data or {}).get("reason") if isinstance(request.data, dict) else None
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 200:
            raise invalid("missing_field", "Give a reason of up to 200 characters.",
                          param="reason")
        with transaction.atomic():
            merchant = _get(merchant_id, lock=True)
            if merchant.status != Merchant.Status.PENDING:
                raise ApiError(409, "merchant_not_pending",
                               f"Only a pending merchant can be rejected; this one is "
                               f"{merchant.status}.")
            merchant.status = Merchant.Status.REJECTED
            merchant.save(update_fields=["status", "updated_at"])
            _audit(self, "merchant.rejected", merchant, reason=reason.strip())
            if merchant.self_registered:
                _tell_operators(
                    merchant, f"registration of {merchant.name} not approved",
                    f"Hello,\n\nAn administrator did not approve the registration of "
                    f"{merchant.name}.\n\nReason: {reason.strip()}\n\nYou can register "
                    "again with corrected details.")
                # The operator account of a rejected merchant cannot sign in.
                get_user_model().objects.filter(
                    staff_account__merchant=merchant).update(is_active=False)
        return Response(merchant_to_dict(merchant))


class MerchantWebhookView(StaffAPIView):
    """POST {webhook_url?, approved}: set or approve the webhook address (FR-ADM-13). Changing
    the address withdraws the approval until an administrator approves the new one."""

    roles = ADMIN

    def post(self, request, merchant_id: str):
        body = body_or_empty(request)
        approved = body.get("approved")
        if not isinstance(approved, bool):
            raise invalid("invalid_field", "'approved' must be true or false.",
                          param="approved")
        with transaction.atomic():
            merchant = _get(merchant_id, lock=True)
            if "webhook_url" in body:
                url = _webhook_url(body.get("webhook_url"))
                if url != merchant.webhook_url:
                    merchant.webhook_url = url
            if approved and not merchant.webhook_url:
                raise invalid("missing_field", "There is no webhook URL to approve.",
                              param="webhook_url")
            merchant.webhook_url_approved = approved
            merchant.save(update_fields=["webhook_url", "webhook_url_approved", "updated_at"])
            _audit(self, "merchant.webhook_approved" if approved else
                   "merchant.webhook_unapproved", merchant, webhook_url=merchant.webhook_url)
        return Response(merchant_to_dict(
            Merchant.objects.prefetch_related("api_keys").get(pk=merchant.pk)))


class AuditListView(StaffAPIView):
    """GET ?action=&actor=&target=&merchant=&from=&to=&page=. Read-only: there is no route
    that edits or deletes an entry, and the table refuses it anyway (migration 0002)."""

    roles = ADMIN

    def get(self, request):
        params = request.query_params
        events = AuditEvent.objects.all()
        if params.get("action"):
            events = events.filter(action__startswith=params["action"][:64])
        if params.get("actor"):
            events = events.filter(actor_id=params["actor"][:64])
        if params.get("actor_type"):
            events = events.filter(actor_type=params["actor_type"][:16])
        if params.get("target"):
            events = events.filter(target_id=params["target"][:32])
        if params.get("merchant"):
            events = events.filter(merchant_id=params["merchant"][:32])
        zone = local_zone()
        if params.get("from"):
            events = events.filter(occurred_at__gte=datetime.combine(
                parse_date(params["from"], "from"), time.min, zone))
        if params.get("to"):
            events = events.filter(occurred_at__lt=datetime.combine(
                parse_date(params["to"], "to") + timedelta(days=1), time.min, zone))
        page = bounded_int(params.get("page"), "page", 1, 1, 100_000)
        size = bounded_int(params.get("page_size"), "page_size", 50, 1, 200)
        count = events.count()
        rows = events.order_by("-occurred_at", "-id")[(page - 1) * size: page * size]
        return Response({"object": "list", "count": count, "page": page, "page_size": size,
                         "data": [{
                             "id": e.pk, "at": rfc3339(e.occurred_at), "action": e.action,
                             "actor_type": e.actor_type, "actor_id": e.actor_id or None,
                             "merchant_id": e.merchant_id or None,
                             "target_type": e.target_type or None,
                             "target_id": e.target_id or None,
                             "request_id": e.request_id or None, "details": e.details,
                         } for e in rows]})
