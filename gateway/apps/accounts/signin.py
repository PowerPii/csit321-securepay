"""Staff two-step sign-in: password, then TOTP (FR-ACC-14), with lockout (FR-ACC-15).

1. `check_password()`: email + password. On success the session remembers the account for
   STAFF_TOTP_STEP_MINUTES; nobody is signed in yet.
2. `check_code()`: the 6-digit code from the authenticator app. At the first sign-in the
   account has no confirmed device: step 1 creates one and the dashboard shows its QR code
   (D2 first-time setup); the first correct code confirms it and makes the one-time backup
   codes (apps.accounts.backup_codes), which are shown once.
3. `check_backup_code()`: instead of step 2, one unused backup code, for a staff member who
   has lost the authenticator app. Each code works once; the holder is emailed when one is
   used.

Wrong passwords, wrong codes and wrong backup codes count together. The fifth failure locks the account for
STAFF_LOCKOUT_MINUTES and emails the account holder. While locked, nothing is checked, so a
locked account cannot be used to guess. Every success, failure and lockout is audited.
"""
import base64
import logging
import time
from dataclasses import dataclass

import segno
from django.conf import settings
from django.contrib.auth import authenticate, login
from django.db import transaction
from django.utils import timezone
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts import backup_codes
from apps.accounts.models import StaffAccount
from apps.audit.models import AuditEvent
from apps.audit.models import record as audit
from apps.common.errors import ApiError

logger = logging.getLogger(__name__)

PENDING_KEY = "staff_signin"  # session key between the two steps
DEVICE_NAME = "Authenticator app"


def _rules():
    return settings.GATEWAY_RULES


def _audit(action: str, account: StaffAccount | None, request, **details):
    audit(action, actor_type=AuditEvent.ActorType.STAFF,
          actor_id=account.public_id if account else "",
          merchant_id=account.merchant.public_id if account and account.merchant else "",
          target_type="staff" if account else "", target_id=account.public_id if account else "",
          request_id=getattr(request, "request_id", ""), details=details)


def invalid_credentials() -> ApiError:
    # Same reply whether the email is unknown or the password is wrong (FR-ACC-02 style).
    return ApiError(401, "invalid_credentials", "Email or password is incorrect. After "
                    f"{_rules()['STAFF_LOCKOUT_FAILURES']} failed attempts the account is "
                    f"locked for {_rules()['STAFF_LOCKOUT_MINUTES']} minutes.")


