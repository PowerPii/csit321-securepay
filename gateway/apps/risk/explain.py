"""Why screening decided what it did, in plain words (extends FR-FRD-01, the decision record).

    TreeShapExplainer(estimator, feature_order)   built once when the model artefact loads
        .contributions(features) -> (base_log_odds, {feature: SHAP value in log-odds})
    explain(decision inputs) -> dict              stored on ScreeningEvent.explanation
    merchant_summary(explanation) -> list[str]    the merchant-safe version (no rules, no numbers)

Model contributions are exact path-dependent TreeSHAP values (Lundberg et al., 2020), the same
numbers the `shap` package's TreeExplainer gives for this model (checked to 1e-14 on the
hgb-fs02-2026-11-13 artefact), computed here with numpy so the gateway needs no new dependency.
They add up: the model's average output plus the contributions is exactly the attempt's raw
output (log-odds), so each one says how far that feature moved this score. On the gateway
model one explanation takes about 7 ms (p95), inside the screening budget (NFR-PER-02, p95
200 ms) and measured by `manage.py loadtest_screening`.

What goes in the record: the rule, list and limit hits that fired, then the model's features
ranked by how strongly they moved the score, each with the value it saw and a sentence an analyst
can read ("4 earlier tries with this card in the last 10 minutes"). Nothing here holds a card
number, an email address or an IP address: only the feature values screening already records.

Merchant operators get `merchant_summary`, which names broad areas ("Unusual activity on this
card") and never a rule, a threshold or a feature value, so the screening logic cannot be
learned from the dashboard. Customers see none of this.
"""
from __future__ import annotations

import math
from typing import Any

EXPLANATION_VERSION = "explain-1"
TOP_MODEL_FACTORS = 5
MIN_EFFECT = 0.01  # log-odds; smaller moves are noise and are not listed

# --- the model part ----------------------------------------------------------------------------


