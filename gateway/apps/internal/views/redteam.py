"""Red team live view for fraud analysts and administrators (docs/v2/RED-TEAM.md)."""
from rest_framework.response import Response

from apps.common.errors import not_found
from apps.internal.access import ANALYST_OR_ADMIN, StaffAPIView
from apps.redteam import service
from apps.redteam.models import RedTeamRun


class RedTeamRunsView(StaffAPIView):
    """GET: the latest runs, newest first, and whether the red team is enabled here."""

    roles = ANALYST_OR_ADMIN

    def get(self, request):
        return Response({**service.runs_list(), "enabled": service.enabled()})


class RedTeamRunView(StaffAPIView):
    """GET: one run's live score, per 10 seconds (the page polls it while the run is live)."""

    roles = ANALYST_OR_ADMIN

    def get(self, request, run_id):
        run = RedTeamRun.objects.filter(public_id=run_id).first()
        if run is None:
            raise not_found()
        return Response(service.live(run))
