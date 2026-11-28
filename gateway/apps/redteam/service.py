"""Red-team runs: start and end one, label its payments, and score it live (docs/v2/RED-TEAM.md).

Safety rules, all checked here on the server:

* Only when `REDTEAM_ENABLED` is on (staging and local, never production), and only with a
  **test** API key. Red-team payments are ordinary test payments: no real card, no money.
* A run lasts at most REDTEAM_RUN_MINUTES and makes at most REDTEAM_MAX_PAYMENTS payments.
* The runner may say which address each bot "comes from" (Red-Team-IP), so a swarm of bots
  on one laptop looks like a swarm to screening and to the per-IP rate limits. That header is
  believed only with the run's bearer token (Red-Team-Token), only while the run is live,
  and on checkout only for a payment of that run. Any other request uses its real address.
* What a payment really was (attack or background shopper) is stored in RedTeamPayment.
  Screening never reads it: it is the ground truth for the live score and the learning loop.
"""
import hashlib
import hmac
import ipaddress
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

from apps.common.errors import ApiError, invalid, not_found
from apps.common.timefmt import rfc3339
from apps.redteam.models import RedTeamPayment, RedTeamRun

RUN_HEADER, TOKEN_HEADER, IP_HEADER = "Red-Team-Run", "Red-Team-Token", "Red-Team-IP"
KIND_HEADER, SCENARIO_HEADER, BOT_HEADER = "Red-Team-Kind", "Red-Team-Scenario", "Red-Team-Bot"
NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
BOT = re.compile(r"^[A-Za-z0-9_.:-]{1,40}$")
Kind = RedTeamPayment.Kind


def _rules():
    return settings.GATEWAY_RULES


def enabled() -> bool:
    return bool(getattr(settings, "REDTEAM_ENABLED", False))


def _hash(token: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f"redteam:{token}".encode(),
                    hashlib.sha256).hexdigest()


def _live(run: RedTeamRun, now=None) -> bool:
    now = now or timezone.now()
    return run.ended_at is None and now < run.expires_at


def _off() -> ApiError:
    return ApiError(403, "red_team_disabled", "The red team is not enabled on this gateway.")


# --- runs (merchant API, test keys only) ---------------------------------------------------

def start_run(merchant, api_key, body) -> dict:
    if not enabled():
        raise _off()
    if api_key.livemode:
        raise ApiError(403, "red_team_test_mode_only", "The red team runs with a test key "
                       "only (sp_test_...).")
    if not isinstance(body, dict) or set(body) - {"scenario", "note"}:
        raise invalid("invalid_field", "Send 'scenario' and an optional 'note' only.")
    scenario = body.get("scenario")
    if not isinstance(scenario, str) or not NAME.match(scenario):
        raise invalid("invalid_field", "'scenario' must be a short lower_case name.",
                      param="scenario")
    note = body.get("note") or ""
    if not isinstance(note, str) or len(note) > 200:
        raise invalid("invalid_field", "'note' must be text of at most 200 characters.",
                      param="note")
    token = secrets.token_urlsafe(32)
    run = RedTeamRun.objects.create(
        merchant=merchant, api_key=api_key, scenario=scenario, note=note.strip(),
        token_hash=_hash(token),
        expires_at=timezone.now() + timedelta(minutes=_rules()["REDTEAM_RUN_MINUTES"]))
    from apps.audit.models import AuditEvent
    from apps.audit.models import record as audit

    audit("red_team.run_started", actor_type=AuditEvent.ActorType.MERCHANT_API,
          actor_id=api_key.public_id, merchant_id=merchant.public_id, target_type="red_team_run",
          target_id=run.public_id, details={"scenario": scenario})
    return {**run_to_dict(run), "token": token}


def end_run(merchant, run_id: str) -> dict:
    run = RedTeamRun.objects.filter(public_id=run_id, merchant=merchant).first()
    if run is None:
        raise not_found()
    if run.ended_at is None:
        run.ended_at = timezone.now()
        run.save(update_fields=["ended_at"])
    return run_to_dict(run)


def run_to_dict(run: RedTeamRun) -> dict:
    return {"id": run.public_id, "object": "red_team_run", "scenario": run.scenario,
            "note": run.note or None, "started_at": rfc3339(run.started_at),
            "expires_at": rfc3339(run.expires_at), "ended_at": rfc3339(run.ended_at),
            "live": _live(run)}


# --- labelling payments (merchant API, inside the payment's transaction) -------------------

def label_from_headers(request, merchant, api_key):
    """Read the Red-Team-* headers of POST /v1/payments. None when there are none."""
    run_id = request.headers.get(RUN_HEADER)
    if not run_id:
        return None
    if not enabled():
        raise _off()
    run = RedTeamRun.objects.filter(public_id=run_id[:32], merchant=merchant,
                                    api_key__livemode=False).first()
    if run is None or not _live(run) or api_key.livemode:
        raise invalid("red_team_run_invalid", "That red-team run is unknown or has ended.",
                      param=RUN_HEADER)
    kind = request.headers.get(KIND_HEADER, Kind.ATTACK)
    if kind not in Kind.values:
        raise invalid("invalid_field", "Red-Team-Kind must be attack or legit.",
                      param=KIND_HEADER)
    scenario = request.headers.get(SCENARIO_HEADER) or run.scenario
    bot = request.headers.get(BOT_HEADER, "")
    if not NAME.match(scenario) or (bot and not BOT.match(bot)):
        raise invalid("invalid_field", "Red-Team-Scenario or Red-Team-Bot is not valid.")
    if run.payments.count() >= _rules()["REDTEAM_MAX_PAYMENTS"]:
        raise ApiError(429, "red_team_run_full", "This red-team run has made the most payments "
                       "a run may make. Start a new run.")
    return run, kind, scenario, bot


