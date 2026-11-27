# Red team (v2, Sprint 2)

The red team attacks our own gateway, from a laptop, with test cards, and the dashboard shows
live how screening defends. Every attack is a real test payment through the real gateway:
created on the merchant API, then card details and **Pay** on the hosted checkout, screened by
the rules, lists, model and policy like any shopper's payment. No real card is used and no money
moves.

The gateway also records what each payment really was (an attack, or a background shopper).
Screening never reads that label. It is the ground truth for the live score now, and for the
learning loop (the next slice), which retrains a challenger model on it.

## Safety rules

All of these are checked by the gateway, not by the runner:

| Rule | Value |
| --- | --- |
| Where | Only where `REDTEAM_ENABLED` is on: local (`deploy/compose.yaml`) and staging (`deploy/compose.staging.yaml`). Production leaves it off, so every red-team call answers `403 red_team_disabled` |
| Which key | A **test** key only (`sp_test_...`). A live key gets `403 red_team_test_mode_only` |
| How long | A run lasts at most `REDTEAM_RUN_MINUTES` (60) and then stops being live |
| How many | At most `REDTEAM_MAX_PAYMENTS` (3000) payments per run, then `429 red_team_run_full` |
| Bot addresses | See [Where a bot comes from](#where-a-bot-comes-from) |
| Token | 32 random bytes, given once when the run starts. The gateway keeps only an HMAC of it |

## Running it from a laptop

The runner is in `redteam/` at the top of the repository and needs only Python 3.10+ and
`httpx` (`pip install -r redteam/requirements.txt`).

```sh
export SECUREPAY_TEST_KEY=sp_test_...   # the demo shop's TEST key, from the dashboard's API keys page
python redteam/run.py --gateway https://gateway.staging.securepaysg.me --scenario card_testing
python redteam/run.py --gateway https://gateway.staging.securepaysg.me --scenario bin_attack --legit 20
```

Then open **Red team** on the dashboard as a fraud analyst or administrator. The latest run is
shown and updates every 3 seconds.

| Option | Default | Meaning |
| --- | --- | --- |
| `--scenario` | (required) | `card_testing` or `bin_attack` |
| `--bots` | 20 | `card_testing`: how many bots, each with its own address and two cards |
| `--tries` | 8 | `card_testing`: how many times each bot tries each card |
| `--count` | 120 | `bin_attack`: how many cards to try |
| `--legit` | 0 | Background shoppers mixed in, so a false positive shows on the live view |
| `--rate` | 1.2 | Payments a second, at most 1.5 (the merchant API allows 100 a minute per key) |
| `--seed` | random | Repeat the same run |
| `--note` | | Shown next to the run on the dashboard |
| `--ca` | `CADDY_ROOT_CA` | CA bundle for a local gateway that uses Caddy's own certificate |

The default card-testing run is 320 payments and takes about four and a half minutes. Each card
is approved for its first five tries and blocked from the sixth, so about the last third of the
run is blocked: the block rate climbs after the attack rate. A bigger swarm
(`--bots 200`) works but takes longer, because of the merchant API's rate limit.

## Scenarios

- **`card_testing`:** a swarm of bots checking stolen cards with small amounts (S$ 0.50 to
  S$ 3.00). Each bot retries each card. The card-attempt burst rule (`card_attempts_10m` of 5
  or more) and the model's velocity features start blocking within seconds, so the attack rate
  climbs and the block rate follows it.
- **`bin_attack`:** a quieter attack. Card numbers are enumerated from one BIN (`42424200`),
  each tried once, from a fresh address and email, with ordinary amounts. Today's per-card,
  per-email and per-IP features all look clean, so much of it slips through. This is the
  pattern the learning loop has to learn.
- **Background shoppers (`--legit`):** ordinary payments (S$ 12.90 to S$ 199), each from its
  own address, card and email.

Cards come from the simulator's demo ranges (`4242 4200 ...` and `5555 5500 ...`,
SIMULATOR-CONTRACT.md section 5), so they are approved when screening lets them through.

## Where a bot comes from

The runner says which address each bot "comes from" (`Red-Team-IP`, from `100.64.0.0/10`), so
a swarm of bots on one laptop looks like a swarm to screening and to the per-IP rate limits.
The gateway believes that header only when:

- the request also carries the run's token (`Red-Team-Token`);
- the run is live (not ended, not expired) and the red team is enabled; and
- on checkout, the payment belongs to that run.

Any other request uses its real address. Each bot gets its own anonymous rate limit
(`red-team:<address>`), as a real swarm would.

## API

### Merchant API (test key, signed replies)

| Call | What it does |
| --- | --- |
| `POST /v1/red_team/runs` | Takes `{scenario, note?}` and returns 201 with the run and its `token` (shown once) |
| `POST /v1/red_team/runs/{id}/end` | Ends the run (no more payments, the token stops working) |

Neither takes an `Idempotency-Key`: a stored reply would keep the token.

A payment joins a run with these headers on `POST /v1/payments`:

| Header | Meaning |
| --- | --- |
| `Red-Team-Run` | The run's ID (`rt_...`). Unknown, ended or another merchant's run: `400 red_team_run_invalid` |
| `Red-Team-Kind` | `attack` (default) or `legit` |
| `Red-Team-Scenario` | Optional; by default the run's scenario |
| `Red-Team-Bot` | Optional bot name, for the record |

The payment the merchant sees is an ordinary payment. Nothing in it says red team.

### Staff API (fraud analysts and administrators)

| Call | What it does |
| --- | --- |
| `GET /internal/red-team/runs` | The latest 20 runs with their attack counts, and `enabled` |
| `GET /internal/red-team/runs/{id}` | The live score: 10-second buckets, per-pattern counts and totals |

The live score counts, per 10 seconds, the attacks blocked, sent to a code (Verify) or
allowed, and the background shoppers and how many of them were stopped. **Stopped** means
blocked or sent to a code. The totals give:

- **caught rate** = attacks stopped ÷ attacks screened;
- **false-positive rate** = shoppers stopped ÷ shoppers screened.

An attack that was created but never paid counts as **not paid yet**.

## Tried locally

On 28 Nov 2026 a real gateway and simulator were run locally, with the runner pointed at them:

| Run | Attacks | Stopped | Shoppers stopped |
| --- | --- | --- | --- |
| `card_testing --bots 3 --tries 6 --legit 3` | 36 | 6 (each card blocked on its sixth try) | 0 of 3 |
| `bin_attack --count 30 --legit 3` | 30 | 0 (all slipped through) | 0 of 3 |

That was with the rules and the stub model (score 0, `RISK_MODEL_VERSION` empty). On staging the live model also scores
each payment.

## Code

| Where | What |
| --- | --- |
| `gateway/apps/redteam` | Runs, labels (`RedTeamPayment`), bot addresses and the live score |
| `gateway/apps/internal/views/redteam.py` | The staff API |
| `redteam/` | The laptop runner and its scenarios |
| `frontend/dashboard/src/routes/RedTeam.tsx` | The Red team page |

The tests are `gateway/apps/redteam/tests.py`, `redteam/tests/test_scenarios.py` and
`frontend/dashboard/src/RedTeam.test.tsx`.
