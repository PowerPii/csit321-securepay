"""Screening entry point used by checkout: run the pipeline and keep the decision record.

    screen_payment(payment, ip=...) -> Screening

Called inside the checkout's transaction with the payment row locked. The pipeline's
database work runs in a savepoint with a Postgres statement_timeout equal to the screening
budget, so a slow feature query is cut off rather than holding the payment; the savepoint
keeps the outer transaction usable so the attempt can still be blocked and recorded.

The ScreeningEvent row is written for every attempt, including unavailable ones, so it is
both the analyst's decision record and the history later attempts are counted against.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from apps.payments.models import Payment
from apps.risk import hashing
from apps.risk.features import FEATURE_SCHEMA_VERSION
from apps.risk.models import ScreeningEvent, ScreeningOutcome
from apps.risk.pipeline import (
    RULES_VERSION,
    Attempt,
    DbRules,
    Decision,
    Outcome,
    Pipeline,
    ScreeningUnavailable,
)

logger = logging.getLogger(__name__)


@dataclass
class Screening:
    outcome: ScreeningOutcome
    decision: Decision | None  # None when screening was unavailable
    event: ScreeningEvent

    @property
    def reasons(self) -> list[str]:
        return list(self.event.reasons)


def attempt_for(payment: Payment, ip: str | None, now) -> Attempt:
    return Attempt(
        attempt_id=payment.public_id,
        merchant_id=payment.merchant.public_id,
        amount_cents=payment.settlement_amount,  # v2 FX: screening works in SGD
        card_fingerprint=payment.card_fingerprint,
        occurred_at=now,
        email_hash=hashing.email_hash(payment.customer_email),
        ip_hash=hashing.ip_hash(ip),
    )


def _simulated_outage():
    """DEBUG-only switch for the outage test (NFR-AVA-04): RISK_SIMULATE_OUTAGE=error|slow."""
    mode = settings.RISK_SIMULATE_OUTAGE if settings.DEBUG else ""
    if mode == "error":
        raise RuntimeError("simulated screening outage")
    if mode == "slow":
        time.sleep((settings.GATEWAY_RULES["SCREENING_TIMEOUT_MS"] + 100) / 1000)


def _run(pipeline: Pipeline, attempt: Attempt) -> Decision:
    budget_ms = settings.GATEWAY_RULES["SCREENING_TIMEOUT_MS"]
    started = time.perf_counter()
    try:
        with transaction.atomic():  # savepoint: a cancelled query must not poison the payment
            with connection.cursor() as cur:
                cur.execute("SELECT current_setting('statement_timeout'), "
                            "set_config('statement_timeout', %s, true)", [f"{budget_ms}ms"])
                previous = cur.fetchone()[0]
            try:
                _simulated_outage()
                decision = pipeline.screen(attempt)
            finally:
                with connection.cursor() as cur:
                    cur.execute("SELECT set_config('statement_timeout', %s, true)", [previous])
    except DatabaseError as exc:  # statement_timeout, connection trouble
        raise ScreeningUnavailable(type(exc).__name__) from exc
    except ScreeningUnavailable:
        raise
    except Exception as exc:
        raise ScreeningUnavailable(type(exc).__name__) from exc
    elapsed = (time.perf_counter() - started) * 1000
    if elapsed > budget_ms:
        raise ScreeningUnavailable(f"timeout after {elapsed:.0f} ms (budget {budget_ms} ms)")
    return decision


def screen_payment(payment: Payment, *, ip: str | None = None,
                   pipeline: Pipeline | None = None) -> Screening:
    now = timezone.now()
    attempt = attempt_for(payment, ip, now)
    started = time.perf_counter()
    try:
        decision = _run(pipeline or Pipeline(rules=DbRules()), attempt)
    except ScreeningUnavailable as exc:
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        logger.warning("Screening unavailable for %s: %s", payment.public_id, exc)
        event = ScreeningEvent.objects.create(
            payment=payment, merchant=payment.merchant, occurred_at=now, amount=payment.settlement_amount,
            card_fingerprint=attempt.card_fingerprint, email_hash=attempt.email_hash,
            ip_hash=attempt.ip_hash, outcome=ScreeningOutcome.NOT_SCREENED,
            reasons=["screening_unavailable"], rules_version=RULES_VERSION,
            feature_schema_version=FEATURE_SCHEMA_VERSION, elapsed_ms=elapsed,
            error=str(exc)[:200],
        )
        return Screening(ScreeningOutcome.NOT_SCREENED, None, event)

    event = ScreeningEvent.objects.create(
        payment=payment, merchant=payment.merchant, occurred_at=now, amount=payment.settlement_amount,
        card_fingerprint=attempt.card_fingerprint, email_hash=attempt.email_hash,
        ip_hash=attempt.ip_hash, outcome=ScreeningOutcome(decision.outcome.value),
        score=decision.score, reasons=decision.reasons, features=decision.features,
        model_version=decision.model_version or "", rules_version=decision.rules_version,
        policy_version=decision.policy_version,
        feature_schema_version=decision.feature_schema_version,
        elapsed_ms=decision.elapsed_ms, explanation=decision.explanation,
    )
    return Screening(ScreeningOutcome(decision.outcome.value), decision, event)


__all__ = ["Outcome", "Screening", "screen_payment"]