class TreeShapExplainer:
    """Exact path-dependent TreeSHAP values for a fitted HistGradientBoostingClassifier (binary).

    Each leaf of each tree contributes `leaf value x product over the path's features`, where a
    feature that is "known" (in the coalition) passes 1 if the attempt follows the path at every
    split on it and 0 otherwise, and an unknown feature passes the share of training samples that
    went that way (its cover ratio). That is a product game per leaf, whose Shapley values have a
    closed form: for path feature i, (o_i - z_i) x sum over k of k!(d-1-k)!/d! x [t^k] of the
    product over the other path features j of (z_j + o_j t). Summed over leaves and trees this is
    the same quantity as Lundberg's TreeSHAP (checked against the `shap` package in the tests'
    reference values), computed with numpy over all leaves at once.

    Everything that depends only on the model is prepared once at load. Per attempt: one pass over
    the path splits, then a few array operations per path length (at most the number of features).
    """

    def __init__(self, estimator: Any, feature_order: list[str]):
        import numpy as np

        predictors = estimator._predictors  # one list of trees per boosting iteration
        if any(len(per_iter) != 1 for per_iter in predictors):
            raise ValueError("only binary classifiers are supported")
        self.feature_order = list(feature_order)
        edge_f, edge_t, edge_left, edge_missing = [], [], [], []  # one row per tree edge
        pair_slot, pair_edge = [], []  # slot (leaf, path feature) -> the edges it must follow
        leaves: dict[int, list] = {}  # path length d -> [(value, [features], [z], [slot])]
        self.base = float(estimator._baseline_prediction.ravel()[0])
        self.n_slots = 0
        for per_iter in predictors:
            nodes = per_iter[0].nodes
            if bool(nodes["is_categorical"].any()):
                raise ValueError("categorical splits are not supported")
            # (node, {feature: (z, [edge ids])}) depth-first; the root covers every sample
            stack: list[tuple[int, dict]] = [(0, {})]
            while stack:
                i, path = stack.pop()
                node = nodes[i]
                if node["is_leaf"]:
                    feats = sorted(path)
                    value = float(node["value"])
                    slots = []
                    for f in feats:
                        slot = self.n_slots
                        self.n_slots += 1
                        slots.append(slot)
                        for edge in path[f][1]:
                            pair_slot.append(slot)
                            pair_edge.append(edge)
                    z = [path[f][0] for f in feats]
                    leaves.setdefault(len(feats), []).append((value, feats, z, slots))
                    self.base += value * math.prod(z)
                    continue
                f = int(node["feature_idx"])
                for child, is_left in ((int(node["left"]), True), (int(node["right"]), False)):
                    edge = len(edge_f)
                    edge_f.append(f)
                    edge_t.append(float(node["num_threshold"]))
                    edge_left.append(is_left)
                    edge_missing.append(bool(node["missing_go_to_left"]))
                    ratio = float(nodes[child]["count"]) / float(node["count"])
                    z, edges = path.get(f, (1.0, []))
                    stack.append((child, {**path, f: (z * ratio, [*edges, edge])}))
        self.edge_f = np.array(edge_f, dtype=np.intp)
        self.edge_t = np.array(edge_t)
        self.edge_left = np.array(edge_left)
        self.edge_missing = np.array(edge_missing)
        self.pair_slot = np.array(pair_slot, dtype=np.intp)
        self.pair_edge = np.array(pair_edge, dtype=np.intp)
        self.groups = []
        for d, rows in sorted(leaves.items()):
            if d == 0:
                continue
            weights = np.array([math.factorial(k) * math.factorial(d - 1 - k) / math.factorial(d)
                                for k in range(d)])
            self.groups.append((
                d,
                np.array([r[0] for r in rows]),
                np.array([r[1] for r in rows], dtype=np.intp),
                np.array([r[2] for r in rows]),
                np.array([r[3] for r in rows], dtype=np.intp),
                weights,
                ~np.eye(d, dtype=bool),
            ))

    def contributions(self, features: dict[str, float]) -> tuple[float, dict[str, float]]:
        import numpy as np

        row = np.array([float(features[name]) for name in self.feature_order])
        x = row[self.edge_f]
        went_left = np.where(np.isnan(x), self.edge_missing, x <= self.edge_t)
        missed = np.bincount(self.pair_slot, weights=(went_left != self.edge_left)[self.pair_edge],
                             minlength=self.n_slots)
        follows = (missed == 0).astype(float)
        phi = np.zeros(len(row))
        for d, value, feat, z, slot, weights, others in self.groups:
            o = follows[slot]  # n x d
            # coefficients of prod over j != i of (z_j + o_j t): n x d(i) x d+1
            coef = np.zeros((len(value), d, d + 1))
            coef[:, :, 0] = 1.0
            for j in range(d):
                zj, oj = z[:, j, None, None], o[:, j, None, None]
                grown = coef * zj
                grown[:, :, 1:] += coef[:, :, :-1] * oj
                coef = np.where(others[j][None, :, None], grown, coef)
            share = coef[:, :, :d] @ weights  # n x d
            np.add.at(phi, feat.ravel(), (value[:, None] * (o - z) * share).ravel())
        return self.base, dict(zip(self.feature_order, phi.tolist(), strict=True))


def sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z)) if z >= 0 else math.exp(z) / (1.0 + math.exp(z))


# --- words -------------------------------------------------------------------------------------


def money(cents: float) -> str:
    return f"S${cents / 100:,.2f}"


def _plural(n: float, one: str, many: str) -> str:
    n = int(n)
    return f"{n} {one if n == 1 else many}"


def _duration(seconds: float) -> str:
    if seconds < 90:
        return _plural(round(seconds), "second", "seconds")
    if seconds < 90 * 60:
        return _plural(round(seconds / 60), "minute", "minutes")
    if seconds < 36 * 3600:
        return _plural(round(seconds / 3600), "hour", "hours")
    return _plural(round(seconds / 86400), "day", "days")


def _tries(n: float, window: str, what: str = "with this card") -> str:
    if n == 0:
        return f"no earlier tries {what} in the last {window}"
    return f"{_plural(n, 'earlier try', 'earlier tries')} {what} in the last {window}"


