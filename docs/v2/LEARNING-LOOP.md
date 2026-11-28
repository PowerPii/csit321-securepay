# Learning loop (v2, Sprint 2)

The red team ([RED-TEAM.md](RED-TEAM.md)) finds attacks that screening misses. The learning loop
turns what it found into a better model. The steps are:

1. **Label.** Every red-team payment is labelled as an attack or a background shopper.
2. **Train a challenger.** A new model is trained on the labelled screening history and scored
   on a holdout.
3. **Shadow.** The challenger scores every attempt beside the model that decides. Its verdict is
   recorded, but it never decides.
4. **Compare.** The dashboard shows both models side by side, on the holdout and on live
   red-team runs.
5. **Promote.** An administrator promotes the challenger, and it decides from then on. A rollback
   brings the previous model back.

Nothing in the loop changes a decision until a person promotes a model.

## The gap it closes: BIN attacks

On staging on 28 Nov 2026, the live model stopped **84%** of a card-testing swarm, but **0 of
120** attempts in a BIN attack. A BIN attack tries many cards from one issuer range, each
only once, and from a fresh address and email each time. Every per-card, per-email and per-IP
count therefore stays at zero.

Feature schema **fs-0.3** adds three counts per **card range**, which is the card's first 6
digits (its BIN):

| Feature | Meaning |
| --- | --- |
| `bin_attempts_10m` | Earlier attempts with cards from this range in 10 minutes |
| `bin_distinct_cards_1h` | Different cards from this range in 1 hour |
| `bin_distinct_ips_1h` | Different IP addresses using this range in 1 hour |

How the range is stored:

- The vault gives screening the first 6 digits when the card is added. They are kept on the
  payment (`card_bin`) and on the decision record.
- The first 6 digits identify the bank, not the cardholder. PCI DSS allows them to be shown and
  stored.
- `research/features/gateway_schema.py` has the same three features, and the parity test checks
  that both sides compute the same values.

The configured model (`hgb-fs02-2026-11-13`) was trained on fs-0.2 and still loads, because
fs-0.3 only adds features. The new counts are also offered in the fraud rule editor.

## Labels

A challenger learns only from what the gateway knows for certain:

| Attempt | Label |
| --- | --- |
| Red-team payment marked `attack` | 1 (fraud) |
| Red-team payment marked `legit` (background shopper) | 0 |
| Any other attempt that screening **allowed** | 0 (genuine traffic) |
| Any other attempt that was stopped, or not screened | left out: we don't know what it was |

Rows come from the decision records (`ScreeningEvent.features`), so each feature has the value
it had at that moment. Only attempts screened with the current schema (fs-0.3) are used, and at
most the latest 50,000.

Training needs at least **20 attacks and 20 genuine attempts**. Below that it answers
`409 not_enough_labels`.

## Training

`apps/risk/learning.py` trains a challenger as follows:

- **Model:** scikit-learn `HistGradientBoostingClassifier`, the same kind as the configured
  model. It uses balanced class weights and depth 4.
- **Monotonic counts:** more earlier activity (any `_10m`, `_1h` or `_24h` count) can only raise
  the score, never lower it. Without this rule, a small training set learns that "few tries in
  this range" means attack, because the first cards of every BIN attack have low counts, and it
  then stops ordinary shoppers.
- **Holdout:** a stratified quarter of the rows is held out first.
  - The model that decides now is scored by what it actually decided.
  - The challenger is scored by what it would have decided with the same rule hits. These are
    read back from the decision record, because `model_…` reasons mark a decision the model
    made.
  - The result is shown per attack pattern.
- **Final fit:** the challenger is then fitted again on all rows.
- **Thresholds:** policy-0.1, which means a code from 0.5 and a block from 0.9.
- **Storage:** the challenger is stored in the database (`RiskModel`) with a manifest. Every
  gateway process sees the same model, and it is loaded only after the SHA-256, scikit-learn
  version and feature checks that a configured artefact passes.
