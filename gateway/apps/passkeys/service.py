"""Shopper passkeys on the account pages: add, list, remove, and sign in with one
(docs/v2/PASSKEYS.md). Approving a payment with a passkey is in apps/checkout/service.py.

* Adding a passkey needs a signed-in session, and the shopper gets an email about it, as
  for a password change.
* Signing in with a passkey proves the device and the person (user verification), so it
  starts a session even while password sign-in is locked after wrong passwords.
* At most PASSKEYS_PER_CUSTOMER passkeys per account.
"""
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts import mail
from apps.common.errors import ApiError, invalid, not_found
from apps.customers import service as customers
from apps.customers.models import CustomerAccount, CustomerSession
from apps.passkeys import rp
from apps.passkeys.models import Passkey, WebAuthnChallenge

Purpose = WebAuthnChallenge.Purpose


def _limit() -> int:
    return settings.GATEWAY_RULES["PASSKEYS_PER_CUSTOMER"]


def list_passkeys(customer: CustomerAccount) -> dict:
    rows = [rp.to_dict(p) for p in customer.passkeys.all()]
    return {"object": "list", "data": rows, "limit": _limit()}


def registration_options(session: CustomerSession) -> dict:
    customer = session.customer
    if customer.passkeys.count() >= _limit():
        raise ApiError(409, "passkey_limit", f"An account can have up to {_limit()} passkeys. "
                       "Remove one first.")
    return {"options": rp.registration_options(customer)}


def _clean_name(value, customer: CustomerAccount) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return f"Passkey {customer.passkeys.count() + 1}"
    if not isinstance(value, str) or len(value.strip()) > 60 or any(
            ord(c) < 32 for c in value):
        raise invalid("invalid_field", "Give the passkey a name of at most 60 characters.",
                      param="name")
    return value.strip()


def add(session: CustomerSession, data, request=None) -> dict:
    data = customers._body_fields(data, ("credential", "name"))
    customer = session.customer
    name = _clean_name(data.get("name"), customer)
    new = rp.finish_registration(customer, data)
    try:
        with transaction.atomic():
            locked = CustomerAccount.objects.select_for_update().get(pk=customer.pk)
            if locked.passkeys.count() >= _limit():
                raise ApiError(409, "passkey_limit", f"An account can have up to {_limit()} "
                               "passkeys. Remove one first.")
            passkey = Passkey.objects.create(
                customer=locked, credential_id=new.credential_id, public_key=new.public_key,
                sign_count=new.sign_count, aaguid=new.aaguid, backed_up=new.backed_up,
                transports=new.transports, name=name)
            customers._audit("customer.passkey_added", customer, request,
                             passkey_id=passkey.public_id, synced=new.backed_up)
            mail.notice(customer.email, "a passkey was added to your account",
                        f"Hello {customer.full_name},\n\nA passkey called \"{name}\" was just "
                        "added to your SecurePay account. You can now sign in and approve "
                        "payments with it.\n\nIf this was not you, remove it on your account "
                        "page and change your password straight away.")
    except IntegrityError:
        raise ApiError(409, "passkey_exists", "This passkey is already registered.") from None
    return rp.to_dict(passkey)


def remove(session: CustomerSession, passkey_id: str, request=None) -> None:
    passkey = Passkey.objects.filter(customer=session.customer, public_id=passkey_id).first()
    if passkey is None:
        raise not_found()
    passkey.delete()
    customers._audit("customer.passkey_removed", session.customer, request,
                     passkey_id=passkey_id)


def sign_in_options() -> dict:
    return {"options": rp.assertion_options(Purpose.SIGN_IN)}


def sign_in(data, request=None) -> tuple[CustomerAccount, str, CustomerSession]:
    data = customers._body_fields(data, ("credential", "remember"))
    passkey, _ = rp.finish_assertion(data, Purpose.SIGN_IN)
    account = passkey.customer
    if not account.is_active:
        raise rp.passkey_failed("That passkey isn't registered with this account.")
    with transaction.atomic():
        CustomerAccount.objects.filter(pk=account.pk).update(last_sign_in_at=timezone.now())
        token, session = customers._start_session(account, bool(data.get("remember")))
        customers._audit("customer.signed_in", account, request, method="passkey",
                         passkey_id=passkey.public_id)
    return account, token, session
