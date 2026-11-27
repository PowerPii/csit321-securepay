"""Hosted checkout API for frontend/checkout (same origin, no login).

JSON only, so a cross-site form post cannot reach it (the browser would need a CORS
preflight). Replies are never cached, and the card number is never echoed or logged.
"""
from rest_framework.response import Response

from apps.checkout import service
from apps.common.clientip import client_ip
from apps.common.views import PublicJSONView
from apps.customers.service import COOKIE as CUSTOMER_COOKIE


class CheckoutView(PublicJSONView):
    pass


class SessionView(CheckoutView):
    """GET: order summary and current state (C3, and C6/C7 after a reload)."""

    def get(self, request, session_id):
        return Response(service.to_dict(service.load(session_id)))


class CardView(CheckoutView):
    """POST {number, expiry_month, expiry_year, cvc, email?}: vault the card."""

    def post(self, request, session_id):
        payment = service.load(session_id)
        payment = service.attach_card(payment, self.body(request), request.request_id)
        return Response(service.to_dict(payment))


class SubmitView(CheckoutView):
    """POST {}: pay. Answers with the result once the simulator answered or timed out."""

    def post(self, request, session_id):
        payment = service.load(session_id)
        payment = service.submit(payment, request.request_id, ip=client_ip(request),
                                 customer_token=request.COOKIES.get(CUSTOMER_COOKIE))
        return Response(service.to_dict(payment))


class VerifyView(CheckoutView):
    """POST {code}: the 6-digit code from the email (C5)."""

    def post(self, request, session_id):
        payment = service.load(session_id)
        payment = service.verify(payment, self.body(request), request.request_id)
        return Response(service.to_dict(payment))


class ResendView(CheckoutView):
    """POST {}: email a new code, which replaces the old one."""

    def post(self, request, session_id):
        payment = service.load(session_id)
        payment = service.resend(payment, request.request_id)
        return Response(service.to_dict(payment))


class PasskeyOptionsView(CheckoutView):
    """POST {}: v2, {options} to approve this payment with a passkey of the signed-in
    shopper (navigator.credentials.get())."""

    def post(self, request, session_id):
        payment = service.load(session_id)
        return Response(service.passkey_options(payment, request.COOKIES.get(CUSTOMER_COOKIE)))


class PasskeyVerifyView(CheckoutView):
    """POST {credential}: v2, the passkey answer, in place of the 6-digit code."""

    def post(self, request, session_id):
        payment = service.load(session_id)
        payment = service.verify_passkey(payment, self.body(request), request.request_id,
                                         request.COOKIES.get(CUSTOMER_COOKIE))
        return Response(service.to_dict(payment))


class ResultView(SessionView):
    pass
