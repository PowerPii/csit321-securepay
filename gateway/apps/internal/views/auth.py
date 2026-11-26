"""D1 sign-in, D2 two-step verification (or a backup code), sign-out, the current staff
member and their backup codes."""
from typing import ClassVar

from django.conf import settings
from django.contrib.auth import get_user, logout
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.authentication import CSRFCheck
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import backup_codes, signin
from apps.accounts.models import StaffAccount
from apps.audit.models import AuditEvent
from apps.audit.models import record as audit
from apps.common.errors import ApiError, invalid
from apps.common.throttling import AnonRateThrottle
from apps.internal.access import StaffAPIView


def _enforce_csrf(request):
    """CSRF on the sign-in steps too, although nobody is signed in yet (login CSRF)."""
    def dummy_get_response(request):  # pragma: no cover - never called
        return None

    check = CSRFCheck(dummy_get_response)
    check.process_request(request)
    reason = check.process_view(request, None, (), {})
    if reason:
        raise ApiError(403, "csrf_failed", "Reload the page and try again.")


def staff_to_dict(account: StaffAccount) -> dict:
    merchant = account.merchant
    return {
        "id": account.public_id,
        "email": account.email,
        "name": account.display_name,
        "role": account.role,
        "role_label": account.get_role_display(),
        "merchant": ({"id": merchant.public_id, "name": merchant.name,
                      "status": merchant.status}
                     if merchant else None),
        # FR-ACC-10: the dashboard warns at idle_warning_minutes, signs out at idle_minutes.
        "idle_minutes": settings.SESSION_COOKIE_AGE // 60,
        "idle_warning_minutes": settings.GATEWAY_RULES["IDLE_WARNING_MINUTES"],
        # The dashboard asks for new backup codes when few (or none) are left.
        "backup_codes_left": backup_codes.remaining(account.user),
        # Demo core-only mode: the dashboard hides the pages outside the core story.
        "demo_core_only": settings.DEMO_CORE_ONLY,
    }


class _SignInStep(APIView):
    authentication_classes: ClassVar = []
    permission_classes: ClassVar = [AllowAny]
    throttle_classes: ClassVar = [AnonRateThrottle]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        _enforce_csrf(request._request)


class LoginView(_SignInStep):
    """POST {email, password}: step 1. 200 {next: "totp", setup: null | {qr...}}."""

    def post(self, request):
        email, password = request.data.get("email"), request.data.get("password")
        if not isinstance(email, str) or not email.strip():
            raise invalid("missing_field", "Enter your email address.", param="email")
        if not isinstance(password, str) or not password:
            raise invalid("missing_field", "Enter your password.", param="password")
        return Response(signin.check_password(request._request, email[:254], password[:256]))


class TotpView(_SignInStep):
    """GET: who is half signed in (for the D2 page). POST {code}: step 2, signs in.

    After first-time setup the reply also carries `backup_codes`, shown this once."""

    def get(self, request):
        account = signin.pending_account(request._request)
        return Response({"email": account.email,
                         "setup_required": signin.confirmed_device(account) is None})

    def post(self, request):
        code = request.data.get("code")
        if not isinstance(code, str):
            raise invalid("missing_field", "Enter the 6-digit code.", param="code")
        result = signin.check_code(request._request, code)
        data = staff_to_dict(result.account)
        if result.backup_codes:
            data["backup_codes"] = result.backup_codes
        return Response(data)


class BackupCodeSignInView(_SignInStep):
    """POST {code}: step 2 with a one-time backup code instead of the authenticator app."""

    def post(self, request):
        code = request.data.get("code")
        if not isinstance(code, str) or not code.strip():
            raise invalid("missing_field", "Enter one of your backup codes.", param="code")
        result = signin.check_backup_code(request._request, code[:64])
        return Response(staff_to_dict(result.account))


class OwnBackupCodesView(StaffAPIView):
    """GET: how many backup codes are left. POST {code}: a new set, confirmed with the current
    authenticator code; the reply carries the codes once and the old ones stop working."""

    def get(self, request):
        return Response(backup_codes.status(self.staff.user))

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        codes = signin.regenerate_backup_codes(request._request, self.staff, body.get("code"))
        return Response({**backup_codes.status(self.staff.user), "codes": codes})


class LogoutView(APIView):
    authentication_classes: ClassVar = []
    permission_classes: ClassVar = [AllowAny]

    def post(self, request):
        _enforce_csrf(request._request)
        user = get_user(request._request)  # DRF's request.user is anonymous here
        account = (StaffAccount.objects.filter(user=user).first()
                   if user.is_authenticated else None)
        logout(request._request)
        if account is not None:
            audit("staff.signed_out", actor_type=AuditEvent.ActorType.STAFF,
                  actor_id=account.public_id, target_type="staff",
                  target_id=account.public_id, request_id=getattr(request, "request_id", ""))
        return Response(status=204)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class MeView(StaffAPIView):
    """The signed-in staff member. Also sets the CSRF cookie, so the dashboard calls it first
    (a 401 still sets the cookie)."""

    def get(self, request):
        return Response(staff_to_dict(self.staff))
