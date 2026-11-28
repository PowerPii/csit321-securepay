"""The learning loop (v2, docs/v2/LEARNING-LOOP.md): train a challenger from labelled screening
history, score it in the shadow, compare, promote, roll back.

Labels come from what the gateway itself knows, never from the screened attempt:

* a red-team payment (apps.redteam) is an attack (1) or a background shopper (0), as the
  runner said when it made it;
* any other attempt that screening allowed is taken as genuine (0). Other attempts that were
  stopped, and anything screening could not screen, are left out: we do not know what they were.

Only attempts screened with the current feature schema are used, so every row has every
feature as it was at that moment (the stored decision record; nothing is recomputed).

The challenger is a HistGradientBoostingClassifier, like the configured model, with the
default thresholds of policy-0.1 (verify at 0.5, block at 0.9). Before it is kept, a
stratified quarter of the rows is held out and both models are scored on it: the champion
by what it actually decided, the challenger by what it would have decided with the same rule
hits. Then it is fitted again on all the rows and stored as the challenger.
"""
from __future__ import annotations

import hashlib
import io
import secrets
from collections import Counter
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from apps.common.errors import ApiError
from apps.common.timefmt import rfc3339
from apps.risk.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION
from apps.risk.models import RiskModel, ScreeningEvent, ScreeningOutcome
from apps.risk.pipeline import DEFAULT_POLICY, Outcome, Policy
from apps.risk.scoring import Thresholds, configured_model, refresh_registry

MIN_PER_CLASS = 20  # attacks and genuine attempts each, before a challenger can be trained
MAX_ROWS = 50_000  # the most recent attempts; training stays a few seconds
STOPPED = ("block", "verify", "not_screened")
Role = RiskModel.Role


@dataclass
class Row:
    features: dict[str, float]
    label: int
    scenario: str  # red-team scenario, "legit" or "genuine"
    outcome: str  # what the champion decided
    reasons: list[str]


def labelled_rows(limit: int = MAX_ROWS) -> list[Row]:
    events = (ScreeningEvent.objects
              .filter(feature_schema_version=FEATURE_SCHEMA_VERSION)
              .exclude(outcome=ScreeningOutcome.NOT_SCREENED)
              .order_by("-occurred_at")
              .values_list("features", "outcome", "reasons", "payment__red_team__kind",
                           "payment__red_team__scenario")[:limit])
    rows = []
    for features, outcome, reasons, kind, scenario in events:
        if not features or any(name not in features for name in FEATURE_NAMES):
            continue
        if kind == "attack":
            label = 1
        elif kind == "legit":
            label, scenario = 0, "legit"
        elif outcome == ScreeningOutcome.ALLOW:
            label, scenario = 0, "genuine"
        else:
            continue
        rows.append(Row(features, label, scenario, outcome, list(reasons or [])))
    return rows


def rule_outcome(row: Row) -> Outcome | None:
    """What the rules and lists alone said, read back from the decision record. The policy
    adds a `model_...` reason only when the model, not a rule, decided."""
    rule_reasons = [r for r in row.reasons if not r.startswith("model_")]
    if not rule_reasons:
        return None
    if row.outcome == "block" and "model_score_high" not in row.reasons:
        return Outcome.BLOCK
    return Outcome.VERIFY


def _matrix(rows: list[Row]):
    import numpy as np

    x = np.array([[float(r.features[n]) for n in FEATURE_NAMES] for r in rows], dtype=float)
    y = np.array([r.label for r in rows], dtype=int)
    return x, y


# More earlier activity can only make an attempt look riskier, never safer: the count features
# are monotonic. Without this a small training set teaches "few tries in this range" = attack
# (the first cards of every BIN attack) and stops ordinary shoppers.
INCREASING = {name for name in FEATURE_NAMES if name.endswith(("_10m", "_1h", "_24h"))
              and "allowed" not in name}


def _fit(x, y):
    from sklearn.ensemble import HistGradientBoostingClassifier

    model = HistGradientBoostingClassifier(
        max_iter=200, learning_rate=0.1, max_depth=4, class_weight="balanced", random_state=0,
        monotonic_cst=[1 if name in INCREASING else 0 for name in FEATURE_NAMES])
    return model.fit(x, y)


