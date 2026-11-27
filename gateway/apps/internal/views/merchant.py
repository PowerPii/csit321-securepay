"""A merchant operator's own merchant (wireframe D8): approval status, API key and webhook
address.

* GET /internal/merchant: status, key hint, webhook address and whether it is approved.
* POST /internal/merchant/api-key: once the merchant is approved and has no active key, issue
  one with a fresh signing secret, shown once (FR-MER-03).
* POST /internal/merchant/api-key/rotate {code}: after the TOTP code is confirmed, revoke the
  active key and issue a new key and signing secret, shown once (FR-MER-04, UC-17). The old
  key is refused from that moment.
* POST /internal/merchant/api-key/revoke {code, confirm_last_key}: after the TOTP code is
  confirmed, revoke the active key. Revoking the only active key stops payments until a new
  key is generated, so it needs `confirm_last_key: true` (FR-MER-05); without it the reply is
  409 last_active_key and nothing changes.
* POST /internal/merchant/webhook {webhook_url}: submit the address for administrator approval
  (FR-MER-10). A new address is never used until an administrator approves it (FR-ADM-13).
"""
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from apps.accounts import mail, signin
from apps.audit.models import AuditEvent
from apps.audit.models import record as audit
from apps.common.errors import ApiError, invalid
from apps.common.timefmt import rfc3339
from apps.common.views import body_or_empty
from apps.internal.access import OPERATOR, StaffAPIView
from apps.internal.views.admin import _webhook_url
from apps.merchants.models import ApiKey, Merchant, parse_mode


def _key_to_dict(key: ApiKey | None) -> dict | None:
    if key is None:
        return None
    return {"id": key.public_id, "mode": key.mode, "hint": key.hint + "…",
            "created_at": rfc3339(key.created_at), "last_used_at": rfc3339(key.last_used_at)}


def own_merchant_to_dict(merchant: Merchant) -> dict:
    active = {k.livemode: k for k in merchant.api_keys.filter(revoked_at__isnull=True)}
    test_key, live_key = active.get(False), active.get(True)
    return {
        "id": merchant.public_id,
        "name": merchant.name,
        "status": merchant.status,
        "registration_number": merchant.registration_number or None,
        "webhook_url": merchant.webhook_url or None,
        "webhook_url_approved": merchant.webhook_url_approved,
        # v2: one active key per mode. `api_key` stays the test key, as in v1.
        "api_key": _key_to_dict(test_key),
        "live_api_key": _key_to_dict(live_key),
        "can_generate_key": merchant.is_active_merchant and test_key is None,
        "can_generate_live_key": merchant.is_active_merchant and live_key is None,
        "revoked_keys": [{"id": k.public_id, "mode": k.mode, "hint": k.hint + "…",
                          "created_at": rfc3339(k.created_at),
                          "revoked_at": rfc3339(k.revoked_at)}
                         for k in merchant.api_keys.filter(revoked_at__isnull=False)
                         .order_by("-revoked_at")[:5]],
    }


class _OwnMerchantView(StaffAPIView):
    roles = OPERATOR

    def _audit(self, action, merchant, target_type="merchant", target_id=None, **details):
        audit(action, actor_type=AuditEvent.ActorType.STAFF, actor_id=self.staff.public_id,
              merchant_id=merchant.public_id, target_type=target_type,
              target_id=target_id or merchant.public_id, request_id=self.request.request_id,
              details=details)

    def _locked_merchant(self) -> Merchant:
        return Merchant.objects.select_for_update().get(pk=self.staff.merchant_id)


class OwnMerchantView(_OwnMerchantView):
    def get(self, request):
        return Response(own_merchant_to_dict(self.staff.merchant))


class OwnApiKeyView(_OwnMerchantView):
    """Generate the first key of a mode (`{"mode": "test"|"live"}`, default test).

    The signing secret is shared by both modes, so it is only replaced (and shown) when the
    merchant has no other active key; otherwise `signing_secret` is null and the one the
    merchant already has keeps working.
    """

    def post(self, request):
        livemode = parse_mode(body_or_empty(request).get("mode"))
        with transaction.atomic():
            merchant = self._locked_merchant()
            if not merchant.is_active_merchant:
                raise ApiError(409, "merchant_not_approved", "An API key can be generated once "
                               "an administrator has approved the merchant.")
            active = merchant.api_keys.filter(revoked_at__isnull=True)
            if active.filter(livemode=livemode).exists():
                raise ApiError(409, "api_key_exists", f"This merchant already has an active "
                               f"{'live' if livemode else 'test'} API key. Rotate it instead.")
            secret = None
            if not active.exists():
                secret = merchant.rotate_signing_secret()
                merchant.save(update_fields=["signing_secret_encrypted", "updated_at"])
            api_key, plaintext = ApiKey.issue(merchant, livemode=livemode)
            self._audit("api_key.issued", merchant, target_type="api_key",
                        target_id=api_key.public_id, mode=api_key.mode)
        data = own_merchant_to_dict(merchant)
        data["credentials"] = {"api_key": plaintext, "signing_secret": secret}
        return Response(data, status=status.HTTP_201_CREATED)


