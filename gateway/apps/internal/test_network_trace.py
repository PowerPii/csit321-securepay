"""v2 network split: the dashboard shows how each operation crossed the card network."""
import json
from pathlib import Path

import pytest

from apps.internal.tests import operator, pay  # noqa: F401
from apps.payments.models import Operation

pytestmark = pytest.mark.django_db(databases=["default", "vault"])

EXAMPLE = Path(__file__).resolve().parents[3] / "contracts/examples/simulator/" \
    "authorise-approved.200.json"


def detail(operator, payment):  # noqa: F811
    r = operator.get(f"/internal/payments/{payment.public_id}")
    assert r.status_code == 200, r.content
    return r.json()["operations"][0]


def test_trace_comes_from_the_signed_reply(operator, shop, fake_sim):  # noqa: F811
    payment = pay(shop)
    op = Operation.objects.get(payment=payment, type="authorise")
    assert detail(operator, payment)["network"] is None  # the fake simulator is pre-v2

    reply = json.loads(EXAMPLE.read_text())
    reply["network"]["trace"][0]["extra"] = "dropped"
    op.evidence_body = json.dumps(reply)
    op.save(update_fields=["evidence_body"])
    net = detail(operator, payment)["network"]
    assert net["response_code"] == "00" and net["auth_code"] == "A1B2C3"
    assert [h["node"] for h in net["trace"]] == ["acquirer", "switch", "issuer"]
    assert "extra" not in net["trace"][0]


@pytest.mark.parametrize("evidence", [
    "", "not json", "[]", json.dumps({"network": {"trace": "x"}}),
    json.dumps({"network": {"response_code": "00",
                            "trace": [{"node": "mystery", "mti_in": "0100", "mti_out": "0110",
                                       "response_code": "00", "elapsed_ms": 1}]}}),
])
def test_malformed_or_missing_trace_is_no_trace(operator, shop, fake_sim, evidence):  # noqa: F811
    payment = pay(shop)
    Operation.objects.filter(payment=payment).update(evidence_body=evidence)
    assert detail(operator, payment)["network"] is None