def _score_card(rows: list[Row], would: list[str]) -> dict:
    """Champion (actual) and challenger (would-be) outcomes for these rows."""
    def stopped(outcomes):
        return sum(o in STOPPED for o in outcomes)

    attacks = [i for i, r in enumerate(rows) if r.label == 1]
    genuine = [i for i, r in enumerate(rows) if r.label == 0]
    by_scenario = {}
    for name in sorted({rows[i].scenario for i in attacks}):
        idx = [i for i in attacks if rows[i].scenario == name]
        by_scenario[name] = {"attacks": len(idx),
                             "champion_stopped": stopped(rows[i].outcome for i in idx),
                             "challenger_stopped": stopped(would[i] for i in idx)}

    def rate(n, d):
        return round(n / d, 4) if d else None

    champ_a, chall_a = stopped(rows[i].outcome for i in attacks), stopped(would[i] for i in attacks)
    champ_g, chall_g = stopped(rows[i].outcome for i in genuine), stopped(would[i] for i in genuine)
    return {
        "attacks": len(attacks), "genuine": len(genuine),
        "champion": {"attacks_stopped": champ_a, "genuine_stopped": champ_g,
                     "caught_rate": rate(champ_a, len(attacks)),
                     "false_positive_rate": rate(champ_g, len(genuine))},
        "challenger": {"attacks_stopped": chall_a, "genuine_stopped": chall_g,
                       "caught_rate": rate(chall_a, len(attacks)),
                       "false_positive_rate": rate(chall_g, len(genuine))},
        "by_scenario": by_scenario,
    }


def data_summary() -> dict:
    rows = labelled_rows()
    counts = Counter(r.label for r in rows)
    return {"attacks": counts[1], "genuine": counts[0], "needed": MIN_PER_CLASS,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "ready": counts[1] >= MIN_PER_CLASS and counts[0] >= MIN_PER_CLASS}


def train_challenger(created_by: str) -> RiskModel:
    import joblib
    import numpy as np
    import sklearn
    from sklearn.model_selection import train_test_split

    rows = labelled_rows()
    counts = Counter(r.label for r in rows)
    if counts[1] < MIN_PER_CLASS or counts[0] < MIN_PER_CLASS:
        raise ApiError(409, "not_enough_labels",
                       f"A challenger needs at least {MIN_PER_CLASS} attacks and "
                       f"{MIN_PER_CLASS} genuine attempts screened with {FEATURE_SCHEMA_VERSION}. "
                       f"There are {counts[1]} attacks and {counts[0]} genuine attempts: run the "
                       "red team (docs/v2/RED-TEAM.md) and try again.",
                       extra={"attacks": counts[1], "genuine": counts[0]})
    x, y = _matrix(rows)
    thresholds = Thresholds("", DEFAULT_POLICY.verify_threshold, DEFAULT_POLICY.block_threshold)

    # Hold out a quarter, score both models on it, then refit on everything.
    idx = np.arange(len(rows))
    train_idx, test_idx = train_test_split(idx, test_size=0.25, stratify=y, random_state=0)
    trial = _fit(x[train_idx], y[train_idx])
    test_rows = [rows[i] for i in test_idx]
    scores = trial.predict_proba(x[test_idx])[:, list(trial.classes_).index(1)]
    would = [Policy(thresholds).decide(rule_outcome(r), float(s))[0].value
             for r, s in zip(test_rows, scores, strict=True)]
    holdout = _score_card(test_rows, would)
    final = _fit(x, y)

    buffer = io.BytesIO()
    joblib.dump(final, buffer)
    blob = buffer.getvalue()
    now = timezone.now()
    version = f"ll-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
    manifest = {
        "version": version, "sha256": hashlib.sha256(blob).hexdigest(),
        "sklearn_version": sklearn.__version__, "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "features": list(FEATURE_NAMES), "positive_class": 1,
        "policy": {"version": f"policy-{version}",
                   "verify_threshold": thresholds.verify_threshold,
                   "block_threshold": thresholds.block_threshold},
        "trained_by": "learning loop (apps.risk.learning)",
    }
    training = {
        "rows": len(rows), "attacks": counts[1], "genuine": counts[0],
        "scenarios": dict(Counter(r.scenario for r in rows if r.label == 1)),
        "holdout": holdout,
    }
    with transaction.atomic():
        RiskModel.objects.select_for_update().filter(role=Role.CHALLENGER).update(
            role=Role.RETIRED, retired_at=now)
        row = RiskModel.objects.create(version=version, role=Role.CHALLENGER, manifest=manifest,
                                       blob=blob, sha256=manifest["sha256"], training=training,
                                       created_by=created_by)
    transaction.on_commit(refresh_registry)
    refresh_registry()
    return row