# feature -> (short phrase for rules and lists, sentence for the value screening saw)
FEATURE_TEXT: dict[str, tuple[str, Any]] = {
    "amount_cents": ("Amount", lambda v: f"amount {money(v)}"),
    "card_attempts_10m": ("Earlier tries with this card in 10 minutes",
                          lambda v: _tries(v, "10 minutes")),
    "card_attempts_1h": ("Earlier tries with this card in 1 hour",
                         lambda v: _tries(v, "hour")),
    "card_attempts_24h": ("Earlier tries with this card in 24 hours",
                          lambda v: _tries(v, "24 hours")),
    "card_attempts_7d": ("Earlier tries with this card in 7 days",
                         lambda v: _tries(v, "7 days")),
    "card_attempts_30d": ("Earlier tries with this card in 30 days",
                          lambda v: "new card: not seen in the last 30 days" if v == 0
                          else _tries(v, "30 days")),
    "card_blocked_24h": ("Blocked tries with this card in 24 hours",
                         lambda v: f"{_plural(v, 'blocked try', 'blocked tries')} with this "
                                   f"card in the last 24 hours"),
    "card_distinct_emails_24h": ("Different emails used with this card in 24 hours",
                                 lambda v: f"{_plural(v, 'other email', 'other emails')} used "
                                           f"with this card in the last 24 hours"),
    "card_allowed_30d": ("Allowed payments with this card in 30 days",
                         lambda v: f"{_plural(v, 'allowed payment', 'allowed payments')} with "
                                   f"this card in the last 30 days"),
    "email_attempts_24h": ("Earlier tries with this email in 24 hours",
                           lambda v: _tries(v, "24 hours", "with this email")),
    "email_distinct_cards_24h": ("Different cards used with this email in 24 hours",
                                 lambda v: f"{_plural(v, 'other card', 'other cards')} used "
                                           f"with this email in the last 24 hours"),
    "ip_attempts_1h": ("Earlier tries from this IP address in 1 hour",
                       lambda v: _tries(v, "hour", "from this IP address")),
    "ip_distinct_cards_1h": ("Different cards from this IP address in 1 hour",
                             lambda v: f"{_plural(v, 'card', 'cards')} tried from this IP "
                                       f"address in the last hour"),
    "card_seconds_since_last": ("Seconds since this card's last try",
                                lambda v: "no earlier try with this card in 30 days" if v < 0
                                else f"last try with this card {_duration(v)} before"),
    "card_mean_amount_30d": ("This card's usual amount",
                             lambda v: "no usual amount yet for this card" if v == 0
                             else f"this card usually pays {money(v)}"),
    "amount_to_card_mean_30d": ("Amount compared with this card's usual",
                                lambda v: "no earlier amount to compare with" if v == 0
                                else f"amount {v:.1f}× this card's usual"),
    # fs-0.3 (v2 learning loop): the card's issuer range, its first 6 digits
    "bin_attempts_10m": ("Earlier tries with cards of this range in 10 minutes",
                         lambda v: f"{_plural(v, 'earlier try', 'earlier tries')} with cards "
                                   f"from this card's range in the last 10 minutes"),
    "bin_distinct_cards_1h": ("Different cards of this range in 1 hour",
                              lambda v: f"{_plural(v, 'other card', 'other cards')} from this "
                                        f"card's range tried in the last hour"),
    "bin_distinct_ips_1h": ("Different IP addresses using this range in 1 hour",
                            lambda v: f"{_plural(v, 'IP address', 'IP addresses')} used cards "
                                      f"from this card's range in the last hour"),
}

