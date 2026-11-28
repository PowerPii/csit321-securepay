// Pure helpers and labels for the dashboard (tested in format.test.ts).
import type { PaymentStatus } from "./api/client";

const ZONE = "Asia/Singapore"; // dashboard dates are Singapore time, like the gateway's filters

// v2 multi-currency (docs/v2/FX.md): amounts are integers of the currency's smallest unit;
// JPY and IDR have no minor unit. The merchant is paid in SGD.
const NO_MINOR_UNIT = new Set(["JPY", "IDR"]);

/** 8990 -> "S$ 89.90"; (119200, "THB") -> "THB 1,192.00"; (5000, "JPY") -> "JPY 5,000". */
export function money(amount: number, currency = "SGD"): string {
  const sign = amount < 0 ? "-" : "";
  const abs = Math.abs(amount);
  const prefix = currency === "SGD" ? "S$" : currency;
  if (NO_MINOR_UNIT.has(currency)) return `${sign}${prefix} ${abs.toLocaleString("en-SG")}`;
  const whole = Math.floor(abs / 100).toLocaleString("en-SG");
  return `${sign}${prefix} ${whole}.${String(abs % 100).padStart(2, "0")}`;
}

/** "89.90", "89.9", "89" or "S$ 89.90" -> 8990 (or whole units for JPY/IDR); null if invalid. */
export function parseMoney(text: string, currency = "SGD"): number | null {
  const clean = text.replace(/S\$|[A-Z]{3}|\s|,/gi, "");
  if (NO_MINOR_UNIT.has(currency)) return /^\d+$/.test(clean) ? Number(clean) : null;
  if (!/^\d+(\.\d{1,2})?$/.test(clean)) return null;
  const [whole, frac = ""] = clean.split(".");
  return Number(whole) * 100 + Number(frac.padEnd(2, "0"));
}

/** "2026-11-02T06:52:00Z" -> "2 Nov, 14:52" (Singapore time). */
export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString("en-SG", {
    timeZone: ZONE, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

/** "2 Nov 2026, 14:52" */
export function longDate(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString("en-SG", {
    timeZone: ZONE, day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
    hour12: false,
  });
}

/** "14:52:05" */
export function timeOnly(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString("en-SG", { timeZone: ZONE, hour12: false });
}

export function cardLabel(card: { brand: string; last4: string } | null): string {
  if (!card) return "";
  const brand = { visa: "Visa", mastercard: "MC", amex: "Amex" }[card.brand] ?? card.brand;
  return `${brand} •••• ${card.last4}`;
}

export const STATUS_LABELS: Record<PaymentStatus, string> = {
  created: "Created",
  screening: "Screening",
  verification_required: "Awaiting code",
  processing: "Processing",
  authorised: "Authorised",
  captured: "Captured",
  declined: "Declined",
  blocked: "Blocked",
  unresolved: "Unresolved",
  cancelled: "Cancelled",
  expired: "Expired",
};

/** The status shown on D3/D4: a captured payment with refunds reads "Refunded" or
 *  "Partially refunded", as in the wireframes. */
export function statusLabel(status: PaymentStatus, amount = 0, refunded = 0): string {
  if (status === "captured" && refunded > 0) return refunded >= amount ? "Refunded" : "Partially refunded";
  return STATUS_LABELS[status] ?? status;
}

/** Dark badges for settled money, outlined for everything else (wireframe Badge tones). */
export function statusTone(status: PaymentStatus): "dark" | "neutral" {
  return status === "captured" ? "dark" : "neutral";
}

export function riskLabel(risk: { decision: string; verification: string | null } | null): string {
  if (!risk) return "";
  if (risk.decision === "verify") return `Verify · ${risk.verification ?? "pending"}`;
  return { allow: "Allow", block: "Block", not_screened: "Not screened" }[risk.decision] ?? risk.decision;
}

export const CASE_DECISION_LABELS: Record<string, string> = {
  blocked: "Blocked",
  verify_failed: "Verify failed",
  verify_passed: "Verify passed",
  verify_pending: "Verify pending",
};

/** Why screening flagged an attempt, in words an analyst or merchant can act on. */
export const REASON_LABELS: Record<string, string> = {
  blocklisted_card: "Card on blocklist",
  blocklisted_email: "Email on blocklist",
  blocklisted_ip: "IP address on blocklist",
  review_list_card: "Card on review list",
  review_list_email: "Email on review list",
  review_list_ip: "IP address on review list",
  amount_over_limit: "Amount over the limit",
  card_attempt_burst: "Burst of attempts with this card (10 min)",
  high_amount: "Amount over S$ 500",
  model_score_high: "Model score at or above the block threshold",
  model_score_medium: "Model score at or above the verify threshold",
  screening_unavailable: "Screening unavailable (not a fraud finding)",
};

export function reasonLabel(code: string): string {
  return REASON_LABELS[code] ?? code.replace(/_/g, " ");
}

export const FINDING_LABELS: Record<string, string> = {
  confirmed_fraud: "Confirmed fraud",
  false_positive: "False positive",
  inconclusive: "Inconclusive",
};

export const FEATURE_LABELS: Record<string, string> = {
  amount_cents: "Amount",
  card_attempts_10m: "Card attempts, 10 min",
  card_attempts_1h: "Card attempts, 1 h",
  card_attempts_24h: "Card attempts, 24 h",
  card_blocked_24h: "Card blocked, 24 h",
  card_distinct_emails_24h: "Emails on this card, 24 h",
  card_allowed_30d: "Card allowed, 30 days",
  email_attempts_24h: "Email attempts, 24 h",
  email_distinct_cards_24h: "Cards on this email, 24 h",
  ip_attempts_1h: "IP attempts, 1 h",
  ip_distinct_cards_1h: "Cards from this IP, 1 h",
  card_attempts_7d: "Card attempts, 7 days",
  card_attempts_30d: "Card attempts, 30 days",
  card_seconds_since_last: "Since this card's last attempt",
  card_mean_amount_30d: "Card's average amount, 30 days",
  amount_to_card_mean_30d: "Amount vs card's average",
  bin_attempts_10m: "Card range attempts, 10 min",
  bin_distinct_cards_1h: "Cards in this range, 1 h",
  bin_distinct_ips_1h: "IPs using this range, 1 h",
};

/** Features in schema order (Postgres returns JSON keys sorted by length), unknown ones last. */
export function orderedFeatures(features: Record<string, number>): [string, number][] {
  const order = Object.keys(FEATURE_LABELS);
  const rank = (k: string) => (order.includes(k) ? order.indexOf(k) : order.length);
  return Object.entries(features).sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b));
}

