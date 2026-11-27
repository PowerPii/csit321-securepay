"""Red-team runs on the merchant API, for the runner (docs/v2/RED-TEAM.md). Test keys only.

POST /v1/red_team/runs {scenario, note?} -> 201 with the run and its bearer `token`, shown
once (so these two calls are not replayed by Idempotency-Key: a stored reply would keep it).
POST /v1/red_team/runs/{id}/end -> the run, ended.
"""
from rest_framework.response import Response

from apps.merchants.api import MerchantAPIView
from apps.redteam import service


class RunsView(MerchantAPIView):
    def post(self, request):
        body = request.data if isinstance(request.data, dict) else None
        return Response(service.start_run(request.user, request.auth, body), status=201)


class EndRunView(MerchantAPIView):
    def post(self, request, run_id: str):
        return Response(service.end_run(request.user, run_id))