def promote(version: str, staff_id: str) -> RiskModel:
    with transaction.atomic():
        row = RiskModel.objects.select_for_update().filter(version=version).first()
        if row is None or row.role != Role.CHALLENGER:
            raise ApiError(409, "not_the_challenger", "Only the current challenger can be "
                           "promoted. Reload the page to see the models as they are now.")
        old = RiskModel.objects.select_for_update().filter(role=Role.CHAMPION).first()
        now = timezone.now()
        if old is not None:
            old.role, old.retired_at = Role.RETIRED, now
            old.save(update_fields=["role", "retired_at"])
        row.role, row.promoted_at, row.promoted_by = Role.CHAMPION, now, staff_id
        row.replaced = old
        row.replaced_configured = "" if old else configured_model().version
        row.retired_at = None
        row.save(update_fields=["role", "promoted_at", "promoted_by", "replaced",
                                "replaced_configured", "retired_at"])
    refresh_registry()
    return row


def rollback(staff_id: str) -> tuple[RiskModel, str]:
    """Retire the champion and bring back what it replaced. Returns (retired, now deciding)."""
    with transaction.atomic():
        row = RiskModel.objects.select_for_update().filter(role=Role.CHAMPION).first()
        if row is None:
            raise ApiError(409, "nothing_to_roll_back", "The configured model is deciding; "
                           "there is no promoted model to roll back.")
        row.role, row.retired_at = Role.RETIRED, timezone.now()
        row.save(update_fields=["role", "retired_at"])
        back = row.replaced
        if back is not None:
            back.role, back.retired_at = Role.CHAMPION, None
            back.promoted_at, back.promoted_by = timezone.now(), staff_id
            back.save(update_fields=["role", "retired_at", "promoted_at", "promoted_by"])
    refresh_registry()
    return row, back.version if back else configured_model().version


# --- what the staff page shows ----------------------------------------------------------------

def shadow_stats(version: str) -> dict:
    """Since the challenger started scoring: labelled red-team attempts, and how often it would
    have decided differently on any attempt."""
    events = ScreeningEvent.objects.filter(shadow_model_version=version)
    stop = Q(outcome__in=STOPPED)
    shadow_stop = Q(shadow_outcome__in=STOPPED)
    agg = events.aggregate(
        screened=Count("id"),
        differs=Count("id", filter=~Q(shadow_outcome=F("outcome"))),
        attacks=Count("id", filter=Q(payment__red_team__kind="attack")),
        attacks_champion=Count("id", filter=Q(payment__red_team__kind="attack") & stop),
        attacks_challenger=Count("id", filter=Q(payment__red_team__kind="attack") & shadow_stop),
        legit=Count("id", filter=Q(payment__red_team__kind="legit")),
        legit_champion=Count("id", filter=Q(payment__red_team__kind="legit") & stop),
        legit_challenger=Count("id", filter=Q(payment__red_team__kind="legit") & shadow_stop),
    )
    return agg


def model_to_dict(row: RiskModel) -> dict:
    policy = row.manifest.get("policy") or {}
    return {
        "version": row.version, "role": row.role,
        "created_at": rfc3339(row.created_at), "created_by": row.created_by or None,
        "promoted_at": rfc3339(row.promoted_at), "promoted_by": row.promoted_by or None,
        "retired_at": rfc3339(row.retired_at),
        "replaced": (row.replaced.version if row.replaced_id else row.replaced_configured or None),
        "policy": {"verify": policy.get("verify_threshold"), "block": policy.get("block_threshold")},
        "training": row.training,
    }


def overview() -> dict:
    champion = RiskModel.objects.filter(role=Role.CHAMPION).select_related("replaced").first()
    challenger = RiskModel.objects.filter(role=Role.CHALLENGER).select_related("replaced").first()
    configured = configured_model()
    configured_policy = configured.policy or DEFAULT_POLICY
    if champion:
        active = {**model_to_dict(champion), "source": "promoted"}
    else:
        active = {"version": configured.version, "role": "champion", "source": "configured",
                  "policy": {"verify": configured_policy.verify_threshold,
                             "block": configured_policy.block_threshold}}
    return {
        "active": active,
        "challenger": ({**model_to_dict(challenger), "shadow": shadow_stats(challenger.version)}
                       if challenger else None),
        "history": [model_to_dict(r) for r in
                    RiskModel.objects.select_related("replaced").order_by("-created_at")[:10]],
        "data": data_summary(),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "settings_model": settings.RISK_MODEL_VERSION or None,
    }
