# 3-D Secure (v2, Sprint 3)

Until now, when fraud screening said **Verify**, SecurePay checked the shopper itself: an
email code, or the shopper's passkey. With 3-D Secure, the **card's bank** does the check, as
real card payments do. SecurePay Bank's access control server (ACS) sends the cardholder a
code by text message. The shopper types it into the bank's screen. The bank returns a proof,
and the proof goes with the authorisation.

The roadmap's demo: a medium-risk payment is challenged, the shopper passes, and the payment
is approved.

## Who does what

```
shopper --pays S$600--> checkout --screening: Verify--> gateway
gateway --POST /sim/3ds/authenticate--> SecurePay Bank ACS: enrolled? start a challenge, "text" a code
shopper --code--> checkout (bank's screen) --> gateway --POST /sim/3ds/{id}/challenge--> ACS: Y + ECI 05 + CAVV
gateway --/sim/authorise + authentication--> acquirer --0100 with DE 48--> switch --> issuer: checks the CAVV, 00
```

| Part | Where | What it does |
| --- | --- | --- |
| ACS | Simulator, `apps/threeds` (`/sim/3ds`) | Says whether a card is enrolled, starts a challenge, checks the code (3 tries, 10 minutes), and makes the CAVV |
| Issuer check | Issuer node (`network-issuer`) | A 0100 whose DE 48 is not a proof the bank made for this card and amount, or one already used, is `05` |
| Gateway | `apps/checkout/threeds.py`, `service.py` | On Verify, asks the bank first; relays the shopper's code; sends the proof with the authorisation. Falls back to the email code or passkey |
| Checkout | `BankChallenge.tsx` | The bank's screen: merchant, amount, card, the simulated text message and the code box |
| Dashboard | Payment detail | A **3-D Secure** panel (passed or failed, ECI) and "3-D Secure proof sent · ECI 05" on the authorisation in **Card network** |

## Rules

- **Only on Verify.** A low-risk payment is not challenged. A blocked payment never reaches
  the bank.
- **Enrolled cards:**
  - SecurePay Bank's issued cards (`4571 20`);
  - the test card `4000 0000 0000 3220`;
  - the demo range `4000 3220 xxxx xxxx` (`python manage.py new_demo_card --kind three-ds`).

  Any other card is not enrolled and gets the email code (or passkey) as before.
- **The bank does not answer?** Within 2 seconds, the gateway falls back to the email code.
  A screening Verify never fails just because the bank is slow.
- **Codes:** three wrong codes and the bank fails the challenge, so the payment is blocked
  (`verification_failed`, as before). After 10 minutes, or when the checkout session ends,
  it is blocked as `verification_expired`. A new code cannot be asked for: the bank sent it.
- **What is kept:**
  - The bank keeps a keyed hash of the card number, the amount and the status. It does not
    keep the code, which is derived from the challenge ID with the bank's key.
  - The gateway keeps the challenge ID, the result, the ECI and the CAVV.
  - The audit log has the method and the ECI, never the code or the CAVV.
- **One proof, one payment.** The issuer uses up a proof the first time it sees it. A second
  authorisation with the same CAVV, or with another amount or card, is declined `05`.
- **Card number:** the gateway reads it from the vault without taking it (`vault.peek`) to
  ask the bank, and the authorisation takes it as before. The security code is never sent to
  the ACS.
- **No phone in the sandbox.** The bank's text message is shown on the bank's screen in the
  checkout, marked as simulated. The SecurePay Bank sandbox will move it to the bank app's
  phone inbox.

## Demo

1. In the demo shop, add items worth more than S$500. The S$500 rule makes screening ask for a
   check.
2. Pay with `4000 0000 0000 3220` (any future expiry, any CVC).
3. The checkout shows **SecurePay Bank · 3-D Secure, Confirm this payment**, with the
   simulated text message.
4. Type a wrong code: "That code isn't right. 2 attempts left." Then type the right code. The
   payment is approved.
5. In the dashboard, open the payment. **3-D Secure** shows "Passed" with ECI 05, and **Card
   network** shows "3-D Secure proof sent · ECI 05" on the authorisation.

The same payment with `4242 4242 4242 4242` gets the email code, as before.

## Limits

- **Frictionless flow:** a real ACS may approve a low-risk payment without a code, using
  device data. Ours always challenges, because we only ask when screening said Verify.
- **No liability shift or merchant-side ECI** in the merchant API yet. The proof is visible
  to staff in the dashboard. Chargebacks (Sprint 3) can use it later.
- **One bank.** The directory server step (which bank's ACS) is implied: every enrolled card
  is SecurePay Bank's.
