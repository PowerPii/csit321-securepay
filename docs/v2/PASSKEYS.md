# Passkeys (v2, Sprint 2)

Shoppers with a SecurePay account can add a **passkey**, which works with a fingerprint, face or
device PIN. They can then use it in two places:

- **To sign in**, instead of an email and password.
- **To approve a risky payment**, instead of the 6-digit code we email. When screening says
  Verify, a shopper who is signed in and has a passkey is asked for the passkey. No code email
  is sent unless they ask for one.

The private key stays on the shopper's device. The gateway keeps only the credential ID, the
public key and a signature counter (`gateway/apps/passkeys`). The WebAuthn checks are done by
the `webauthn` library ([py_webauthn](https://github.com/duo-labs/py_webauthn)), which is
pinned with hashes in `requirements.lock`.

Staff passkey sign-in for the dashboard comes in a follow-up slice.

## Rules

| Rule | Value |
| --- | --- |
| Relying party ID | `WEBAUTHN_RP_ID`, by default the host of `CHECKOUT_BASE_URL` (`gateway.<domain>`) |
| Allowed origin | `WEBAUTHN_ORIGIN`, by default `https://gateway.<domain>` |
| User verification | Required every time: fingerprint, face or device PIN, not just a tap |
| Passkey type | Discoverable (resident key), so sign-in needs no email |
| Attestation | `none`: we trust the device to keep its key, and we don't check who made it |
| Challenge | 32 random bytes, stored only as SHA-256, used once, expires after `PASSKEY_CHALLENGE_SECONDS` (300); the worker deletes it an hour later |
| Challenge purpose | `register`, `sign_in` or `approve_payment`. A challenge only works for the purpose (and payment) it was made for |
| Cloned authenticator | A signature counter that does not go up is refused |
| Per account | At most `PASSKEYS_PER_CUSTOMER` (10) |

Staging and production have different relying party IDs (`gateway.staging.securepaysg.me` and
`gateway.securepaysg.me`), so a passkey made on one does not work on the other.

## API (same origin, JSON only, `sp_customer` cookie)

| Call | What it does |
| --- | --- |
| `GET /customer/passkeys` | Lists the account's passkeys: `{data: [{id, name, synced, created_at, last_used_at}], limit}` |
| `POST /customer/passkeys/register/options` | Returns `{options}` for `navigator.credentials.create()`. Needs a session |
| `POST /customer/passkeys/register` | Takes `{credential, name?}` and returns 201 with the new passkey. The shopper gets an email that a passkey was added |
| `POST /customer/passkeys/{id}/remove` | Removes the passkey (204) |
| `POST /customer/passkeys/sign-in/options` | Returns `{options}` for `navigator.credentials.get()`. No account is named |
| `POST /customer/passkeys/sign-in` | Takes `{credential, remember?}` and signs in, like a password sign-in |
| `POST /checkout/{session}/verify/passkey/options` | Returns `{options}` limited to the shopper's own passkeys |
| `POST /checkout/{session}/verify/passkey` | Takes `{credential}`. A good answer approves the payment, like a right code |

Every answer that fails is a `400 passkey_failed`. This covers a wrong origin, a wrong signature,
missing user verification, an unknown passkey, an expired or reused challenge, or a cloned
counter.

The rate limits are:

- The two sign-in calls share the `customer_auth` limit.
- Checkout calls use the anonymous limit.

## Approving a payment

1. Screening says **Verify**. If the payment belongs to a signed-in shopper with a passkey,
   the verification block in the checkout session says `passkey: true` and `code_sent: false`,
   and no email is sent.
2. The page shows **Approve with your passkey**.
   - `verify/passkey/options` checks that the same shopper is still signed in on this browser.
     If not, it answers `409 passkey_not_available`.
   - The browser asks for the passkey.
   - `verify/passkey` checks the answer and sends the payment on to the bank.
3. **Email me a code instead** calls `verify/resend`. The first code goes out straight away,
   and from then on the normal code rules apply.
4. A failed passkey answer counts as a wrong attempt. The third wrong attempt blocks the
   payment with `verification_failed` (FR-CHK-18), as wrong codes do. Cancelling the prompt in
   the browser counts for nothing.
5. A code sent to a passkey shopper's checkout before any code was emailed is refused with
   `400 code_not_sent`.

The audit log records which method was used: `payment.verified` has `method: "passkey"` and the
passkey's ID, and `customer.signed_in` has `method: "passkey"`.

## Pages

These are in `frontend/checkout`:

- **Account, Passkeys:** lists the passkeys, adds one with an optional name, and removes one.
- **Sign in:** a **Sign in with a passkey** button, shown only where the browser has WebAuthn.
- **Checkout, One more step:** **Approve with your passkey** (with **Email me a code instead**)
  for passkey shoppers. The code form also offers **Use my passkey instead**.

`src/passkeys.ts` only converts between base64url text and bytes. The gateway does every check.

## Testing

`gateway/apps/passkeys/testing.py` is a software authenticator. It makes real P-256 keys and
"none" attestation, so the tests in `apps/passkeys/tests.py` run the real library checks on
real signatures, including wrong origin, no user verification, replay, expiry, cloned counters,
another shopper's passkey and three bad answers. The front-end tests use a fake
`navigator.credentials` (`src/testPasskeys.ts`).

On a real device, passkeys need HTTPS and the real host name. Try them on staging
(`gateway.staging.securepaysg.me`) with a phone or laptop that has a screen lock.
