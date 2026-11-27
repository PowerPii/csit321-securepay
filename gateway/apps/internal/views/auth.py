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
from apps.passkeys import staff as staff_passkeys


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
                         "setup_required": signin.confirmed_device(account) is None,
                         "passkey": staff_passkeys.available(account)})

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


class PasskeySignInOptionsView(_SignInStep):
    """POST {}: v2, {options} for step 2 with a passkey of the half-signed-in staff member."""

    def post(self, request):
        return Response(staff_passkeys.sign_in_options(request._request))


class PasskeySignInView(_SignInStep):
    """POST {credential}: v2, step 2 with a passkey instead of the authenticator code."""

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else None
        result = staff_passkeys.sign_in(request._request, body)
        return Response(staff_to_dict(result.account))


class OwnPasskeysView(StaffAPIView):
    """GET: v2, the signed-in staff member's passkeys."""

    def get(self, request):
        return Response(staff_passkeys.list_passkeys(self.staff))


class OwnPasskeyOptionsView(StaffAPIView):
    """POST {}: {options} for navigator.credentials.create()."""

    def post(self, request):
        return Response(staff_passkeys.registration_options(self.staff))


class OwnPasskeyAddView(StaffAPIView):
    """POST {credential, name?, code}: add the passkey, confirmed with the authenticator code."""

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else None
        return Response(staff_passkeys.add(request._request, self.staff, body), status=201)


class OwnPasskeyRemoveView(StaffAPIView):
    """POST {}: remove one of my passkeys (204)."""

    def post(self, request, passkey_id):
        staff_passkeys.remove(request._request, self.staff, passkey_id)
        return Response(status=204)


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
