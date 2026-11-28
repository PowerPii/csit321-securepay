from django.urls import path

from apps.redteam.api import EndRunView, RunsView

urlpatterns = [
    path("red_team/runs", RunsView.as_view(), name="red-team-runs"),
    path("red_team/runs/<str:run_id>/end", EndRunView.as_view(), name="red-team-run-end"),
]
