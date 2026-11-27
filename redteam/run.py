"""SecurePay red team: attack a test gateway from a laptop and watch it defend (v2).

    export SECUREPAY_TEST_KEY=sp_test_...          # the demo shop's TEST key; never a live one
    python redteam/run.py --gateway https://gateway.staging.securepaysg.me --scenario card_testing
    python redteam/run.py --gateway ... --scenario bin_attack --count 120 --legit 20

Every attempt is a real test payment through the real gateway: created on the merchant API,
then card details and "pay" on the hosted checkout, screened by the rules, lists, model and
policy like any shopper's. The gateway knows which run each payment belongs to and whether it
was an attack (the Red-Team-* headers, docs/v2/RED-TEAM.md), but screening never sees that.
Open the dashboard's Red team page as a fraud analyst or administrator to watch it live.

Only httpx is needed (pip install httpx). The gateway refuses runs unless REDTEAM_ENABLED is on
(staging and local) and the key is a test key.
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scenarios import SCENARIOS, Attempt, plan

# The merchant API allows 100 requests a minute per key; one payment is one API call.
MAX_RATE = 1.5


def error_code(reply: httpx.Response) -> str:
    try:
        return reply.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return ""


def session_id(payment: dict) -> str:
    return payment["checkout_url"].rstrip("/").rsplit("/", 1)[-1]


class Runner:
    def __init__(self, gateway: str, key: str, verify: bool | str = True):
        self.gateway = gateway.rstrip("/")
        # The gateway takes HTTPS return URLs only; nobody follows it, so any HTTPS page will do.
        self.return_url = "https://" + urlsplit(self.gateway).netloc + "/redteam/return"
        self.api = httpx.Client(base_url=self.gateway, verify=verify, timeout=30,
                                headers={"Authorization": f"Bearer {key}"})
        self.browser = httpx.Client(base_url=self.gateway, verify=verify, timeout=30)
        self.run: dict = {}
        self.results: Counter = Counter()
        self.lock = threading.Lock()

    def start(self, scenario: str, note: str) -> dict:
        r = self.api.post("/v1/red_team/runs", json={"scenario": scenario, "note": note})
        if r.status_code != 201:
            raise SystemExit(f"The gateway refused the run: {r.status_code} {r.text[:300]}")
        self.run = r.json()
        return self.run

    def end(self) -> None:
        if self.run:
            self.api.post(f"/v1/red_team/runs/{self.run['id']}/end", json={})

    def attempt(self, a: Attempt) -> str:
        headers = {"Idempotency-Key": str(uuid.uuid4()), "Red-Team-Run": self.run["id"],
                   "Red-Team-Kind": a.kind, "Red-Team-Scenario": a.scenario,
                   "Red-Team-Bot": a.bot}
        try:
            created = self.api.post("/v1/payments", headers=headers, json={
                "amount": a.amount, "currency": "SGD",
                "order_reference": f"RT-{uuid.uuid4().hex[:10]}",
                "return_url": self.return_url, "customer_email": a.email})
            if created.status_code != 201:
                return f"create {created.status_code} {error_code(created)}"
            base = f"/checkout/{session_id(created.json())}"
            bot = {"Red-Team-Token": self.run["token"], "Red-Team-IP": a.ip}
            card = self.browser.post(f"{base}/card", headers=bot, json={
                "number": a.card, "expiry_month": 12, "expiry_year": 2030, "cvc": "123",
                "privacy_consent": True})
            if card.status_code != 200:
                return f"card {card.status_code} {error_code(card)}"
            paid = self.browser.post(f"{base}/submit", headers=bot, json={})
            if paid.status_code == 429:
                return "rate limited"
            return paid.json().get("status", f"submit {paid.status_code}")
        except httpx.HTTPError as exc:
            return f"error {type(exc).__name__}"

    def go(self, attempts: list[Attempt], rate: float, workers: int) -> None:
        gap = 1 / rate
        started = time.monotonic()

        def one(i_a):
            i, a = i_a
            # Keep to the pace: attempt i starts no earlier than i * gap.
            wait = started + i * gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            outcome = self.attempt(a)
            with self.lock:
                self.results[(a.kind, a.scenario, outcome)] += 1
                done = sum(self.results.values())
                if done % 20 == 0 or done == len(attempts):
                    print(f"  {done}/{len(attempts)} sent")

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(one, enumerate(attempts)))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gateway", default=os.environ.get("GATEWAY_URL",
                                                            "https://gateway.localhost"))
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    parser.add_argument("--bots", type=int, default=20, help="card_testing: number of bots")
    parser.add_argument("--tries", type=int, default=8, help="card_testing: tries per card")
    parser.add_argument("--count", type=int, default=120, help="bin_attack: cards to try")
    parser.add_argument("--legit", type=int, default=0, help="background shoppers to mix in")
    parser.add_argument("--rate", type=float, default=1.2, help="payments a second (max 1.5)")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--note", default="")
    parser.add_argument("--ca", default=os.environ.get("CADDY_ROOT_CA"),
                        help="CA bundle for a local gateway with Caddy's own certificate")
    args = parser.parse_args(argv)

    key = os.environ.get("SECUREPAY_TEST_KEY", "")
    if not key.startswith("sp_test_"):
        print("Set SECUREPAY_TEST_KEY to the demo shop's TEST key (sp_test_...).")
        return 2
    rate = max(0.1, min(args.rate, MAX_RATE))
    rng = random.Random(args.seed)  # nosec B311 - attack traffic, not security
    tls = args.ca or True  # a CA bundle for a local gateway, else the system store
    runner = Runner(args.gateway, key, tls)
    run = runner.start(args.scenario, args.note)
    attempts = plan(args.scenario, rng, run["id"], bots=max(1, min(args.bots, 200)),
                    tries=max(1, min(args.tries, 20)), count=max(1, min(args.count, 1000)),
                    legit_count=max(0, min(args.legit, 500)))
    print(f"Run {run['id']} ({args.scenario}): {len(attempts)} payments at {rate}/s, "
          f"about {len(attempts) / rate / 60:.1f} minutes. Watch it on the dashboard's "
          "Red team page.")
    try:
        runner.go(attempts, rate, workers=8)
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        runner.end()
    print("\nKind    Scenario       Outcome            Count")
    for (kind, scenario, outcome), n in sorted(runner.results.items()):
        print(f"  {kind:6s} {scenario:14s} {outcome:18s} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