OPS = {"gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "eq": "="}
LIST_TEXT = {"card": "This card", "email": "This email address", "ip": "This IP address"}

# Broad areas a merchant may see. Never a rule, a threshold or a value.
MERCHANT_AREA = {
    "amount_cents": "Unusually large amount",
    "amount_to_card_mean_30d": "Amount unusual for this card",
    "card_mean_amount_30d": "Amount unusual for this card",
    "email_attempts_24h": "Unusual activity on this email address",
    "email_distinct_cards_24h": "Unusual activity on this email address",
    "ip_attempts_1h": "Unusual activity from this network",
    "ip_distinct_cards_1h": "Unusual activity from this network",
    "bin_attempts_10m": "Unusual activity on cards like this one",
    "bin_distinct_cards_1h": "Unusual activity on cards like this one",
    "bin_distinct_ips_1h": "Unusual activity on cards like this one",
}
CARD_AREA = "Unusual activity on this card"


def feature_sentence(name: str, value: float) -> str:
    text = FEATURE_TEXT.get(name)
    if text is None:
        return f"{name.replace('_', ' ')} {value:g}"
    return text[1](value)


def rule_sentence(feature: str, op: str, value: float) -> str:
    label = FEATURE_TEXT.get(feature, (feature.replace("_", " "),))[0]
    shown = money(value) if feature in ("amount_cents", "card_mean_amount_30d") else f"{value:g}"
    return f"{label} {OPS.get(op, op)} {shown}"


def _rule_factor(code: str, rules: dict, features: dict[str, float],
                 amount_cents: int, max_amount: int | None) -> dict:
    if code.startswith(("blocklisted_", "review_list_")):
        kind = code.rsplit("_", 1)[1]
        listed = "on the blocklist" if code.startswith("blocklisted_") else "on the review list"
        return {"source": "list", "code": code, "direction": "raises",
                "text": f"{LIST_TEXT.get(kind, kind)} is {listed}",
                "area": f"{LIST_TEXT.get(kind, kind)} was flagged before"}
    if code == "amount_over_limit":
        limit = f" ({money(max_amount)} limit)" if max_amount else ""
        return {"source": "limit", "code": code, "direction": "raises",
                "text": f"Amount {money(amount_cents)} is over the gateway's limit{limit}",
                "area": "Amount over the allowed limit"}
    spec = rules.get(code)
    if spec is None:  # the rule changed since; keep the code
        return {"source": "rule", "code": code, "direction": "raises",
                "text": f"Rule {code}", "area": "Matched a fraud rule"}
    feature, op, value = spec
    seen = float(amount_cents) if feature == "amount_cents" else features.get(feature)
    text = f"Rule {code}: {rule_sentence(feature, op, value)}"
    if seen is not None:
        text += f" (was {money(seen) if feature == 'amount_cents' else f'{seen:g}'})"
    return {"source": "rule", "code": code, "feature": feature, "direction": "raises",
            "text": text, "area": MERCHANT_AREA.get(feature, CARD_AREA)}


def explain(*, outcome: str, reasons: list[str], features: dict[str, float], amount_cents: int,
            score: float | None, contributions: tuple[float, dict[str, float]] | None,
            rules: dict[str, tuple[str, str, float]] | None = None,
            max_amount: int | None = None) -> dict:
    """The explanation stored with a decision.

    `rules` maps a rule code to (feature, op, value) as it was when the decision was made.
    `contributions` is TreeShapExplainer.contributions(...) or None (stub model).
    """
    rules = rules or {}
    factors = [_rule_factor(code, rules, features, amount_cents, max_amount)
               for code in reasons if code not in ("model_score_high", "model_score_medium",
                                                   "screening_unavailable")]
    model_factors: list[dict] = []
    base_score = None
    if contributions is not None:
        base, effects = contributions
        base_score = round(sigmoid(base), 4)
        ranked = sorted(effects.items(), key=lambda kv: (-abs(kv[1]), kv[0]))
        for name, effect in ranked[:TOP_MODEL_FACTORS]:
            if abs(effect) < MIN_EFFECT:
                continue
            value = features.get(name, 0.0)
            model_factors.append({
                "source": "model", "feature": name, "value": value,
                "effect": round(effect, 4), "direction": "raises" if effect > 0 else "lowers",
                "text": feature_sentence(name, value),
                "area": MERCHANT_AREA.get(name, CARD_AREA),
            })
    raising = [f for f in model_factors if f["direction"] == "raises"]
    summary_parts = [f["text"] for f in factors] + [f["text"] for f in raising[:3]]
    if outcome == "allow" and not factors:
        headline = "Allowed: no rule fired and the model score is below the step-up line"
    elif not summary_parts:
        headline = "No single factor stood out"
    else:
        headline = " + ".join(_short(p) for p in summary_parts[:4])
    return {
        "version": EXPLANATION_VERSION,
        "method": "tree-shap" if contributions is not None else "rules-only",
        "headline": headline,
        "score": score,
        "base_score": base_score,
        "factors": factors + model_factors,
    }


def _short(text: str) -> str:
    """'Rule high_amount: Amount > S$500.00 (was S$812.00)' -> 'Amount > S$500.00'."""
    if text.startswith("Rule ") and ": " in text:
        text = text.split(": ", 1)[1].split(" (was", 1)[0]
    return text[0].lower() + text[1:] if text[:2] != "IP" else text


def merchant_summary(explanation: dict | None) -> list[str]:
    """Broad areas that raised risk, for merchant operators. No rule, threshold or value."""
    if not explanation:
        return []
    areas: list[str] = []
    for factor in explanation.get("factors", []):
        if factor.get("direction") == "raises" and factor.get("area") not in areas:
            areas.append(factor["area"])
    return areas[:3]