def record(label, payment) -> None:
    if label is None:
        return
    run, kind, scenario, bot = label
    RedTeamPayment.objects.create(run=run, payment=payment, kind=kind, scenario=scenario,
                                  bot=bot)


# --- where a bot "comes from" ---------------------------------------------------------------

def _claimed_ip(request) -> str | None:
    raw = request.headers.get(IP_HEADER) if hasattr(request, "headers") else None
    if not raw:
        return None
    try:
        return str(ipaddress.ip_address(raw.strip()))
    except ValueError:
        return None


def _token_run(request) -> RedTeamRun | None:
    token = request.headers.get(TOKEN_HEADER) if hasattr(request, "headers") else None
    if not token or not enabled() or len(token) > 100:
        return None
    run = RedTeamRun.objects.filter(token_hash=_hash(token)).first()
    return run if run is not None and _live(run) else None


def bot_ip(request, payment=None) -> str | None:
    """The bot's address, when this request carries a live run's token (and, with a payment,
    the payment belongs to that run). Otherwise None: use the real address."""
    ip = _claimed_ip(request)
    if ip is None:
        return None
    run = _token_run(request)
    if run is None:
        return None
    if payment is not None and not RedTeamPayment.objects.filter(
            payment=payment, run=run).exists():
        return None
    return ip


# --- the live score (staff API) -------------------------------------------------------------

BUCKET_SECONDS = 10


def live(run: RedTeamRun) -> dict:
    """Per 10 seconds since the run started: attacks and background payments, and what
    screening did with them. 'Stopped' = blocked or sent to verification."""
    rows = (RedTeamPayment.objects.filter(run=run)
            .values("kind", "scenario", "created_at", "payment__screening__outcome",
                    "payment__status"))
    start = run.started_at
    end = run.ended_at or min(timezone.now(), run.expires_at)
    count = max(1, int((end - start).total_seconds() // BUCKET_SECONDS) + 1)
    buckets = [{"at": rfc3339(start + timedelta(seconds=i * BUCKET_SECONDS)), "attacks": 0,
                "attacks_blocked": 0, "attacks_verify": 0, "attacks_allowed": 0, "legit": 0,
                "legit_stopped": 0} for i in range(min(count, 720))]
    scenarios: dict[str, dict] = {}
    for row in rows:
        i = int((row["created_at"] - start).total_seconds() // BUCKET_SECONDS)
        bucket = buckets[min(max(i, 0), len(buckets) - 1)]
        outcome = row["payment__screening__outcome"]  # None: not submitted yet
        if row["kind"] == Kind.ATTACK:
            s = scenarios.setdefault(row["scenario"], {"scenario": row["scenario"],
                                                       "attacks": 0, "blocked": 0,
                                                       "verify": 0, "allowed": 0,
                                                       "pending": 0})
            s["attacks"] += 1
            bucket["attacks"] += 1
            if outcome in ("block", "not_screened"):
                bucket["attacks_blocked"] += 1
                s["blocked"] += 1
            elif outcome == "verify":
                bucket["attacks_verify"] += 1
                s["verify"] += 1
            elif outcome == "allow":
                bucket["attacks_allowed"] += 1
                s["allowed"] += 1
            else:
                s["pending"] += 1
        else:
            bucket["legit"] += 1
            if outcome in ("block", "not_screened", "verify"):
                bucket["legit_stopped"] += 1
    totals = RedTeamPayment.objects.filter(run=run).aggregate(
        attacks=Count("id", filter=Q(kind=Kind.ATTACK)),
        attacks_screened=Count("id", filter=Q(kind=Kind.ATTACK,
                                              payment__screening__isnull=False)),
        stopped=Count("id", filter=Q(kind=Kind.ATTACK, payment__screening__outcome__in=(
            "block", "not_screened", "verify"))),
        legit=Count("id", filter=Q(kind=Kind.LEGIT, payment__screening__isnull=False)),
        legit_stopped=Count("id", filter=Q(kind=Kind.LEGIT, payment__screening__outcome__in=(
            "block", "not_screened", "verify"))))
    screened = totals["attacks_screened"]
    return {
        "run": run_to_dict(run),
        "bucket_seconds": BUCKET_SECONDS,
        "buckets": buckets,
        "scenarios": sorted(scenarios.values(), key=lambda s: s["scenario"]),
        "totals": {
            **totals,
            "caught_rate": round(totals["stopped"] / screened, 4) if screened else None,
            "false_positive_rate": (round(totals["legit_stopped"] / totals["legit"], 4)
                                    if totals["legit"] else None),
        },
    }


def runs_list(limit: int = 20) -> dict:
    runs = RedTeamRun.objects.annotate(
        attack_count=Count("payments", filter=Q(payments__kind=Kind.ATTACK)))[:limit]
    return {"object": "list", "data": [{**run_to_dict(r), "attacks": r.attack_count}
                                       for r in runs]}