def locked_error(account: StaffAccount) -> ApiError:
    seconds = account.lock_seconds_left()
    minutes = max(1, -(-seconds // 60))
    return ApiError(403, "account_locked",
                    "This account is locked after too many failed sign-ins. Try again in "
                    f"{minutes} minute{'s' if minutes != 1 else ''}.", retry_after=seconds)


def _find(email: str) -> StaffAccount | None:
    return (StaffAccount.objects.select_related("user", "merchant")
            .filter(user__email__iexact=email.strip(), user__is_active=True,
                    status=StaffAccount.Status.ACTIVE).first())


def _record_failure(account: StaffAccount, request, stage: str) -> bool:
    """Count one failure; lock at the limit. Returns True if this failure locked the account."""
    now = timezone.now()
    with transaction.atomic():
        account = StaffAccount.objects.select_for_update().get(pk=account.pk)
        if account.is_locked(now):
            return True
        account.failed_attempts += 1
        locked = account.failed_attempts >= _rules()["STAFF_LOCKOUT_FAILURES"]
        if locked:
            account.locked_until = now + StaffAccount.lock_duration()
            account.failed_attempts = 0
        account.save(update_fields=["failed_attempts", "locked_until"])
    _audit("staff.sign_in_failed", account, request, stage=stage)
    if locked:
        _audit("staff.locked", account, request,
               minutes=_rules()["STAFF_LOCKOUT_MINUTES"])
        _alert_locked(account)
        from apps.security.alerts import raise_alert
        from apps.security.models import AlertKind, Severity

        raise_alert(AlertKind.STAFF_LOCKED, severity=Severity.MEDIUM, source=account.public_id,
                    merchant_id=account.merchant.public_id if account.merchant else "",
                    summary=(f"Dashboard account {account.public_id} ({account.get_role_display()}) "
                             f"was locked after {_rules()['STAFF_LOCKOUT_FAILURES']} failed "
                             f"attempts (last at the {stage} step)."),
                    details={"stage": stage, "role": account.role})
    return locked


def _alert_locked(account: StaffAccount) -> None:
    """FR-ACC-15: tell the account holder. A mail failure never unlocks or breaks sign-in."""
    _security_notice(
        account,
        "SecurePay: your dashboard account was locked",
        f"Hello,\n\nYour SecurePay dashboard account ({account.email}) was locked for "
        f"{_rules()['STAFF_LOCKOUT_MINUTES']} minutes after "
        f"{_rules()['STAFF_LOCKOUT_FAILURES']} failed sign-in attempts.\n\n"
        "If this was not you, tell an administrator straight away.\n\nSecurePay")


def confirmed_device(account: StaffAccount) -> TOTPDevice | None:
    return TOTPDevice.objects.filter(user=account.user, confirmed=True).first()


def verify_totp(device: TOTPDevice | None, code) -> bool:
    """Check a 6-digit authenticator code and use it up: one success per device and time step.

    django-otp compares the code with the last accepted time step held on the object it is
    given, then saves. Two gunicorn workers holding the same device could each accept the same
    code, so the device row is locked and read again before the check (Codex release review,
    18 Nov 2026). Every flow that accepts a TOTP code goes through here.
    """
    if device is None or not isinstance(code, str):
        return False
    code = code.strip().replace(" ", "")
    if not (len(code) == 6 and code.isdigit()):
        return False
    with transaction.atomic():
        locked = TOTPDevice.objects.select_for_update().filter(pk=device.pk).first()
        if locked is None:
            return False
        ok = locked.verify_token(code)
    device.refresh_from_db()  # the caller may save the device (first-time confirmation)
    return ok


def _setup_device(account: StaffAccount) -> TOTPDevice:
    """The unconfirmed device for first-time setup; a new secret each time it is shown."""
    TOTPDevice.objects.filter(user=account.user, confirmed=False).delete()
    return TOTPDevice.objects.create(user=account.user, name=DEVICE_NAME, confirmed=False)


@dataclass
class Setup:
    secret: str
    uri: str
    qr_svg: str  # data: URI

    def as_dict(self):
        return {"secret": self.secret, "otpauth_uri": self.uri, "qr_svg": self.qr_svg}


def _setup_details(device: TOTPDevice) -> Setup:
    secret = base64.b32encode(device.bin_key).decode().rstrip("=")
    uri = device.config_url
    qr = segno.make(uri, error="m")
    return Setup(secret=secret, uri=uri, qr_svg=qr.svg_data_uri(scale=5, border=2))


def check_password(request, email: str, password: str) -> dict:
    """Step 1. Returns what step 2 needs: whether this is the first-time setup."""
    request.session.pop(PENDING_KEY, None)
    account = _find(email)
    if account is None:
        # Unknown email: the same reply, and a password hash anyway so timing does not tell.
        authenticate(request, username="unknown-" + email[:200], password=password)
        _audit("staff.sign_in_failed", None, request, stage="password")
        raise invalid_credentials()
    if account.is_locked():
        # The same reply as a wrong password, so the password step never tells whether an
        # email is a staff account (security self-test SEC-07, Fifa 18 Nov 2026). The holder
        # was emailed when it locked; the message itself mentions the lockout.
        raise invalid_credentials()
    user = authenticate(request, username=account.user.get_username(), password=password)
    if user is None:
        _record_failure(account, request, "password")
        raise invalid_credentials()

    if account.merchant is not None and account.merchant.status in ("suspended", "deactivated"):
        _audit("staff.sign_in_refused", account, request, merchant_status=account.merchant.status)
        raise ApiError(403, "merchant_suspended", f"{account.merchant.name} is "
                       f"{account.merchant.status}, so its dashboard is closed. Contact SecurePay "
                       "support.")
    request.session.cycle_key()  # no session fixation across the sign-in
    request.session[PENDING_KEY] = {"account": account.pk, "at": time.time()}
    device = confirmed_device(account)
    setup = None if device else _setup_details(_setup_device(account))
    from apps.passkeys.staff import available as passkey_available

    return {"next": "totp", "email": account.email,
            "setup": setup.as_dict() if setup else None,
            "passkey": passkey_available(account)}  # v2: step 2 may use a passkey


def pending_account(request) -> StaffAccount:
    pending = request.session.get(PENDING_KEY)
    step = _rules()["STAFF_TOTP_STEP_MINUTES"] * 60
    if not pending or time.time() - pending.get("at", 0) > step:
        request.session.pop(PENDING_KEY, None)
        raise ApiError(401, "sign_in_required",
                       "Your sign-in timed out. Enter your email and password again.")
    account = (StaffAccount.objects.select_related("user", "merchant")
               .filter(pk=pending["account"], user__is_active=True).first())
    if account is None:
        raise ApiError(401, "sign_in_required", "Enter your email and password again.")
    return account


def check_code(request, code: str) -> "SignedIn":
    """Step 2. Signs the staff member in, or counts a failure."""
    account = pending_account(request)
    if account.is_locked():
        request.session.pop(PENDING_KEY, None)
        raise locked_error(account)
    device = confirmed_device(account)
    first_setup = device is None
    if first_setup:
        device = TOTPDevice.objects.filter(user=account.user, confirmed=False).first()
        if device is None:
            raise ApiError(401, "sign_in_required", "Enter your email and password again.")
    if not verify_totp(device, code or ""):
        if _record_failure(account, request, "totp"):
            request.session.pop(PENDING_KEY, None)
            raise locked_error(StaffAccount.objects.get(pk=account.pk))
        raise ApiError(401, "code_incorrect",
                       "That code is not right. Enter the current 6-digit code from your "
                       "authenticator app.", param="code")

    codes = None
    if first_setup:
        device.confirmed = True
        device.save(update_fields=["confirmed"])
        codes = backup_codes.generate(account.user)
        _audit("staff.backup_codes_generated", account, request, count=len(codes),
               reason="setup")
    _complete_sign_in(request, account, device, mfa_setup=first_setup)
    return SignedIn(account, backup_codes=codes)


@dataclass
class SignedIn:
    account: StaffAccount
    backup_codes: list[str] | None = None  # shown once, after first-time setup
    backup_codes_left: int | None = None  # after signing in with a backup code


def _complete_sign_in(request, account: StaffAccount, device: TOTPDevice, **details) -> None:
    request.session.pop(PENDING_KEY, None)
    user = account.user
    user.backend = "django.contrib.auth.backends.ModelBackend"
    login(request, user)  # new session key
    # The session is verified against the authenticator device, also after a backup code:
    # the code stands in for the app, it does not replace it.
    otp_login(request, device)
    StaffAccount.objects.filter(pk=account.pk).update(
        failed_attempts=0, locked_until=None, last_sign_in_at=timezone.now())
    _audit("staff.signed_in", account, request, **details)


def check_backup_code(request, code: str) -> SignedIn:
    """Step 2 with a one-time backup code instead of the authenticator app."""
    account = pending_account(request)
    if account.is_locked():
        request.session.pop(PENDING_KEY, None)
        raise locked_error(account)
    device = confirmed_device(account)
    if device is None:
        # First-time setup: there are no backup codes yet, the app must be set up.
        raise ApiError(409, "setup_required", "Set up your authenticator app first. Backup "
                       "codes are made when it is set up.")
    if not backup_codes.use(account.user, code if isinstance(code, str) else ""):
        if _record_failure(account, request, "backup_code"):
            request.session.pop(PENDING_KEY, None)
            raise locked_error(StaffAccount.objects.get(pk=account.pk))
        raise ApiError(401, "code_incorrect", "That backup code is not right or has already "
                       "been used. Each backup code works once.", param="code")
    left = backup_codes.remaining(account.user)
    _complete_sign_in(request, account, device, mfa_setup=False, method="backup_code",
                      backup_codes_left=left)
    _alert_backup_code_used(account, left)
    return SignedIn(account, backup_codes_left=left)


def _alert_backup_code_used(account: StaffAccount, left: int) -> None:
    """Tell the holder, so a stolen code is noticed. A mail failure never breaks sign-in."""
    _security_notice(
        account,
        "SecurePay: a backup code was used to sign in",
        f"Hello,\n\nA one-time backup code was just used to sign in to your SecurePay "
        f"dashboard account ({account.email}). You have {left} backup "
        f"code{'s' if left != 1 else ''} left.\n\nIf you have lost your phone, set up "
        "your authenticator app again with an administrator and make new backup codes "
        "under Two-step verification in the dashboard.\n\nIf this was not you, tell an "
        "administrator straight away.\n\nSecurePay")


def _security_notice(account: StaffAccount, subject: str, body: str) -> None:
    """Queued for the worker. A failure to queue is logged and never breaks sign-in (the
    savepoint keeps a failed insert from spoiling any surrounding transaction)."""
    from apps.events.emails import queue
    from apps.events.models import OutboundEmail

    try:
        with transaction.atomic():
            queue(OutboundEmail.Kind.SECURITY_NOTICE, None, account.email, subject, body)
    except Exception:
        logger.exception("Could not queue the security notice for %s", account.public_id)


def regenerate_backup_codes(request, account: StaffAccount, code) -> list[str]:
    """New backup codes for a signed-in staff member, confirmed with the current TOTP code.
    The earlier codes stop working."""
    confirm_with_totp(request, account, code, "backup_codes")
    codes = backup_codes.generate(account.user)
    _audit("staff.backup_codes_generated", account, request, count=len(codes),
           reason="regenerated", mfa=True)
    return codes


def confirm_with_totp(request, account: StaffAccount, code, action: str) -> None:
    """Step-up MFA for a sensitive action by a signed-in staff member (FR-MER-04, UC-17).

    The current code from the authenticator app must be entered again. A wrong code cancels
    the action and counts towards the same lockout as sign-in; the failure that locks the
    account also signs it out.
    """
    if not isinstance(code, str):
        raise ApiError(400, "missing_field", "Enter the 6-digit code from your authenticator "
                       "app to confirm.", param="code")
    if verify_totp(confirmed_device(account), code):
        return
    if _record_failure(account, request, f"confirm:{action}"):
        from django.contrib.auth import logout

        logout(request)
        raise locked_error(StaffAccount.objects.get(pk=account.pk))
    raise ApiError(403, "code_incorrect", "That code is not right, so nothing was changed. "
                   "Enter the current 6-digit code from your authenticator app.", param="code")
