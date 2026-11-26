"""Customer account API for the account pages (C10 to C12), served under /customer/.

Same origin as the pages, JSON only (a cross-site form cannot post JSON without a CORS
preflight), and the `sp_customer` cookie is HttpOnly, Secure and SameSite=Strict, so another
site can neither send nor read it: the same protection the guest status check uses instead
of a CSRF token (decided 13 Nov 2026).
"""
from typing import ClassVar

from django.conf import settings
from rest_framework.response import Response

from apps.common.errors import ApiError
from apps.common.throttling import AnonRateThrottle, ScopedRateThrottle
from apps.common.views import PublicJSONView, body_or_empty
from apps.customers import history, insights, service


class CustomerView(PublicJSONView):
    def initial(self, request, *args, **kwargs):
        # Demo core-only mode (settings.DEMO_CORE_ONLY): accounts are paused, not removed.
        # Checked before throttles and sessions so every account endpoint answers the same.
        if settings.DEMO_CORE_ONLY:
            raise ApiError(404, "accounts_unavailable",
                           "Customer accounts aren't available on this server right now.")
        super().initial(request, *args, **kwargs)

    @staticmethod
    def token(request) -> str | None:
        return request.COOKIES.get(service.COOKIE)

    def session(self, request):
        return service.require_session(self.token(request))

    @staticmethod
    def signed_in(response: Response, token: str, session) -> Response:
        max_age = None
        if session.persistent:
            max_age = settings.GATEWAY_RULES["CUSTOMER_SESSION_MAX_HOURS"] * 3600
        response.set_cookie(service.COOKIE, token, path="/", httponly=True, secure=True,
                            samesite="Strict", max_age=max_age)
        return response

    @staticmethod
    def signed_out(response: Response) -> Response:
        response.delete_cookie(service.COOKIE, path="/", samesite="Strict")
        return response


class _AuthStep(CustomerView):
    throttle_classes: ClassVar = [AnonRateThrottle, ScopedRateThrottle]
    throttle_scope = "customer_auth"


class _EmailStep(CustomerView):
    throttle_classes: ClassVar = [AnonRateThrottle, ScopedRateThrottle]
    throttle_scope = "account_email"


class RegisterView(_AuthStep):
    """POST {full_name, email, password, remember?} -> 201 and signed in. 409 email_taken."""

    def post(self, request):
        account, token, session = service.register(request.data, request)
        return self.signed_in(Response(service.me(account), status=201), token, session)


class SignInView(_AuthStep):
    """POST {email, password, remember?} -> 200 and signed in."""

    def post(self, request):
        account, token, session = service.sign_in(request.data, request)
        return self.signed_in(Response(service.me(account)), token, session)


class MeView(CustomerView):
    """GET -> the signed-in customer, or 401 sign_in_required."""

    def get(self, request):
        return Response(service.me(self.session(request).customer))


class SignOutView(CustomerView):
    def post(self, request):
        service.sign_out(self.token(request))
        return self.signed_out(Response(status=204))


class SignOutEverywhereView(CustomerView):
    """'Sign out on every device at once' (C10)."""

    def post(self, request):
        service.sign_out_everywhere(self.session(request), request)
        return self.signed_out(Response(status=204))


class PasswordView(CustomerView):
    """POST {current_password, password}: this browser stays signed in, others end."""

    def post(self, request):
        ended = service.change_password(self.session(request), request.data, request)
        return Response({"message": "Your password has been changed. You were signed out on "
                         "every other device.", "sessions_ended": ended})


class ProfileView(CustomerView):
    """GET: the personal data kept about the customer. POST {current_password, full_name?,
    email?}: correct it (FR-ACC-13)."""

    def get(self, request):
        return Response(service.personal_data(self.session(request).customer))

    def post(self, request):
        return Response(service.update_details(self.session(request), request.data, request))


class PaymentsView(CustomerView):
    def get(self, request):
        return Response(history.history(self.session(request).customer))


class InsightsView(CustomerView):
    """GET -> this customer's spending for the last 12 months, by month and by merchant."""

    def get(self, request):
        return Response(insights.insights(self.session(request).customer))


class PaymentDetailView(CustomerView):
    def get(self, request, reference):
        return Response(history.payment_detail(self.session(request).customer, reference))


class PaymentReceiptView(CustomerView):
    """GET -> the receipt PDF for one of this customer's charged payments."""

    def get(self, request, reference):
        from apps.guest.receipts import pdf_response

        return pdf_response(history.own_payment(self.session(request).customer, reference))


class ResetRequestView(_EmailStep):
    """POST {email} -> 202, the same message whether or not the email has an account."""

    def post(self, request):
        return Response({"message": service.request_reset(request.data, request)}, status=202)


class ResetCheckView(_AuthStep):
    """POST {token} -> {email}, or 400 link_invalid. Does not use the link."""

    def post(self, request):
        data = body_or_empty(request)
        return Response(service.check_reset_link(data.get("token")))


class ResetConfirmView(_AuthStep):
    """POST {token, password} -> 200; every session of the account has ended."""

    def post(self, request):
        account = service.reset_password(request.data, request)
        response = Response({"email": account.email, "message": "Your password has been "
                             "changed. Sign in with the new password."})
        return self.signed_out(response)
