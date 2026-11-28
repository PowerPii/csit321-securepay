"""Feature schema fs-0.2: what screening knows about an attempt at the moment it is submitted.

Current-attempt features plus prior-activity counts over earlier `ScreeningEvent` rows keyed
by card fingerprint, email hash and IP hash, in fixed windows. Only events strictly before
this attempt are counted (architecture section 7), so the same definitions can be replayed
on historical data without leaking the future.

`FEATURE_NAMES` is the contract with the model: a trained artefact's manifest lists the
features it was trained on, and the loader refuses an artefact that asks for a feature
this schema does not produce (apps.risk.scoring). Add a feature here, bump the version,
and mirror it in research/features/gateway_schema.py: the parity test (test_parity.py)
replays the same events through both and requires the same values.

fs-0.2 (sprint 7) keeps every fs-0.1 feature unchanged and adds card history the IEEE-CIS
training data can also provide: attempts in 7 and 30 days, seconds since the previous
attempt, and the mean earlier amount. A value that does not exist yet (no earlier attempt)
is 0 for the mean and the ratio and -1 for the gap, so every feature is a finite number
(JSON has no NaN). Artefacts trained on fs-0.1 still load: their features are a subset.

fs-0.3 (v2 learning loop) adds the card's issuer range (BIN, its first 6 digits): attempts
from that range in 10 minutes, and distinct cards and addresses using it in an hour. A BIN
attack tries many cards of one range once each, from fresh addresses: every per-card,
per-email and per-IP count stays low, but these do not (docs/v2/LEARNING-LOOP.md).
"""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Avg, Count, Max, Q

from apps.risk.models import ScreeningEvent, ScreeningOutcome

FEATURE_SCHEMA_VERSION = "fs-0.3"
# Schemas whose artefacts this gateway can feed: each only added features to the one before.
COMPATIBLE_SCHEMAS = ("fs-0.1", "fs-0.2", "fs-0.3")

FEATURE_NAMES: tuple[str, ...] = (
    "amount_cents",
    "card_attempts_10m",
    "card_attempts_1h",
    "card_attempts_24h",
    "card_blocked_24h",
    "card_distinct_emails_24h",
    "card_allowed_30d",
    "email_attempts_24h",
    "email_distinct_cards_24h",
    "ip_attempts_1h",
    "ip_distinct_cards_1h",
    # fs-0.2
    "card_attempts_7d",
    "card_attempts_30d",
    "card_seconds_since_last",
    "card_mean_amount_30d",
    "amount_to_card_mean_30d",
    # fs-0.3
    "bin_attempts_10m",
    "bin_distinct_cards_1h",
    "bin_distinct_ips_1h",
)
NO_EARLIER_ATTEMPT = -1.0  # card_seconds_since_last when the card has no attempt in 30 days

BLOCKED = (ScreeningOutcome.BLOCK, ScreeningOutcome.NOT_SCREENED)


class SqlFeatures:
    """Indexed counts in four queries (card, email, IP, card range), one round trip each."""

    def build(self, attempt) -> dict[str, float]:
        now = attempt.occurred_at
        m10, h1, h24, d7, d30 = (now - timedelta(minutes=10), now - timedelta(hours=1),
                                 now - timedelta(hours=24), now - timedelta(days=7),
                                 now - timedelta(days=30))
        before = ScreeningEvent.objects.filter(occurred_at__lt=now)
        features = {name: 0.0 for name in FEATURE_NAMES}
        features["amount_cents"] = float(attempt.amount_cents)
        features["card_seconds_since_last"] = NO_EARLIER_ATTEMPT

        if attempt.card_fingerprint:
            card = before.filter(card_fingerprint=attempt.card_fingerprint,
                                 occurred_at__gte=d30).aggregate(
                card_attempts_10m=Count("id", filter=Q(occurred_at__gte=m10)),
                card_attempts_1h=Count("id", filter=Q(occurred_at__gte=h1)),
                card_attempts_24h=Count("id", filter=Q(occurred_at__gte=h24)),
                card_blocked_24h=Count("id", filter=Q(occurred_at__gte=h24,
                                                      outcome__in=BLOCKED)),
                card_distinct_emails_24h=Count("email_hash", distinct=True,
                                               filter=Q(occurred_at__gte=h24)
                                               & ~Q(email_hash="")),
                card_allowed_30d=Count("id", filter=Q(outcome=ScreeningOutcome.ALLOW)),
                card_attempts_7d=Count("id", filter=Q(occurred_at__gte=d7)),
                card_attempts_30d=Count("id"),
                mean_amount=Avg("amount"),
                last_at=Max("occurred_at"),
            )
            mean, last = card.pop("mean_amount"), card.pop("last_at")
            features.update({k: float(v) for k, v in card.items()})
            if last is not None:
                features["card_seconds_since_last"] = (now - last).total_seconds()
                features["card_mean_amount_30d"] = float(mean)
                features["amount_to_card_mean_30d"] = (
                    attempt.amount_cents / float(mean) if mean else 0.0)

        if attempt.email_hash:
            email = before.filter(email_hash=attempt.email_hash, occurred_at__gte=h24).aggregate(
                email_attempts_24h=Count("id"),
                email_distinct_cards_24h=Count("card_fingerprint", distinct=True,
                                               filter=~Q(card_fingerprint="")),
            )
            features.update({k: float(v) for k, v in email.items()})

        if attempt.ip_hash:
            ip = before.filter(ip_hash=attempt.ip_hash, occurred_at__gte=h1).aggregate(
                ip_attempts_1h=Count("id"),
                ip_distinct_cards_1h=Count("card_fingerprint", distinct=True,
                                           filter=~Q(card_fingerprint="")),
            )
            features.update({k: float(v) for k, v in ip.items()})

        if attempt.card_bin:
            card_bin = before.filter(card_bin=attempt.card_bin, occurred_at__gte=h1).aggregate(
                bin_attempts_10m=Count("id", filter=Q(occurred_at__gte=m10)),
                bin_distinct_cards_1h=Count("card_fingerprint", distinct=True,
                                            filter=~Q(card_fingerprint="")),
                bin_distinct_ips_1h=Count("ip_hash", distinct=True, filter=~Q(ip_hash="")),
            )
            features.update({k: float(v) for k, v in card_bin.items()})
        return features
