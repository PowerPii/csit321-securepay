import { jsonClient } from "./http";
export { ApiError } from "./http";

// The hosted checkout API (gateway/apps/checkout). Same origin, JSON only, session-bound:
// the browser sends card details and "pay"; it can never set merchant, amount or currency.

export type Result =
  | "open"
  | "processing"
  | "verification_required"
  | "approved"
  | "declined"
  | "not_completed"
  | "verification_failed"
  | "pending"
  | "cancelled"
  | "expired"
  | "unavailable";

export interface Session {
  session_id: string;
  status: string;
  result: Result;
  merchant: { name: string };
  /** v2: true when the merchant used a live key. Older gateways leave it out (test). */
  livemode?: boolean;
  amount: number;
  currency: string;
  order_reference: string;
  description: string | null;
  line_items: { name: string; quantity: number; unit_amount: number }[] | null;
  reference: string | null;
  email_required: boolean;
  customer_email_masked: string | null;
  card: { brand: string; last4: string } | null;
  expires_at: string;
  submitted_at: string | null;
  authorised_at: string | null;
  return_url: string;
  verification: Verification | null;
}

// Present while the payment waits for the emailed code (C5).
export interface Verification {
  /** v2: the signed-in shopper can approve with a passkey instead of the code. */
  passkey?: boolean;
  /** v2: false until a code is emailed (a passkey shopper asks for one only if needed). */
  code_sent?: boolean;
  sent_to: string | null;
  code_expires_at: string;
  attempts_remaining: number;
  resend_available_at: string | null; // null: no more codes can be sent
}

export interface CardInput {
  number: string;
  expiry_month: number;
  expiry_year: number;
  cvc: string;
  email?: string;
  privacy_consent: true; // FR-ACC-12: the gateway refuses the card without it
}

const request = jsonClient("/checkout");
const call = (path: string, init?: RequestInit) => request<Session>(path, init);

export const getSession = (sessionId: string) => call(encodeURIComponent(sessionId));

export const addCard = (sessionId: string, card: CardInput) =>
  call(`${encodeURIComponent(sessionId)}/card`, { method: "POST", body: JSON.stringify(card) });

export const submit = (sessionId: string) =>
  call(`${encodeURIComponent(sessionId)}/submit`, { method: "POST", body: "{}" });

export const verify = (sessionId: string, code: string) =>
  call(`${encodeURIComponent(sessionId)}/verify`, { method: "POST", body: JSON.stringify({ code }) });

export const resendCode = (sessionId: string) =>
  call(`${encodeURIComponent(sessionId)}/verify/resend`, { method: "POST", body: "{}" });

// v2 passkeys: approve the payment with the signed-in shopper's passkey instead of a code.
export const passkeyOptions = (sessionId: string) =>
  request<{ options: Record<string, unknown> }>(`${encodeURIComponent(sessionId)}/verify/passkey/options`,
    { method: "POST", body: "{}" });

export const verifyPasskey = (sessionId: string, credential: Record<string, unknown>) =>
  call(`${encodeURIComponent(sessionId)}/verify/passkey`, { method: "POST", body: JSON.stringify({ credential }) });
