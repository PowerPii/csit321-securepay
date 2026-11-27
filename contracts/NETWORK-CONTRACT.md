# SecurePay Network contract (v2, Sprint 1 and 2)

v1 had one simulator that "stood in for the card network and issuer" (SIMULATOR-CONTRACT.md
section 1). In v2 that stand-in is split into the three parties a real card payment crosses,
so the demo can show where a decision is made and how long each hop took:

```
gateway --HTTPS /sim/authorise--> acquirer --0100--> switch --0100--> issuer
                                  (simulator)        ("SecurePay       ("SecurePay
                                                      Switch")          Bank")
gateway <---- signed operation --  acquirer <--0110-- switch <--0110-- issuer
```

The gateway-to-simulator contract does not change: the gateway still calls `/sim/authorise`,
`/sim/capture`, `/sim/cancel` and `/sim/refund` with the same bodies and gets the same signed
operation record, which now also carries a `network` object (section 5). Slice 1 sent
authorisations over the network; slice 2 sends capture, cancel and refund too (section 7),
and the staff dashboard shows each operation's path (section 8).

| File | What it holds |
| --- | --- |
| `contracts/NETWORK-CONTRACT.md` | This file |
| `simulator/apps/network/iso.py` | Message types, data elements and response codes |
| `contracts/schemas/simulator-operation.schema.json` | `network` and `$defs/Network` |

## 1. Roles

| Node | Container (Compose) | `SIM_ROLE` | What it does |
| --- | --- | --- | --- |
| Acquirer | `simulator` | `acquirer` | The `/sim` API for the gateway. Builds the 0100, sends it to the switch, records the 0110 with its ledger hold, as in v1. The only node with a database. |
| Switch | `network-switch` | `switch` | Routes an authorisation by the card's first digit: 4 (Visa), 5 or 2 (Mastercard) go to the issuer, anything else is answered `15`. A capture, cancel or refund has no card number and goes to the issuer of the authorisation its DE 90 names; one without a valid DE 90 is answered `15`. |
| Issuer | `network-issuer` | `issuer` | Holds the cardholder accounts and approves or declines. Stateless: its decisions are the test cards of SIMULATOR-CONTRACT.md section 5, so every v1 test card behaves the same. |

All three run the same simulator image. `SIM_ROLE=all` (the default, used by local runs and
every test) runs the three nodes in one process and they call each other directly.

## 2. Messages

ISO 8583 vocabulary carried as JSON, so a trace reads without a decoder:

```json
{"mti": "0100", "fields": {"2": "4242424242424242", "3": "000000", "4": "000000004590", "11": "318204", ...}}
```

| MTI | Meaning |
| --- | --- |
| `0100` / `0110` | Authorisation request / response |
| `0220` / `0230` | Capture: completion advice for an earlier authorisation |
| `0400` / `0410` | Cancel: reversal of an earlier authorisation (releases the hold) |
| `0200` / `0210` | Refund: financial request with processing code `200000` |

| DE | Name | In | Notes |
| --- | --- | --- | --- |
| 2 | Primary account number | 0100 | Never echoed in the 0110, never stored, never in a trace |
| 3 | Processing code | both | `000000` purchase, `200000` refund |
| 4 | Amount | both | 12 digits, cents |
| 7 | Transmission time | both | `MMDDhhmmss` UTC |
| 11 | System trace audit number (STAN) | both | 6 random digits per request |
| 14 | Expiry | 0100 | `YYMM` |
| 32 | Acquirer ID | both | `SPACQ01` |
| 37 | Retrieval reference number (RRN) | both | 12 characters, 0-9 A-Z |
| 38 | Authorisation code | 0110 | 6 characters, approvals only |
| 39 | Response code | 0110 | Section 6 |
| 41 | Terminal ID | both | `SPGW0001` (the gateway) |
| 42 | Card acceptor ID | both | Last 15 characters of the gateway merchant ID |
| 49 | Currency | both | `702` (SGD) |
| 90 | Original data | follow-ups | `0100` + the authorisation's STAN + RRN (22 characters; our short form of ISO's 42) |

The security code (CVC) never leaves the acquirer: there is no data element for it.

## 3. Security

- Every message between nodes is signed: `Network-Signature: t=<unix seconds>,v1=<hex
  HMAC-SHA256>` over `"<t>." + raw body`, both ways. A node refuses an unsigned or stale
  (over 5 minutes) request with a bare `401`, and the sender treats an unsigned reply as no
  reply at all (an approval nobody signed is not an approval).
- The key is SHA-256 of `"network|" + simulator_signing_secret`, so no new secret file is
  needed, and a signed simulator reply can never pass as a network message.
- The switch and the issuer are only on the private `simulator` Docker network: no Caddy
  route, no published port. Messages there are plain HTTP (signed, not encrypted). This is
  acceptable only because the network carries test card numbers; TLS between the nodes is a
  follow-up if it ever carries anything else.
