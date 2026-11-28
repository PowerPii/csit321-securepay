# SecurePay processing simulator contract (M2 draft)

Status: **v1 draft for Muhammad Adeeb**, 13 November 2026. The URS lists "API, webhook and simulator contracts" as an open item for Week 9 (M2), owned by Muhammad Adeeb, including "the simulator's handling of an operation it reported as not received". `API-CONTRACT.md` covers the merchant side; this file covers the link between the gateway and the processing simulator. Proposals S1 to S4 (section 10) were confirmed by Fifa on 13 November 2026; everything else follows the URS, the architecture recommendation (own simulator, no Stripe test mode) or `API-CONTRACT.md`.

| File | What it holds |
| --- | --- |
| `contracts/SIMULATOR-CONTRACT.md` | This file: the rules a schema cannot express |
| `contracts/openapi/simulator.yaml` | OpenAPI 3.1 for `/sim/*`, the fault controls and the simulator notification |
| `contracts/schemas/simulator-operation.schema.json` | The operation record, returned by every call (JSON Schema 2020-12) |
| `contracts/examples/simulator/` | Example replies, checked by `contracts/check.py` in CI |

## 1. Roles

The simulator stands in for the card network and issuer. It **owns the authoritative fictional processing record** (URS Section 3.4): what was authorised, captured, cancelled and refunded. The gateway owns payments, refunds, screening and merchant notifications. A timeout on the gateway side proves nothing; only a simulator record does (URS Section 2.4.1).

The two services keep **separate state machines on purpose** (architecture section 10), so the simulator checks capture, cancel and refund rules itself even though the gateway checks them first. A simulator decline on a rule the gateway already checked means the two records disagree, and reconciliation reports it.

## 2. Conventions and security

- **Transport.** HTTPS through Caddy on the internal `simulator` Docker network (`https://simulator.localhost`). Not reachable from the edge network. TLS 1.2 or higher (NFR-ENC-01).
- **Format.** UTF-8 JSON, `snake_case`, amounts in integer cents, currency `SGD` only, timestamps RFC 3339 UTC with `Z`, as in `API-CONTRACT.md` section 1.
- **Identifiers.** Prefix plus 24 base62 characters: `op_` operation (made by the gateway), `sim_` simulator record, `ntf_` notification, `flt_` fault rule, `req_` request. The gateway also sends its own `mer_`, `pay_` and `rfd_` IDs so both sides' records can be compared.
- **Gateway key.** Every request carries `Authorization: Bearer simk_<40 base62>` from the Docker secret `simulator_api_key`. The simulator stores only a SHA-256 digest. Wrong or missing key: `401 api_key_invalid`, unsigned.
- **Signed replies.** Every `200` and `409` reply and every notification carries `Simulator-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256>` over `"<t>." + raw body`, with the secret `simulator_signing_secret` (`simsig_...`). It is the same scheme as the merchant signature in `API-CONTRACT.md` section 2. The gateway rejects a bad signature or a timestamp more than 300 seconds off, and stores each signed reply it applies as **reconciliation evidence** (FR-TXN-13, architecture section 4: corrections require simulator evidence).
- **Card data.** Only `/sim/authorise` receives a card number and security code. The simulator uses them to choose the outcome and keeps only brand and last four digits; it never stores or logs the number or the code, including in error replies (NFR-CON-02). Numbers that fail the Luhn check are refused with `400 invalid_card`.
- **Versioning.** The rules in `API-CONTRACT.md` section 1 apply. Paths stay at `/sim/...` (as in the skeleton) while v1 lasts; a breaking change moves to `/sim/v2/...`.

## 3. Operation identity

1. **The gateway creates the `operation_id` and commits it, with the operation type, before it calls the simulator.** A crash between commit and call therefore leaves an operation the gateway knows about, which the restart sweep checks (FR-TXN-15).
2. **One logical operation, one `operation_id`.** Authorise, capture, cancel and each refund get their own ID. Capture, cancel and refund name the authorisation they act on with `authorisation_id`, which is the `operation_id` of the authorise call.
3. **The simulator holds at most one record per `operation_id`** (unique key). For a repeat of a recorded operation:
   - same request: the stored reply, `200` with `Simulator-Replayed: true`. No second effect.
   - different request: `409 operation_id_conflict` with the existing record. Nothing changes.
   - the ID was retired as `no_record` (section 6): `409 operation_retired`. Nothing changes.
   The request is compared without the card number and security code, which the simulator does not keep.