class _KeyChangeView(_OwnMerchantView):
    """Rotate or revoke the active key of one mode (`"mode"` in the body, default test)."""

    def _active_key(self, merchant: Merchant) -> ApiKey:
        livemode = parse_mode(self._body().get("mode"))
        key = merchant.api_keys.filter(revoked_at__isnull=True, livemode=livemode).first()
        if key is None:
            raise ApiError(409, "no_active_api_key", f"This merchant has no active "
                           f"{'live' if livemode else 'test'} API key.")
        return key

    def _body(self) -> dict:
        return body_or_empty(self.request)


class OwnApiKeyRotateView(_KeyChangeView):
    """New key and signing secret, shown once; the old key stops working at once."""

    def post(self, request):
        body = self._body()
        merchant = self.staff.merchant
        if not merchant.is_active_merchant:
            raise ApiError(409, "merchant_not_active", "Only an active merchant can rotate its "
                           "API key.")
        self._active_key(merchant)  # nothing to rotate: say so before asking for the code
        signin.confirm_with_totp(request._request, self.staff, body.get("code"), "rotate_key")
        with transaction.atomic():
            merchant = self._locked_merchant()
            old = self._active_key(merchant)
            old.revoked_at = timezone.now()
            old.save(update_fields=["revoked_at"])
            secret = merchant.rotate_signing_secret()
            merchant.save(update_fields=["signing_secret_encrypted", "updated_at"])
            new, plaintext = ApiKey.issue(merchant, livemode=old.livemode)
            self._audit("api_key.rotated", merchant, target_type="api_key",
                        target_id=new.public_id, old_api_key_id=old.public_id, mfa=True,
                        mode=new.mode)
        data = own_merchant_to_dict(merchant)
        data["credentials"] = {"api_key": plaintext, "signing_secret": secret}
        return Response(data)


class OwnApiKeyRevokeView(_KeyChangeView):
    def post(self, request):
        body = self._body()
        merchant = self.staff.merchant
        key = self._active_key(merchant)
        others = (merchant.api_keys.filter(revoked_at__isnull=True, livemode=key.livemode)
                  .exclude(pk=key.pk).exists())
        if not others and body.get("confirm_last_key") is not True:
            # FR-MER-05: checked before the code, so the warning comes first.
            raise ApiError(409, "last_active_key", "This is your only active API key. Once it "
                           "is revoked your server cannot create payments or check them until "
                           "you generate a new key.", extra={"active_keys": 1})
        signin.confirm_with_totp(request._request, self.staff, body.get("code"), "revoke_key")
        with transaction.atomic():
            merchant = self._locked_merchant()
            key = self._active_key(merchant)
            key.revoked_at = timezone.now()
            key.save(update_fields=["revoked_at"])
            self._audit("api_key.revoked", merchant, target_type="api_key",
                        target_id=key.public_id, mfa=True, last_key=not others, mode=key.mode)
        return Response(own_merchant_to_dict(merchant))


class OwnWebhookView(_OwnMerchantView):
    def post(self, request):
        body = body_or_empty(request)
        url = _webhook_url(body.get("webhook_url"))
        if not url:
            raise invalid("missing_field", "Enter the webhook URL.", param="webhook_url")
        with transaction.atomic():
            merchant = self._locked_merchant()
            if merchant.status in (Merchant.Status.REJECTED, Merchant.Status.SUSPENDED,
                                   Merchant.Status.DEACTIVATED):
                raise ApiError(409, "merchant_not_active", "This merchant cannot change its "
                               "webhook address.")
            if url == merchant.webhook_url:
                return Response(own_merchant_to_dict(merchant))
            merchant.webhook_url = url
            merchant.webhook_url_approved = False  # until an administrator approves it
            merchant.save(update_fields=["webhook_url", "webhook_url_approved", "updated_at"])
            self._audit("merchant.webhook_submitted", merchant, webhook_url=url)
            mail.notify_administrators(
                f"webhook address to approve for {merchant.name}",
                f"Hello,\n\n{merchant.name} submitted a webhook address for approval:\n\n{url}"
                f"\n\nNo webhooks go to it until it is approved. Review it at "
                f"{mail.dashboard_link('merchants')}.")
        return Response(own_merchant_to_dict(merchant))
