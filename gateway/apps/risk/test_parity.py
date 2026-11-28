"""Parity: the gateway's SQL features equal the research replay the model is trained on.

One random event stream (shared cards, emails and IPs, same-time ties, every outcome, gaps
from seconds to 40 days) is written as ScreeningEvent rows and also replayed by
research/features/gateway_schema.py. For every event, SqlFeatures at that event's time must
give the same values (19 in fs-0.3). If either side changes a definition, this fails.

Runs wherever the research folder sits next to the gateway (the repo, CI); skipped inside
the gateway image, which does not ship research/.
"""
import importlib.util
import math
import random
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from apps.payments.models import Payment
from apps.risk.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, SqlFeatures
from apps.risk.models import ScreeningEvent
from apps.risk.pipeline import Attempt

pytestmark = pytest.mark.django_db

RESEARCH = Path(__file__).resolve().parents[3] / "research" / "features" / "gateway_schema.py"


@pytest.fixture(scope="module")
def research():
    if not RESEARCH.is_file():
        pytest.skip("research/ is not next to the gateway (e.g. inside the gateway image)")
    spec = importlib.util.spec_from_file_location("gateway_schema", RESEARCH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up while defining
    spec.loader.exec_module(module)
    return module


def test_both_sides_agree_on_the_schema(research):
    assert research.FEATURE_SCHEMA_VERSION == FEATURE_SCHEMA_VERSION
    assert tuple(research.FEATURE_NAMES) == FEATURE_NAMES


def random_stream(rng, n):
    """(offset seconds, amount, card, email, ip, outcome, bin); ties and long gaps on purpose."""
    gaps = [0, 0, 1, 30, 299, 300, 600, 3599, 3600, 7200, 86399, 86400, 3 * 86400,
            7 * 86400, 12 * 86400, 30 * 86400, 40 * 86400]
    t, rows = 0, []
    for _ in range(n):
        t += rng.choice(gaps[:9] * 4 + gaps)
        rows.append((t, rng.randint(100, 90_000), rng.choice(["c1", "c2", "c3", ""]),
                     rng.choice(["e1", "e2", "e3", ""]), rng.choice(["i1", "i2", ""]),
                     rng.choice(["allow"] * 5 + ["verify", "block", "not_screened"]),
                     rng.choice(["424242", "555555", ""])))
    return rows


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_sql_features_equal_the_research_replay(research, shop, seed):
    rng = random.Random(seed)  # nosec B311 - test data
    rows = random_stream(rng, 120)
    base = timezone.now() - timedelta(seconds=rows[-1][0] + 60)
    for t, amount, card, email, ip, outcome, card_bin in rows:
        payment = Payment.create_for_merchant(shop.merchant, order_reference="PARITY",
                                              amount=amount, return_url="https://shop.test/r")
        ScreeningEvent.objects.create(
            payment=payment, merchant=shop.merchant, occurred_at=base + timedelta(seconds=t),
            amount=amount, card_fingerprint=card, email_hash=email, ip_hash=ip, outcome=outcome,
            card_bin=card_bin)

    expected = research.replay(
        research.Event(at=float(t), amount_cents=a, card=c, email=e, ip=i, outcome=o, bin=b)
        for t, a, c, e, i, o, b in rows)
    sql = SqlFeatures()
    for (t, amount, card, email, ip, _, card_bin), want in zip(rows, expected, strict=True):
        got = sql.build(Attempt(attempt_id="pay_x", merchant_id="mer_x", amount_cents=amount,
                                card_fingerprint=card, email_hash=email, ip_hash=ip,
                                card_bin=card_bin,
                                occurred_at=base + timedelta(seconds=t)))
        assert set(got) == set(want)
        for name in FEATURE_NAMES:
            assert math.isclose(got[name], want[name], rel_tol=1e-9, abs_tol=1e-9), (
                f"t={t} {name}: gateway {got[name]} research {want[name]}")


# The fraud experiment's synthetic golden vectors (contract 2.0, invented events only, no
# IEEE-CIS rows). Its card1 + addr1 group maps to our card key; every event is within 30 days,
# so its "since replay began" history equals our 30-day window. Unavailable (null) values
# are our -1 gap and 0 mean / ratio. Amounts are dollars there, cents here.
GOLDEN = RESEARCH.parent / "fixtures" / "experiment-golden-vectors-2.0.json"
GOLDEN_MAP = {
    "count_1h": ("card_attempts_1h", 1, 0.0),
    "count_24h": ("card_attempts_24h", 1, 0.0),
    "count_7d": ("card_attempts_7d", 1, 0.0),
    "prior_count": ("card_attempts_30d", 1, 0.0),
    "seconds_since_previous": ("card_seconds_since_last", 1, -1.0),
    "prior_mean_amount": ("card_mean_amount_30d", 100, 0.0),
    "amount_to_prior_mean": ("amount_to_card_mean_30d", 1, 0.0),
}


def golden_events():
    import json

    if not GOLDEN.is_file():
        pytest.skip("research/ is not next to the gateway")
    data = json.loads(GOLDEN.read_text())
    expected = {row["TransactionID"]: row["features"] for row in data["expected_history"]}
    rows = []
    for e in data["events"]:
        card = "" if e["card1"] is None or e["addr1"] is None else f"{e['card1']}|{e['addr1']}"
        rows.append((e["TransactionID"], float(e["TransactionDT"]),
                     round(e["TransactionAmt"] * 100), card))
    return rows, expected


def check_golden(event_id, got, expected):
    for theirs, (ours, scale, missing) in GOLDEN_MAP.items():
        value = expected[event_id][theirs]
        want = missing if value is None else value * scale
        assert math.isclose(got[ours], want, rel_tol=1e-10, abs_tol=1e-9), (
            f"event {event_id} {ours}: got {got[ours]}, golden {theirs}={value}")


def test_research_replay_matches_the_experiment_golden_vectors(research):
    rows, expected = golden_events()
    out = research.replay(research.Event(at=t, amount_cents=a, card=c) for _, t, a, c in rows)
    for (event_id, *_), got in zip(rows, out, strict=True):
        check_golden(event_id, got, expected)


def test_sql_features_match_the_experiment_golden_vectors(shop):
    rows, expected = golden_events()
    base = timezone.now() - timedelta(seconds=rows[-1][1] + 60)
    for _, t, amount, card in rows:
        payment = Payment.create_for_merchant(shop.merchant, order_reference="GOLDEN",
                                              amount=1000,  # the payment row only anchors the event
                                              return_url="https://shop.test/r")
        ScreeningEvent.objects.create(
            payment=payment, merchant=shop.merchant, occurred_at=base + timedelta(seconds=t),
            amount=amount, card_fingerprint=card, outcome="allow")
    sql = SqlFeatures()
    for event_id, t, amount, card in rows:
        got = sql.build(Attempt(attempt_id="pay_x", merchant_id="mer_x", amount_cents=amount,
                                card_fingerprint=card, occurred_at=base + timedelta(seconds=t)))
        check_golden(event_id, got, expected)
