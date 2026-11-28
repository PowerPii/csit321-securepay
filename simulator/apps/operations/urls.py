"""Simulator API, called only by the gateway (authenticated, internal network).

Every call carries the gateway's operation ID; the simulator enforces uniqueness on it,
so a retry or status query never makes a second financial effect. `/sim/faults` is the
test-control API (SIMULATOR-CONTRACT.md section 8), present only with SIM_TEST_CONTROLS=1.
"""
from django.urls import path

from apps.faults import views as fault_views
from apps.issuing import views as issuing_views
from apps.operations import views
from apps.threeds import views as threeds_views

urlpatterns = [
    path("authorise", views.authorise, name="sim-authorise"),
    path("operations", views.operation_list, name="sim-operations"),
    path("operations/<str:operation_id>", views.operation_status, name="sim-operation"),
    path("capture", views.capture, name="sim-capture"),
    path("cancel", views.cancel, name="sim-cancel"),
    path("refund", views.refund, name="sim-refund"),
    path("issuing/cards", issuing_views.create, name="sim-issuing-create"),
    path("issuing/cards/<str:card_id>", issuing_views.detail, name="sim-issuing-card"),
    path("issuing/cards/<str:card_id>/cancel", issuing_views.cancel,
         name="sim-issuing-cancel"),
    path("3ds/authenticate", threeds_views.authenticate, name="sim-3ds-authenticate"),
    path("3ds/<str:three_ds_id>", threeds_views.detail, name="sim-3ds"),
    path("3ds/<str:three_ds_id>/challenge", threeds_views.challenge, name="sim-3ds-challenge"),
    path("faults", fault_views.faults, name="sim-faults"),
    path("faults/<str:fault_id>", fault_views.fault, name="sim-fault"),
]
