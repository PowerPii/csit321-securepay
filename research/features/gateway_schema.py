"""The gateway's screening features (fs-0.2), replayed offline over a list of events.

This is the research-side twin of `gateway/apps/risk/features.py` (`SqlFeatures`). The gateway
counts earlier `ScreeningEvent` rows in SQL; this module counts the same things over events in
memory, so a model can be trained on exactly what the gateway will compute when it scores a
live payment. `gateway/apps/risk/test_parity.py` replays one event stream through both and
requires the same values, so the two cannot drift apart unnoticed.

Rules both sides follow:

* Only events strictly before the attempt count (same timestamp = not yet seen), so the
  replay never leaks the future.
* A window of length w covers [t - w, t).
* An empty key (no card fingerprint, email or IP) gives zero counts for that key.
* `card_seconds_since_last` is -1 and the mean and ratio are 0 when the card has no attempt
  in the last 30 days.

Pure Python (standard library only), so the gateway's tests can import it without pandas.
"""
from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

FEATURE_SCHEMA_VERSION = "fs-0.3"

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
    "card_attempts_7d",
    "card_attempts_30d",
    "card_seconds_since_last",
    "card_mean_amount_30d",
    "amount_to_card_mean_30d",
    # fs-0.3: the card's issuer range (first 6 digits)
    "bin_attempts_10m",
    "bin_distinct_cards_1h",
    "bin_distinct_ips_1h",
)

MINUTE, HOUR, DAY = 60.0, 3600.0, 86400.0
BLOCKED = ("block", "not_screened")
NO_EARLIER_ATTEMPT = -1.0


@dataclass(frozen=True)
class Event:
    """One screening attempt. `at` is in seconds on any fixed clock."""

    at: float
    amount_cents: int
    card: str = ""
    email: str = ""
    ip: str = ""
    outcome: str = "allow"
    bin: str = ""  # fs-0.3  # allow, verify, block, not_screened


@dataclass
class _History:
    """Earlier events of one key, in time order, with running totals for O(log n) windows."""

    times: list[float] = field(default_factory=list)
    others: list[str] = field(default_factory=list)  # the other key for distinct counts
    amount_sum: list[int] = field(default_factory=lambda: [0])
    blocked: list[int] = field(default_factory=lambda: [0])
    allowed: list[int] = field(default_factory=lambda: [0])

    def add(self, event: Event, other: str) -> None:
        self.times.append(event.at)
        self.others.append(other)
        self.amount_sum.append(self.amount_sum[-1] + event.amount_cents)
        self.blocked.append(self.blocked[-1] + (event.outcome in BLOCKED))
        self.allowed.append(self.allowed[-1] + (event.outcome == "allow"))

    def start(self, t: float, window: float) -> int:
        return bisect_left(self.times, t - window)

    def distinct_others(self, t: float, window: float) -> int:
        return len({o for o in self.others[self.start(t, window):] if o})


def features_for(event: Event, cards: dict[str, _History], emails: dict[str, _History],
                 ips: dict[str, _History], bins: dict[str, _History] | None = None,
                 bin_ips: dict[str, _History] | None = None) -> dict[str, float]:
    """Features of `event` given the histories of everything strictly before it."""
    t = event.at
    f = {name: 0.0 for name in FEATURE_NAMES}
    f["amount_cents"] = float(event.amount_cents)
    f["card_seconds_since_last"] = NO_EARLIER_ATTEMPT

    card = cards.get(event.card) if event.card else None
    if card and card.times:
        n = len(card.times)
        i24, i30 = card.start(t, DAY), card.start(t, 30 * DAY)
        f["card_attempts_10m"] = float(n - card.start(t, 10 * MINUTE))
        f["card_attempts_1h"] = float(n - card.start(t, HOUR))
        f["card_attempts_24h"] = float(n - i24)
        f["card_blocked_24h"] = float(card.blocked[n] - card.blocked[i24])
        f["card_distinct_emails_24h"] = float(card.distinct_others(t, DAY))
        f["card_allowed_30d"] = float(card.allowed[n] - card.allowed[i30])
        f["card_attempts_7d"] = float(n - card.start(t, 7 * DAY))
        f["card_attempts_30d"] = float(n - i30)
        if n > i30:
            mean = (card.amount_sum[n] - card.amount_sum[i30]) / (n - i30)
            f["card_seconds_since_last"] = t - card.times[-1]
            f["card_mean_amount_30d"] = mean
            f["amount_to_card_mean_30d"] = event.amount_cents / mean if mean else 0.0

    email = emails.get(event.email) if event.email else None
    if email and email.times:
        f["email_attempts_24h"] = float(len(email.times) - email.start(t, DAY))
        f["email_distinct_cards_24h"] = float(email.distinct_others(t, DAY))

    ip = ips.get(event.ip) if event.ip else None
    if ip and ip.times:
        f["ip_attempts_1h"] = float(len(ip.times) - ip.start(t, HOUR))
        f["ip_distinct_cards_1h"] = float(ip.distinct_others(t, HOUR))

    rng = (bins or {}).get(event.bin) if event.bin else None
    if rng and rng.times:
        f["bin_attempts_10m"] = float(len(rng.times) - rng.start(t, 10 * MINUTE))
        f["bin_distinct_cards_1h"] = float(rng.distinct_others(t, HOUR))
        f["bin_distinct_ips_1h"] = float((bin_ips or {})[event.bin].distinct_others(t, HOUR))
    return f


def replay(events: Iterable[Event]) -> list[dict[str, float]]:
    """Features for every event, in the order given. Events at the same time do not see
    each other, as in the gateway (`occurred_at < now`)."""
    events = list(events)
    order: Sequence[int] = sorted(range(len(events)), key=lambda i: events[i].at)
    cards: dict[str, _History] = {}
    emails: dict[str, _History] = {}
    ips: dict[str, _History] = {}
    bins: dict[str, _History] = {}  # others = card
    bin_ips: dict[str, _History] = {}  # others = IP
    out: list[dict[str, float] | None] = [None] * len(events)
    pos = 0
    while pos < len(order):
        end = pos + 1
        while end < len(order) and events[order[end]].at == events[order[pos]].at:
            end += 1
        group = order[pos:end]
        for i in group:  # score the whole same-time group first
            out[i] = features_for(events[i], cards, emails, ips, bins, bin_ips)
        for i in group:  # then let later events see it
            e = events[i]
            if e.card:
                cards.setdefault(e.card, _History()).add(e, e.email)
            if e.email:
                emails.setdefault(e.email, _History()).add(e, e.card)
            if e.ip:
                ips.setdefault(e.ip, _History()).add(e, e.card)
            if e.bin:
                bins.setdefault(e.bin, _History()).add(e, e.card)
                bin_ips.setdefault(e.bin, _History()).add(e, e.ip)
        pos = end
    return out  # type: ignore[return-value]
