"""Staff passkeys on the dashboard (docs/v2/PASSKEYS.md, "Staff").

A passkey is a second step of the staff sign-in, in place of the authenticator-app code. It
never replaces the password, so the two-step sign-in (FR-ACC-14) and its lockout (FR-ACC-15)
stay as they are:

1. Email and password (apps/accounts/signin.check_password), as before.
2. The authenticator code, a backup code, or now a passkey of this staff member.

* Adding a passkey needs a signed-in staff member and the current authenticator code (step-up,
  like new backup codes), and the holder is emailed about it.
* Only a staff member whose authenticator app is set up can add or use one: the signed-in
  session is still verified against that device, as after a backup code.
* A failed passkey answer at step 2 counts towards the same lockout as a wrong code.
"""
from django.conf import settings
from django.db import IntegrityError, transaction

from apps.accounts import signin
from apps.accounts.models import StaffAccount
from apps.common.errors import ApiError, invalid, not_found
from apps.passkeys import rp
from apps.passkeys.models import Passkey, WebAuthnChallenge

Purpose = WebAuthnChallenge.Purpose


def _limit() -> int:
    return settings.GATEWAY_RULES["PASSKEYS_PER_CUSTOMER"]


def available(account: StaffAccount) -> bool:
    """Step 2 may offer a passkey: one is registered and the authenticator app is set up."""
    return (signin.confirmed_device(account) is not None
            and Passkey.objects.filter(staff=account).exists())


def list_passkeys(account: StaffAccount) -> dict:
    return {"object": "list", "data": [rp.to_dict(p) for p in account.passkeys.all()],
            "limit": _limit()}


def registration_options(account: StaffAccount) -> dict:
    if signin.confirmed_device(account) is None:
        raise ApiError(409, "setup_required", "Set up your authenticator app first.")
    if account.passkeys.count() >= _limit():
        raise ApiError(409, "passkey_limit", f"A staff account can have up to {_limit()} "
                       "passkeys. Remove one first.")
    return {"options": rp.registration_options(account)}


def _clean_name(value, account: StaffAccount) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return f"Passkey {account.passkeys.count() + 1}"
    if not isinstance(value, str) or len(value.strip()) > 60 or any(
            ord(c) < 32 for c in value):
        raise invalid("invalid_field", "Give the passkey a name of at most 60 characters.",
                      param="name")
    return value.strip()


def add(request, account: StaffAccount, data) -> dict:
    """POST {credential, name?, code}: `code` is the current authenticator code."""
    if not isinstance(data, dict) or set(data) - {"credential", "name", "code"}:
        raise invalid("invalid_field", "Send 'credential', 'name' and 'code' only.")
    name = _clean_name(data.get("name"), account)
    signin.confirm_with_totp(request, account, data.get("code"), "add_passkey")
    new = rp.finish_registration(account, data)
    try:
        with transaction.atomic():
            locked = StaffAccount.objects.select_for_update().get(pk=account.pk)
            if locked.passkeys.count() >= _limit():
                raise ApiError(409, "passkey_limit", f"A staff account can have up to "
                               f"{_limit()} passkeys. Remove one first.")
            passkey = Passkey.objects.create(
                staff=locked, credential_id=new.credential_id, public_key=new.public_key,
                sign_count=new.sign_count, aaguid=new.aaguid, backed_up=new.backed_up,
                transports=new.transports, name=name)
            signin._audit("staff.passkey_added", account, request,
                          passkey_id=passkey.public_id, synced=new.backed_up, mfa=True)
    except IntegrityError:
        raise ApiError(409, "passkey_exists", "This passkey is already registered.") from None
    signin._security_notice(
        account, "SecurePay: a passkey was added to your account",
        f"Hello,\n\nA passkey called \"{name}\" was just added to your SecurePay dashboard "
        f"account ({account.email}). You can now use it instead of the authenticator code "
        "when you sign in.\n\nIf this was not you, tell an administrator straight away.\n\n"
        "SecurePay")
    return rp.to_dict(passkey)


def remove(request, account: StaffAccount, passkey_id: str) -> None:
    passkey = Passkey.objects.filter(staff=account, public_id=passkey_id).first()
    if passkey is None:
        raise not_found()
    passkey.delete()
    signin._audit("staff.passkey_removed", account, request, passkey_id=passkey_id)


# --- step 2 of the sign-in ----------------------------------------------------------------

def sign_in_options(request) -> dict:
    account = signin.pending_account(request)
    if account.is_locked():
        request.session.pop(signin.PENDING_KEY, None)
        raise signin.locked_error(account)
    if not available(account):
        raise ApiError(409, "passkey_not_available", "This account has no passkey. Enter "
                       "the code from your authenticator app.")
    return {"options": rp.assertion_options(Purpose.STAFF_SIGN_IN, owner=account)}


def sign_in(request, data) -> signin.SignedIn:
    """Step 2 with a passkey. Signs the staff member in, or counts a failure."""
    if not isinstance(data, dict) or set(data) - {"credential"}:
        raise invalid("invalid_field", "Only 'credential' may be sent.")
    account = signin.pending_account(request)
    if account.is_locked():
        request.session.pop(signin.PENDING_KEY, None)
        raise signin.locked_error(account)
    device = signin.confirmed_device(account)
    if device is None:
        raise ApiError(409, "setup_required", "Set up your authenticator app first.")
    try:
        passkey, _ = rp.finish_assertion(data, Purpose.STAFF_SIGN_IN)
        if passkey.staff_id != account.pk:
            raise rp.passkey_failed("That passkey isn't registered with this account.")
    except ApiError as exc:
        if signin._record_failure(account, request, "passkey"):
            request.session.pop(signin.PENDING_KEY, None)
            raise signin.locked_error(StaffAccount.objects.get(pk=account.pk)) from None
        raise exc from None
    signin._complete_sign_in(request, account, device, mfa_setup=False, method="passkey",
                             passkey_id=passkey.public_id)
    return signin.SignedIn(account)
