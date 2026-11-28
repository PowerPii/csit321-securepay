# Issuing (v2, Sprint 2)

Until now, SecurePay only took card payments. With issuing, a merchant can also **make
cards**: virtual Visa cards from SecurePay Bank, the issuer on our own network
([NETWORK-CONTRACT.md](../../contracts/NETWORK-CONTRACT.md)).

- Each card has a **spending limit** in SGD.
- A card can be **single use**. It is then used up by its first approved payment.
- The bank decides every payment on the card against these rules, at the moment of
  authorisation.

The roadmap's demo: make an S$30 single-use card (for example, one for an AI agent to buy
one thing). The first payment is approved. The second is declined by the bank.

## Who does what

```
merchant --POST /v1/issuing/cards--> gateway --POST /sim/issuing/cards--> SecurePay Bank
                                     (stores last 4,   (signed reply)       (makes the number,
                                      limit, status)                         keeps a keyed hash)

shopper pays with the card at any shop:
gateway --/sim/authorise--> acquirer --0100--> switch --0100--> issuer: limit, use, status
                                                                 61 / 62 / 00
```

| Part | Where | What it does |
| --- | --- | --- |
| Card management | Simulator, `apps/issuing` (`/sim/issuing/cards`) | Makes the card in the bank's range, `4571 20xx xxxx xxxx`. Returns the number and security code once, and keeps a keyed SHA-256 of the number and the last four digits |
| Authorisation | Issuer node (`network-issuer`) | Answers a `0100` on an issued card: `14` unknown, `54` wrong expiry, `62` used or cancelled, `61` over the limit, else `00`. A cancel gives the amount back, and a capture of less than was authorised gives back the rest |
| Merchant API | Gateway, `apps/issuing` | `/v1/issuing/cards`: create, list, read, cancel. API-CONTRACT.md section 3 |
| Dashboard | **Issued cards** page | Operators make cards (the number is shown once), see the bank's answers and cancel. Administrators see every merchant's cards, read-only |
| SDK | `securepaysg` | `sp.issuing.cards.create / list / retrieve / cancel` |

## Rules

- **Limit:** S$1 to S$10,000 per card, over the card's whole life.
- **Expiry:** 3 years from the month the card is made.
- **Currency:** the limit is in SGD. A payment in another currency counts at its SGD value,
  as the network works in SGD ([FX.md](FX.md)).
- **Card number:** the gateway and the bank never store it.
  - It is in the `201` reply to the create call only, sent with `Cache-Control: no-store`.
  - It is not kept for idempotent replay, unlike other `POST` replies.
  - A retry with the same `Idempotency-Key` reaches the same card, because the card's ID
    is derived from the merchant, the mode and the key. That retry gets the card without its
    number.
  - If the first reply was lost, the merchant cancels the card and makes another.
- **No bank, no card:** if the bank does not answer, the gateway makes no card and answers
  `503 issuer_unavailable`.
- **Screening still applies:** a payment with an issued card is still screened like any
  other, before it reaches the bank. A fraud block happens before the bank sees it.
- **Merchant view:** the merchant whose shop took the payment sees a bank decline as
  `card_declined`, as for any card. The card owner sees the exact reason on the card
  (`61 Exceeds amount limit`, `62 Restricted card`).
- **Modes:** a card made with a test key lives in test mode. A live key sees only live
  cards.

## Infrastructure change

Until now the issuer node kept no state. It now reads and writes its issued cards in the
**simulator database**:

- The `network-issuer` container gets the simulator database user and password.
- It joins the private `data` network.
- The acquirer container still runs the migrations.

No new database or secret is needed. A separate bank database would be closer to reality;
it is left as a follow-up.

## Demo

1. In the dashboard, sign in as the merchant operator and open **Issued cards**.
2. Choose **Make a card**, with a limit of S$30 and **Single use** ticked. Copy the number,
   expiry and security code.
3. In the demo shop, pay for an order of S$30 or less with that card. It is approved.
4. Pay again with the same card. It is declined.
5. Open the card. It shows `Used`, S$30 spent, and the bank's answers: `00 Approved`, then
   `62 Restricted card`. The payment's **Card network** section shows `62` at SecurePay Bank.

Over the API:

```bash
curl -X POST $GATEWAY/v1/issuing/cards \
  -H "Authorization: Bearer $SECUREPAY_TEST_KEY" \
  -H "Idempotency-Key: agent-card-0001" \
  -H "Content-Type: application/json" \
  -d '{ "spending_limit": 3000, "single_use": true, "description": "Agent order" }'
```

## Tried locally

On 28 Nov 2026 I ran this against a real gateway and simulator (the issuer in the same
process):

| Step | Result |
| --- | --- |
| Make an S$30 single-use card | `201`, number `457120…`; the same key again: `200`, no number |
| Pay S$30 | Authorised (`00`) |
| Pay S$5 with the same card | Declined, `62 Restricted card`; the card shows `used`, S$30 spent |
| S$50 card: pay S$30, S$25, S$20 | Authorised, declined (`61 Exceeds amount limit`), authorised |
| Cancel it, then pay S$1 | Declined |

## Limits

- **Virtual cards only.** There are no physical cards, cardholders or PINs.
- **One limit per card.** There are no per-merchant, per-day or per-category controls yet.
  Agentic payments (Sprint 3, optional) would add mandates scoped by merchant and time on
  top of this.
- **Refunds don't restore the limit**, as with most real spend controls.
- **No issuing ledger yet.** The money a merchant spends on its cards is not charged to its
  balance. Funding cards from the merchant's balance is a follow-up for the ledger.