4. **The gateway never resends an operation to learn its outcome.** It asks with a status query (section 6). An authorisation cannot be resent anyway, because the vault deletes the card details at submission (architecture section 11).
5. While one operation of a payment is unresolved, the gateway sends no other operation for that payment (FR-TXN-15, `409 operation_in_progress` in `API-CONTRACT.md`).

## 4. Operations

All four calls answer `200` with the operation record, approved or declined. **A decline is a 200, not an error.** Errors (`400`, `401`, `409`, `503`) mean nothing new was recorded.

| Call | Request | Approved when | Effect | Declined reasons |
| --- | --- | --- | --- | --- |
| `POST /sim/authorise` | `operation_id`, `merchant_id`, `payment_id`, `amount`, `currency`, `card` | The test card approves (section 5) and the card has not expired | Opens an authorisation holding `amount` | `insufficient_funds`, `card_declined`, `expired_card` |
| `POST /sim/capture` | `operation_id`, `authorisation_id`, `amount` | Authorisation is open and `amount <= authorised_amount` | Charges `amount`, releases the rest; authorisation becomes `captured`. Once only (partial capture, PR #3) | `authorisation_not_found`, `authorisation_not_open`, `capture_exceeds_authorised` |
| `POST /sim/cancel` | `operation_id`, `authorisation_id` | Authorisation is open | Releases the whole hold; authorisation becomes `cancelled` | `authorisation_not_found`, `authorisation_not_open` |
| `POST /sim/refund` | `operation_id`, `authorisation_id`, `refund_id`, `amount` | Authorisation is captured and approved refunds plus `amount` are at most `captured_amount` | Returns `amount` to the fictional cardholder | `authorisation_not_found`, `authorisation_not_captured`, `refund_exceeds_captured` |

**Authorisation states in the simulator:** `open`, then `captured` or `cancelled`. Capture and cancel lock the authorisation row, so two of them at once cannot both succeed. The simulator does **not** expire authorisations itself; the gateway sends the 7-day auto-cancel as an ordinary cancel (FR-TXN-05).

**Ledger.** Each approved operation writes balanced fictional ledger entries (hold, release, charge, refund) in the same transaction as the record. Declined operations write none. The ledger is internal to the simulator; the operation records are what the gateway compares against.

**Every reply includes `authorisation`**, the current state and totals of the authorisation (authorised, captured, refunded), so reconciliation can compare totals as well as single operations.

## 5. Test cards (FR-SIM-01)

The card number decides what happens to an authorisation. (v2: the decision is now made by the issuer node behind the acquirer, with the same results; see NETWORK-CONTRACT.md.) Any expiry in the future and any 3-digit code work; an expiry in the past is declined as `expired_card`. Numbers not in this table are declined as `card_declined`. These must match `simulator/apps/operations/test_cards.py`.

| Card number | Brand | Behaviour | What the gateway sees |
| --- | --- | --- | --- |
| 4242 4242 4242 4242 | Visa | Approve | `200 approved` |
| 5555 5555 5555 4444 | Mastercard | Approve | `200 approved` |
| 4000 0000 0000 9995 | Visa | Decline, insufficient funds | `200 declined` |
| 4000 0000 0000 0002 | Visa | Decline, generic | `200 declined` |
| 4000 0000 0000 0119 | Visa | Lost response: records the approval, never replies | Timeout; status query finds `approved` (demo story S4) |
| 4000 0000 0000 0127 | Visa | Slow: records the approval, replies after 15 s | Timeout; the late reply arrives after the gateway gave up and is ignored; status query finds `approved` |
| 4000 0000 0000 0135 | Visa | **New.** Lost request: records nothing, never replies | Timeout; status query finds `no_record`; payment declined `not_processed` (UC-28) |
| 4242 4200 xxxx xxxx | Visa | **Demo range.** Any Luhn-valid 16-digit number with this prefix approves | `200 approved` |
| 5555 5500 xxxx xxxx | Mastercard | **Demo range.** Same as above | `200 approved` |
| 4000 0099 xxxx xxxx | Visa | **Demo range.** Like 4000 0000 0000 9995: decline, insufficient funds | `200 declined` |
| 4000 0119 xxxx xxxx | Visa | **Demo range.** Like 4000 0000 0000 0119: lost response | Timeout; status query finds `approved` (demo story S4) |
| 51BB BB00 xxxx xxxx | Mastercard | **v2 shopper banks.** BBBB = 0000 to 9999: 10,000 synthetic issuer ranges, all approve. The red team's background shoppers use them, so they come from many card ranges as real shoppers do (docs/v2/LEARNING-LOOP.md) | `200 approved` |

The demo ranges were added for demo day: fraud screening counts a card's earlier attempts over 30 days across the gateway, so a card used in many rehearsals starts to look risky. `python manage.py new_demo_card` on the simulator prints fresh numbers with no history (`--kind` picks the range; `--set` prints one card per demo scene). The exact numbers in the table above take precedence over the ranges.

Capture, cancel and refund have no card, so their faults come from fault rules (section 7).

## 6. Status query and the "no record" rule (FR-SIM-03, UC-28)

`GET /sim/operations/{operation_id}?type=<type>` returns the record for that ID.

**If there is no record, the simulator writes one.** It inserts a `no_record` entry under the same unique key and returns it. From then on the ID is **retired**: if the original request arrives late (for example it was held up in the network), the simulator answers `409 operation_retired` and records no effect. That is what makes "no record" a final answer the gateway can act on.

**Why it cannot race.** The operation and the `no_record` entry are inserted under the same unique key in PostgreSQL. If an operation is being processed when the query arrives, the query's insert waits for that transaction and then reads the finished record. If the query commits first, the operation's insert fails and it is rejected. Either the operation happened and the query reports it, or the query retired the ID and the operation never happens. There is no in-between answer such as "pending".

A `no_record` entry never changes. Later queries for the same ID return the same entry. `GET /sim/operations` (the comparison listing) is read-only and never retires an ID.

## 7. What the gateway does with each answer

Only a signed `200` (or the record inside a signed `409`) is evidence. Anything else leaves the operation as it was.

| Answer from the simulator | Payment authorise | Capture or cancel | Refund |
| --- | --- | --- | --- |
| `200 approved` | `authorised` | `captured` / `cancelled`, `pending_operation` cleared | `completed`, reservation becomes refunded |
| `200 declined` | `declined`, reason `card_declined` (expired card and insufficient funds included; the dashboard shows the detail) | stays `authorised`, `pending_operation` cleared, discrepancy logged | `failed`, `declined_by_processor`, reservation released |
| `200 no_record` (status query only) | `declined`, reason `not_processed` | stays `authorised`, operation retired, `pending_operation` cleared; merchant may retry with a new Idempotency-Key | `failed`, `not_processed`, reservation released |
| `409 operation_retired` | Gateway bug (it never resends). Apply the `no_record` inside the reply and alert | same | same |
| `409 operation_id_conflict` | Gateway bug. Leave unresolved, escalate (UC-42) | same | same |
| Timeout, connection error, `5xx`, `400`, `401`, bad signature | `unresolved` (FR-CHK-11) | `pending_operation` unresolved | `unresolved`, reservation kept |

Each change goes through `check_transition`, writes the outbox row in the same transaction (`API-CONTRACT.md` section 6) and an audit entry quoting `simulator_reference` and the request ID.

**Reconciliation schedule (FR-TXN-11, FR-TXN-12, NFR-AVA-03).** The worker queries each unresolved operation by its original `operation_id` after 30 s, 1 min, 2 min, 5 min, 10 min and 20 min (±10% jitter), which is 6 checks (`RECONCILIATION_MAX_CHECKS = 6`, already in settings) ending about 38.5 minutes after the timeout. Still no signed answer after the sixth check: the operation is escalated to administrators (UC-42). That meets NFR-AVA-03, which needs every unresolved payment final or escalated within 1 hour. An administrator's re-check (FR-TXN-13) is the same status query; nobody can set an outcome by hand.

**On start-up** the gateway marks every operation it committed but has no answer for as unresolved and queues an immediate status query before it sends anything else for that payment (FR-TXN-15).

## 8. Fault controls (FR-SIM-02)

Recovery tests need repeatable faults beyond the test cards. The simulator has a small test-control API, **present only when `SIM_TEST_CONTROLS=1`** (on in local and CI, off in the demo deployment unless the demo needs it). It uses its own key, `simc_...` from the secret `simulator_test_control_key`, which only the test runner holds.

`POST /sim/faults` adds a rule: `match` (any of `operation_type`, `payment_id`, `merchant_id`, `card_last4`), an `action`, and `remaining` (apply to the next N matching operations, or `null` until removed). `DELETE /sim/faults` clears all rules between tests.

| Action | What the simulator does | Tests |
| --- | --- | --- |
| `drop_request` | Records nothing, holds the connection 15 s, closes it without a reply | `no_record` path, late-arrival rejection |
| `delay_request` | Waits `delay_ms` before recording | A status query during the wait retires the ID; the delayed request then gets `409 operation_retired` (FR-SIM-03) |
| `drop_response` | Records the operation, holds 15 s, closes without a reply | Lost response, S4, refund kept reserved |
| `delay_response` | Records, replies after `delay_ms` | Slow simulator, NFR-PER-01 measured without it |
| `fail_status_query` | Status queries for matching operations answer `503` | Escalation after 6 checks (NFR-AVA-03) |
| `notify` | Sends `copies` notifications after `delay_ms` (section 9) | Repeated and late notifications |

The test cards in section 5 behave like built-in rules (0119 is `drop_response`, 0127 is `delay_response` 15 s, 0135 is `drop_request`), so the demo needs no test controls.

## 9. Simulator notifications

FR-SIM-02 asks for repeated notifications, and URS Section 2.4.1 allows recovery "through a status enquiry or authenticated notification". The simulator can therefore send `POST /internal/simulator/notifications` to the gateway (OpenAPI `webhooks.operationRecorded`): a signed `operation.recorded` message carrying the operation record, with a notification `id` that stays the same across copies and a `delivery` copy number.

The gateway verifies the signature and timestamp, then treats the record exactly like a status-query reply: it applies it only if the operation is still `processing` or `unresolved`, so a repeat, a late copy or a copy for an operation that is already final changes nothing. It answers `200` within 5 seconds. The simulator does not retry; the status query remains the safety net. `no_record` is never notified, because only a status query can retire an ID.

## 10. Decisions taken for this contract

The URS leaves these values to the contract. All four were confirmed by Fifa on 13 November 2026. Each follows the practice of `API-CONTRACT.md` (Stripe and Adyen style idempotency and signatures) or an existing setting.

| # | Proposal | Value | Why |
| --- | --- | --- | --- |
| S1 | Gateway timeout for a simulator call | 2 s to connect, 10 s for the reply; then unresolved, never failed | A simulator that has not connected in 2 s is down, so fail fast. Keeps the customer's wait short and well inside NFR-AVA-03; the 15 s slow and dropped cases always exceed it. `SIMULATOR_CONNECT_TIMEOUT_SECONDS`, `SIMULATOR_TIMEOUT_SECONDS` |
| S2 | Reconciliation schedule | 30 s, 1, 2, 5, 10, 20 min (±10%), then escalate | Uses the existing `RECONCILIATION_MAX_CHECKS = 6`; escalates at about 38.5 min, inside the 1-hour bound of NFR-AVA-03. `RECONCILIATION_DELAYS_SECONDS` |
| S3 | Simulator notifications | Off unless a `notify` fault rule is active | Keeps the status query as the one normal recovery path, so S4 and NFR-AVA-03 test what they mean to test, while FR-SIM-02 can still be shown |
| S4 | Authentication | Bearer key for requests; HMAC-signed replies and notifications | A signed reply can be kept as evidence for a correction (FR-TXN-13). Same signature scheme as the merchant API, so one implementation |

## 11. Follow-ups outside `contracts/`

All done by sprint 8 (13 November 2026): fault controls in `simulator/apps/faults/`, notifications received by `gateway/apps/reconciliation/views.py`, the secrets in `deploy/compose.yaml`. When several fault rules match one operation, the oldest request rule (`drop_request`, `delay_request`, `drop_response`, `delay_response`) applies and every matching `notify` rule fires; a replay of an operation already recorded never uses up a rule. The original list:

- `simulator/apps/operations/test_cards.py`: add `4000000000000135` as a lost-request behaviour (`DROP_REQUEST`), and treat the expiry date.
- `simulator/apps/operations/urls.py`: add `GET /sim/operations` and the `/sim/faults` routes behind `SIM_TEST_CONTROLS`.
- `gateway/config/settings.py`: the S1 values are already in `GATEWAY_RULES` (sprint 2); add `RECONCILIATION_DELAYS_SECONDS = (30, 60, 120, 300, 600, 1200)` for S2.
- `deploy/compose.yaml`: add the secrets `simulator_api_key`, `simulator_signing_secret` (both services) and `simulator_test_control_key` (test runner only).
- `API-CONTRACT.md` section 12 and `contracts/README.md` now point here.

## 12. Traceability

| Requirement | Section |
| --- | --- |
| FR-SIM-01 | 4, 5 |
| FR-SIM-02 | 8, 9 |
| FR-SIM-03, UC-28 | 3, 6, 7 |
| FR-SIM-04, UC-23, UC-24 | 4 |
| FR-CHK-07, FR-CHK-11 | 3, 7 |
| FR-TXN-11, FR-TXN-12, FR-TXN-13, FR-TXN-15, UC-42 | 7 |
| NFR-AVA-03 | 7, 8 |
| NFR-CON-02, NFR-ENC-01 | 2 |
