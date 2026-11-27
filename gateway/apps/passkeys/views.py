"""Shopper passkey API for the account pages, served under /customer/passkeys.

Same rules as the rest of /customer/ (apps/customers/views.py): same origin, JSON only, the
SameSite=Strict session cookie. The sign-in steps share the customer_auth rate limit.
"""
from typing import ClassVar

from rest_framework.response import Response

from apps.common.throttling import AnonRateThrottle, ScopedRateThrottle
from apps.customers.views import CustomerView
from apps.passkeys import service


class _SignInStep(CustomerView):
    throttle_classes: ClassVar = [AnonRateThrottle, ScopedRateThrottle]
    throttle_scope = "customer_auth"


class PasskeysView(CustomerView):
    """GET -> this account's passkeys."""

    def get(self, request):
        return Response(service.list_passkeys(self.session(request).customer))


class RegisterOptionsView(CustomerView):
    """POST {} -> {options} for navigator.credentials.create()."""

    def post(self, request):
        return Response(service.registration_options(self.session(request)))


class RegisterView(CustomerView):
    """POST {credential, name?} -> 201 and the new passkey."""

    def post(self, request):
        return Response(service.add(self.session(request), request.data, request), status=201)


class RemoveView(CustomerView):
    """POST {} -> 204: the passkey can no longer sign in or approve payments."""

    def post(self, request, passkey_id):
        service.remove(self.session(request), passkey_id, request)
        return Response(status=204)


class SignInOptionsView(_SignInStep):
    """POST {} -> {options} for navigator.credentials.get()."""

    def post(self, request):
        return Response(service.sign_in_options())


class SignInView(_SignInStep):
    """POST {credential, remember?} -> 200 and signed in, as with a password."""

    def post(self, request):
        from apps.customers import service as customers

        account, token, session = service.sign_in(request.data, request)
        return self.signed_in(Response(customers.me(account)), token, session)