/** A feature value as an analyst reads it: money in S$, gaps as durations, ratios as "x 1.25". */
export function featureValue(name: string, value: number): string {
  if (name === "card_seconds_since_last") {
    if (value < 0) return "No attempt in 30 days";
    if (value < 120) return `${Math.round(value)} s`;
    if (value < 7200) return `${Math.round(value / 60)} min`;
    if (value < 172800) return `${Math.round(value / 3600)} h`;
    return `${Math.round(value / 86400)} days`;
  }
  if (name === "amount_cents" || name === "card_mean_amount_30d") {
    return value > 0 ? money(value) : "–";
  }
  if (name === "amount_to_card_mean_30d") return value > 0 ? `x ${value.toFixed(2)}` : "–";
  return String(value);
}

/** Audit actions as status-history lines (D4). */
const ACTION_LABELS: Record<string, string> = {
  "payment.created": "Created by merchant API",
  "checkout.card_added": "Card entered on the hosted checkout (tokenised in the vault)",
  "payment.submitted": "Submitted and screened",
  "payment.verification_required": "Screening: Verify, code sent",
  "payment.verification_code_resent": "Verification code sent again",
  "payment.verification_failed_attempt": "Wrong verification code",
  "payment.verified": "Verification code passed",
  "payment.authorised": "Authorised by the simulator",
  "payment.declined": "Declined",
  "payment.blocked": "Blocked",
  "payment.unresolved": "No answer from the simulator: unresolved",
  "payment.expired": "Checkout expired",
  "payment.capture_requested": "Capture requested",
  "payment.captured": "Captured",
  "payment.cancel_requested": "Cancel requested",
  "payment.cancelled": "Cancelled",
  "payment.capture_unresolved": "Capture sent, no answer yet",
  "payment.cancel_unresolved": "Cancel sent, no answer yet",
  "payment.capture_not_processed": "Capture not processed by the simulator",
  "payment.cancel_not_processed": "Cancel not processed by the simulator",
  "payment.capture_rejected_by_processor": "Capture refused by the simulator",
  "payment.cancel_rejected_by_processor": "Cancel refused by the simulator",
  "refund.requested": "Refund requested",
  "refund.rejected": "Refund rejected: more than the refundable amount",
  "refund.completed": "Refund completed",
  "refund.failed": "Refund failed",
  "refund.unresolved": "Refund sent, no answer yet",
  "operation.escalated": "Escalated to administrators after status checks",
  "operation.rechecked": "Re-checked with the simulator",
  "operation.recheck_no_answer": "Re-checked: still no answer",
  "review.assigned": "Fraud case assigned",
  "review.unassigned": "Fraud case unassigned",
  "review.finding_recorded": "Fraud finding recorded",
  "review.reopened": "Fraud case reopened",
};

export function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action.replace(/[._]/g, " ");
}

export function actorLabel(actorType: string, actor: string | null): string {
  if (actorType === "merchant_api") return `merchant API${actor ? ` (${actor.slice(0, 8)}…)` : ""}`;
  if (actorType === "staff") return actor ?? "staff";
  if (actorType === "customer") return "customer";
  return actor ? `system (${actor})` : "system";
}

export function titleCase(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1).replace(/_/g, " ");
}