- **One challenger at a time:** training again retires the previous challenger.

You can start training in two ways:

- From the dashboard: **Fraud models → Train a challenger**, as an analyst or administrator.
- From the command line: `python manage.py train_challenger`.

## Shadow, promotion and rollback

**Shadow scoring**

- While a challenger exists, screening also scores the attempt with it, applies its thresholds
  to the same rule hits, and records the result on the decision record: `shadow_model_version`,
  `shadow_score` and `shadow_outcome`.
- A challenger that fails is ignored, and the decision stands.
- The Red team page shows what the challenger would have done in each run.

**Promotion**

- Promoting a model is for administrators only, and they must enter the current authenticator
  code, as for key rotation.
- The challenger becomes the **champion** and decides from then on.
- Other gateway processes notice the change within 5 seconds.

**Rollback**

- A rollback retires the champion and brings back the model it replaced. If no earlier model
  was promoted, the configured artefact decides again.

**Audit log**

- Every step is written to the audit log: `risk_model.trained`, `risk_model.promoted` and
  `risk_model.rolled_back`.

| Call | Who | What it does |
| --- | --- | --- |
| `GET /internal/risk-models` | Analyst, administrator | The model deciding now, the challenger with its holdout and shadow counts, recent models, labelled data available |
| `POST /internal/risk-models/train` | Analyst, administrator | Trains a new challenger (201) |
| `POST /internal/risk-models/promote` | Administrator | Takes `{version, code}` and makes the challenger the champion |
| `POST /internal/risk-models/rollback` | Administrator | Takes `{code}` and brings back the previous model |

## Shopper banks in the simulator

For the loop to work, background shoppers must come from many card ranges, as real shoppers
do. When every demo shopper shared one range, the challenger blocked them all as a "BIN
attack".

The simulator therefore approves **10,000 synthetic shopper banks**, `51BB BB00 xxxx xxxx`
(SIMULATOR-CONTRACT.md section 5). The red team's background shoppers draw their cards from
these. Attacks keep their single range, `4242 4200`.

## The demo

1. Run `bin_attack` with `--legit 30`. It slips through, and the Red team page shows 0% caught.
2. Open **Fraud models** and choose **Train a challenger**. The holdout shows the challenger
   catching the attacks that the current model missed.
3. Run `bin_attack` again. Nothing changes for shoppers, but the Red team page says the
   challenger *would have* stopped the attack.
4. As an administrator, choose **Promote to decide** and enter the code.
5. Run `bin_attack` again. It is blocked.

## Tried locally

On 28 Nov 2026 I ran the full story against a real local gateway and simulator. Only the rules
and the stub model were deciding at the start.

| Step | Attacks stopped | Shoppers stopped |
| --- | --- | --- |
| 1. `bin_attack --count 60 --legit 30` | 0 of 60 | 0 of 30 |
| 2. Train: holdout, current model vs challenger | 0 of 15 vs 15 of 15 | 0 of 8 vs 0 of 8 |
| 3. `bin_attack` again, challenger in the shadow | 0 of 60 (shadow: 60 of 60) | 0 of 30 (shadow: 0 of 30) |
| 4. Promote | | |
| 5. `bin_attack` again | **60 of 60** | 1 of 30 |
| 6. `card_testing --bots 3 --tries 8 --legit 20` | 47 of 48 | 0 of 20 |

## Limits

- **Small data.** The challenger learns from a few hundred demo attempts, not from years of real
  traffic.
  - Its idea of "many cards from one range" is set by demo volumes. Two or three shoppers from
    the same shopper bank within an hour can look risky to it, which is the 1 of 30 in step 5.
  - On real traffic, where a popular bank's range sees many genuine cards an hour, it would need
    each range's normal volume as a feature first.
  - This is why the challenger must pass a holdout and a shadow period before anyone promotes
    it.
- **No automatic promotion.** A person always decides, after looking at the comparison.
- **Labels only from the red team and allowed traffic.** Chargebacks and analyst decisions are
  not labels yet.
