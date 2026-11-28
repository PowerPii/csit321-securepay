"""The learning loop for staff (v2, docs/v2/LEARNING-LOOP.md).

* GET  risk-models : the model deciding now, the challenger in the shadow with its holdout
  scores and live shadow counts, recent models, and how much labelled data there is.
  Analysts and administrators.
* POST risk-models/train : train a new challenger (replaces the current one). Analysts and
  administrators: a challenger only scores in the shadow, it changes no decision.
* POST risk-models/promote {version, code} : the challenger starts deciding. Administrators,
  with the current authenticator code (like key rotation).
* POST risk-models/rollback {code} : retire the champion and bring back what it replaced.
  Administrators, with the code.
"""
from rest_framework import status
from rest_framework.response import Response

from apps.accounts import signin
from apps.audit.models import AuditEvent
from apps.audit.models import record as audit
from apps.common.errors import invalid
from apps.common.views import body_or_empty
from apps.internal.access import ADMIN, ANALYST_OR_ADMIN, StaffAPIView
from apps.risk import learning


class _ModelsView(StaffAPIView):
    def _audit(self, action, version, **details):
        audit(action, actor_type=AuditEvent.ActorType.STAFF, actor_id=self.staff.public_id,
              target_type="risk_model", target_id=version,
              request_id=self.request.request_id, details=details)


class RiskModelsView(_ModelsView):
    roles = ANALYST_OR_ADMIN

    def get(self, request):
        return Response(learning.overview())


class TrainView(_ModelsView):
    roles = ANALYST_OR_ADMIN

    def post(self, request):
        row = learning.train_challenger(self.staff.public_id)
        holdout = row.training["holdout"]
        self._audit("risk_model.trained", row.version, rows=row.training["rows"],
                    attacks=row.training["attacks"], genuine=row.training["genuine"],
                    holdout_challenger=holdout["challenger"], holdout_champion=holdout["champion"])
        return Response({"trained": learning.model_to_dict(row), **learning.overview()},
                        status=status.HTTP_201_CREATED)


class PromoteView(_ModelsView):
    roles = ADMIN

    def post(self, request):
        body = body_or_empty(request)
        version = body.get("version")
        if not isinstance(version, str) or not version or len(version) > 64:
            raise invalid("invalid_field", "Send the challenger's 'version'.", "version")
        signin.confirm_with_totp(request._request, self.staff, body.get("code"),
                                 "promote_model")
        row = learning.promote(version, self.staff.public_id)
        self._audit("risk_model.promoted", row.version,
                    replaced=row.replaced.version if row.replaced_id else row.replaced_configured)
        return Response(learning.overview())


class RollbackView(_ModelsView):
    roles = ADMIN

    def post(self, request):
        body = body_or_empty(request)
        signin.confirm_with_totp(request._request, self.staff, body.get("code"),
                                 "rollback_model")
        retired, now_deciding = learning.rollback(self.staff.public_id)
        self._audit("risk_model.rolled_back", retired.version, now_deciding=now_deciding)
        return Response(learning.overview())