- The switch and issuer have no database and keep no state.

## 4. Transport and timeouts

| Setting | Default | Meaning |
| --- | --- | --- |
| `SIM_ROLE` | `all` | `all`, `acquirer`, `switch` or `issuer`; decides which routes the container serves (`/sim/*` or `/net/switch` or `/net/issuer`) |
| `SIM_SWITCH_URL` | empty | Where the acquirer sends a 0100. Empty = in-process |
| `SIM_ISSUER_URL` | empty | Where the switch sends a 0100. Empty = in-process |
| `SIM_NETWORK_TIMEOUT` | `3` | Seconds per hop, well inside the gateway's 10 s reply timeout |

A node that cannot be reached, times out, or replies without a valid signature is answered
`91` (issuer unavailable) by the node before it. The acquirer records that as a decline, so
the gateway gets a normal signed `200 declined` with `decline_reason: issuer_unavailable`,
never a hang.

## 5. The `network` object on an operation

Set on every operation that crossed the network. `null` for `no_record`, for a capture,
cancel or refund the acquirer declined on its own rules (it is never sent), and for records
made before v2.

```json
"network": {
  "stan": "318204", "rrn": "K7Q2M9X4B1TZ", "auth_code": "A1B2C3",
  "response_code": "00", "response_text": "Approved",
  "trace": [
    {"node": "acquirer", "mti_in": "0100", "mti_out": "0110", "response_code": "00", "elapsed_ms": 4},
    {"node": "switch",   "mti_in": "0100", "mti_out": "0110", "response_code": "00", "elapsed_ms": 3},
    {"node": "issuer",   "mti_in": "0100", "mti_out": "0110", "response_code": "00", "elapsed_ms": 1}
  ]
}
```

One hop per node the message reached, acquirer first. Each hop's `elapsed_ms` includes the
hops after it. A trace never contains the card number. `auth_code` is set on approved
authorisations only; follow-ups have their own STAN and RRN.

## 6. Response codes

| DE 39 | Meaning | Who answers it | `decline_reason` |
| --- | --- | --- | --- |
| `00` | Approved | Issuer | (approved) |
| `05` | Do not honour | Issuer (generic decline and unknown cards) | `card_declined` |
| `51` | Insufficient funds | Issuer | `insufficient_funds` |
| `54` | Expired card | Issuer | `expired_card` |
| `15` | No such issuer | Switch (card not 4, 5 or 2) | `card_declined` |
| `91` | Issuer unavailable | Switch or acquirer (next node unreachable) | `issuer_unavailable` |
| `96` | System malfunction | Any node (malformed message) | `issuer_unavailable` |

The lost-response, slow and lost-request test cards keep their v1 behaviour: the issuer
approves the first two (`00`) and the acquirer then loses or delays its reply to the gateway;
the lost-request card is dropped by the acquirer before it reaches the network.

The gateway treats every simulator decline as `card_declined` for the merchant, so the new
`issuer_unavailable` reason needs no gateway change.

## 7. Capture, cancel and refund (slice 2)

The acquirer keeps the v1 rules (SIMULATOR-CONTRACT.md section 4): an authorisation must be
open to capture or cancel, a capture cannot exceed the authorised amount, refunds cannot
exceed the captured amount. In order, with the authorisation row locked:

1. The acquirer checks its own rules. A failure is declined at once with the v1 reason and
   no network message (`network: null`), as a real acquirer would not send it.
2. Otherwise it sends the follow-up (`0220`, `0400` or `0200`) with DE 90 pointing at the
   authorisation. The issuer accepts any well-formed follow-up (`00`); a malformed DE 90 is
   `96`.
3. `00` is recorded as approved with its ledger posting. Anything else, including an
   unreachable network (`91`), is declined with `issuer_unavailable`, and the authorisation
   and the ledger are left as they were, so the gateway can try again with a new operation.

An authorisation recorded before v2 has no STAN or RRN to point at, so its capture, cancel
or refund is decided by the acquirer alone, as in v1 (`network: null`).

The row lock is held for the network round trip, at most `SIM_NETWORK_TIMEOUT` per hop. It
covers only that one authorisation, and it is what keeps a capture and a cancel at the same
moment from both succeeding.

## 8. In the staff dashboard

The gateway already keeps every answered operation's signed reply as evidence
(`payments.Operation.evidence_body`). `GET /internal/payments/{id}` reads the `network`
object from it and returns it on each operation (`operations[].network`, the known fields
only; anything malformed is `null`). The payment detail page shows a "Card network" section:
for each operation, the response code and text, the authorisation code, STAN and RRN, and
the path Acquirer, SecurePay Switch, SecurePay Bank with the message types and milliseconds
at each node. Nothing new is stored on the gateway.
