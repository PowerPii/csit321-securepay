"""API key authentication, merchant permission and per-key rate limit for /v1 (section 2)."""
import re
from datetime import timedelta

from django.utils import timezone
from rest_framework import authentication, exceptions, permissions

from apps.common import throttling
from apps.merchants.models import (
    API_KEY_BODY_LENGTH,
    API_KEY_PREFIX,
    LIVE_API_KEY_PREFIX,
    ApiKey,
    digest_api_key,
)

KEY_PATTERN = re.compile(
    rf"^(?:{API_KEY_PREFIX}|{LIVE_API_KEY_PREFIX})[0-9A-Za-z]{{{API_KEY_BODY_LENGTH}}}$")
LAST_USED_RESOLUTION = timedelta(minutes=1)


class ApiKeyAuthentication(authentication.BaseAuthentication):
    """`Authorization: Bearer sp_test_...` or `sp_live_...`. The merchant and the mode always
    come from the key."""

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).decode("latin-1")
        scheme, _, key = header.partition(" ")
        if scheme.lower() != "bearer" or not KEY_PATTERN.match(key.strip()):
            raise exceptions.AuthenticationFailed()
        api_key = (
            ApiKey.objects.active()
            .select_related("merchant")
            .filter(digest=digest_api_key(key.strip()))
            .first()
        )
        if api_key is None:
            raise exceptions.AuthenticationFailed()
        now = timezone.now()
        if api_key.last_used_at is None or now - api_key.last_used_at > LAST_USED_RESOLUTION:
            ApiKey.objects.filter(pk=api_key.pk).update(last_used_at=now)
        return api_key.merchant, api_key

    def authenticate_header(self, request):
        return 'Bearer realm="securepay"'


class IsActiveMerchant(permissions.BasePermission):
    def has_permission(self, request, view):
        if not isinstance(request.auth, ApiKey):
            raise exceptions.NotAuthenticated()
        if not request.user.is_active_merchant:
            raise exceptions.PermissionDenied(
                "This merchant account is not active.", code="merchant_not_active"
            )
        return True


class ApiKeyRateThrottle(throttling.SimpleRateThrottle):
    """Requests a minute per key (NFR-AVA-02; 100 unless an administrator changed it,
    FR-ADM-12); other keys are not affected."""

    scope = "merchant_api_key"

    def get_rate(self):
        from apps.security.limits import current

        return f"{current().api_key_requests_per_minute}/min"

    def get_cache_key(self, request, view):
        if not isinstance(request.auth, ApiKey):
            return None
        return self.cache_format % {"scope": self.scope, "ident": request.auth.pk}


class MerchantIpRateThrottle(throttling.SimpleRateThrottle):
    """Requests a minute per client IP address on the merchant API (FR-ADM-12)."""

    scope = "merchant_api_ip"

    def get_rate(self):
        from apps.security.limits import current

        return f"{current().ip_requests_per_minute}/min"

    def get_cache_key(self, request, view):
        from apps.common.clientip import client_ip

        ip = client_ip(request)
        return self.cache_format % {"scope": self.scope, "ident": ip} if ip else None
