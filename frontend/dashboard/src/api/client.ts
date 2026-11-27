// Thin wrapper over the gateway's staff API (/internal/*). Session cookie + CSRF token;
// the backend is the authority on permissions and merchant scoping.
export type PaymentStatus =
  | "created" | "screening" | "verification_required" | "processing" | "authorised"
  | "captured" | "declined" | "blocked" | "unresolved" | "cancelled" | "expired";

export type Role = "merchant_operator" | "fraud_analyst" | "administrator";

export interface ErrorBody {
  error?: { type: string; code: string; message: string; param: string | null };
}

export class ApiError extends Error {
  constructor(public status: number, public body: ErrorBody | null) {
    super(body?.error?.message ?? `Request failed (${status})`);
  }
  get code(): string | undefined {
    return this.body?.error?.code;
  }
}

function csrfToken(): string {
  // Read on every request: signing in rotates the token.
  return document.cookie.match(/(?:^|; )csrftoken=([^;]+)/)?.[1] ?? "";
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (init.method && init.method !== "GET" && !csrfToken()) {
    // First POST of a visit (sign-in): GET /me sets the CSRF cookie, even when it answers 401.
    await fetch("/internal/me", { credentials: "same-origin" }).catch(() => undefined);
  }
  const res = await fetch(`/internal${path}`, {
    credentials: "same-origin",
    ...init,
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken(), ...init.headers },
  });
  const body = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, body);
  return body as T;
}

export function post<T>(path: string, body: unknown = {}): Promise<T> {
  return api<T>(path, { method: "POST", body: JSON.stringify(body) });
}

export function query(params: Record<string, string | number | undefined | null>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

// --- shapes --------------------------------------------------------------------------------

export interface Staff {
  id: string;
  email: string;
  name: string;
  role: Role;
  role_label: string;
  merchant: { id: string; name: string; status?: Merchant["status"] } | null;
  idle_minutes?: number; // FR-ACC-10: signed out after this many idle minutes
  idle_warning_minutes?: number; // ... and warned this many minutes in
  backup_codes_left?: number; // one-time backup codes for two-step verification
  backup_codes?: string[]; // only in the reply that finishes first-time setup, shown once
}

export interface Card { brand: string; last4: string }
export interface MerchantRef { id: string; name: string }

export interface PaymentRow {
  id: string;
  livemode?: boolean;
  reference: string;
  order_reference: string;
  merchant: MerchantRef;
  amount: number;
  currency: string;
  settlement_amount?: number; // v2: SGD cents
  status: PaymentStatus;
  refunded_amount: number;
  card: Card | null;
  risk: { decision: string; verification: string | null } | null;
  customer: string | null;
  outcome_reason: string | null;
  created_at: string;
}

export interface Page<T> {
  count: number;
  page: number;
  page_size: number;
  data: T[];
}

export interface PaymentList extends Page<PaymentRow> {
  summary: {
    awaiting_capture: number;
    unresolved: number;
    blocked_today: number;
    refunded_this_month: number;
    escalated: number | null;
  };
}

/** One reason behind a decision (apps.risk.explain). Model factors carry their TreeSHAP effect
 *  in log-odds: positive raised the score, negative lowered it. */
export interface ExplanationFactor {
  source: "rule" | "list" | "limit" | "model";
  code?: string;
  feature?: string;
  value?: number;
  effect?: number;
  direction: "raises" | "lowers";
  text: string;
}

/** Analysts and administrators get the factors; merchant operators get broad areas only. */
export type Explanation =
  | { headline: string; method: "tree-shap" | "rules-only"; base_score: number | null; factors: ExplanationFactor[] }
  | { areas: string[] };

export interface Screening {
  id: string;
  outcome: string;
  score: number | null;
  reasons: string[];
  features: Record<string, number>;
  model_version: string | null;
  rules_version: string | null;
  policy_version: string | null;
  feature_schema_version: string | null;
  thresholds: { verify: number; block: number } | null;
  elapsed_ms: number;
  error: string | null;
  occurred_at: string;
  explanation?: Explanation | null;
}

/** What a merchant operator gets instead: the decision and broad reasons, no screening internals. */
export interface MerchantScreening {
  id: string;
  outcome: string;
  occurred_at: string;
  merchant_view: true;
  explanation: { areas: string[] } | null;
}

// v2: how an operation crossed the card network (contracts/NETWORK-CONTRACT.md section 5).
export interface NetworkHop {
  node: "acquirer" | "switch" | "issuer";
  mti_in: string;
  mti_out: string;
  response_code: string;
  elapsed_ms: number;
}

export interface NetworkTrace {
  stan: string;
  rrn: string;
  auth_code: string | null;
  response_code: string;
  response_text: string;
  trace: NetworkHop[];
}

export interface Operation {
  id: string;
  type: string;
  status: string;
  amount: number;
  simulator_reference: string | null;
  reconciliation_checks: number;
  last_checked_at: string | null;
  next_check_at: string | null;
  escalated_at: string | null;
  created_at: string;
  completed_at: string | null;
  network: NetworkTrace | null;
}

export interface Refund {
  id: string;
  reference: string;
  amount: number;
  status: string;
  reason: string | null;
  failure_reason: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface TimelineEntry {
  at: string;
  action: string;
  actor_type: string;
  actor: string | null;
  target_type: string;
  target_id: string;
  details: Record<string, unknown>;
}

/** FR-TXN-16: one row of the payment's correlated timeline. */
export interface TrailEntry {
  at: string;
  kind: "payment" | "screening" | "operation" | "refund" | "webhook" | "email" | "audit";
  id: string;
  title: string;
  status: string | null;
  detail?: string | null;
  request_id: string | null;
  actor_type?: string;
  correlation_id: string;
}

export interface Webhook {
  id: string;
  type: string;
  sequence: number;
  status: string;
  attempts: number;
  last_http_status: number | null;
  last_error: string | null;
  next_attempt_at: string | null;
  delivered_at: string | null;
}

/** Webhook delivery log (extends FR-TXN-07): one event and, in the detail, every attempt. */
export interface WebhookEventRow extends Webhook {
  payment_id: string;
  merchant_id: string;
  merchant_name: string;
  resends: number;
  last_attempt_at: string | null;
  created_at: string;
}

export interface WebhookAttemptRow {
  number: number;
  at: string;
  http_status: number | null;
  error: string | null;
  duration_ms: number;
  succeeded: boolean;
  response_excerpt: string | null;
  manual: boolean;
}

export interface WebhookEventDetail extends WebhookEventRow {
  endpoint: string | null;
  endpoint_approved: boolean;
  payload: Record<string, unknown>;
  attempt_log: WebhookAttemptRow[];
  can_resend: boolean;
  max_resends: number;
}

export interface WebhookList extends Page<WebhookEventRow> {
  counts: Record<"pending" | "delivered" | "undelivered", number>;
  endpoint: { url: string | null; approved: boolean } | null;
  event_types: string[];
}

export interface PaymentDetail {
  id: string;
  reference: string;
  order_reference: string;
  status: PaymentStatus;
  amount: number;
  currency: string;
  // v2 multi-currency: what the merchant is paid, in SGD cents, at the locked rate.
  settlement?: {
    currency: "SGD";
    amount: number;
    captured_amount: number;
    fx: { rate: string; rate_sheet: string; fee_percent: string } | null;
  };
  line_items: { name: string; quantity: number; unit_amount: number }[] | null;
  captured_amount: number;
  refundable_amount: number;
  refunded_amount: number;
  refund_reserved_amount: number;
  card: { token: string; brand: string; last4: string } | null;
  outcome: { reason: string; message: string } | null;
  pending_operation: { type: string; status: string; requested_at: string } | null;
  authorisation_expires_at: string | null;
  created_at: string;
  authorised_at: string | null;
  captured_at: string | null;
  cancelled_at: string | null;
  merchant: MerchantRef;
  customer: string | null;
  description: string | null;
  screening: Screening | MerchantScreening | null;
  screening_decision: string | null;
  verification: string | null;
  operations: Operation[];
  refunds: Refund[];
  webhooks: Webhook[];
  timeline: TimelineEntry[];
  correlation_id: string;
  trail: TrailEntry[];
  actions: { capture: boolean; cancel: boolean; refund: boolean; recheck: boolean; review: boolean };
}

export interface Review {
  status: "open" | "closed";
  assigned_to: { id: string; name: string } | null;
  finding: string | null;
  notes: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
}

export interface CaseRow {
  id: string;
  payment: { id: string; reference: string; amount: number; currency: string; status: PaymentStatus };
  merchant: MerchantRef;
  score: number | null;
  outcome: string;
  decision: string;
  reasons: string[];
  headline?: string | null;
  occurred_at: string;
  review: Review;
}

export interface CaseList extends Page<CaseRow> {
  counts: { open: number; mine: number; closed: number };
}

export interface LinkCount { attempts: number; merchants: number; blocked: number }

export interface CaseDetail extends CaseRow {
  payment: CaseRow["payment"] & {
    order_reference: string;
    card: Card | null;
    customer: string | null;
    outcome_reason: string | null;
    created_at: string;
  };
  screening: Screening;
  linked_activity: { window_hours: number; card: LinkCount | null; email: LinkCount | null; ip: LinkCount | null };
  related: {
    case_id: string | null;
    payment: { id: string; reference: string; status: PaymentStatus };
    merchant: string;
    amount: number;
    outcome: string;
    score: number | null;
    shared: string[];
    occurred_at: string;
  }[];
}

export interface Merchant {
  id: string;
  name: string;
  status: "pending" | "active" | "rejected" | "suspended" | "deactivated";
  status_reason?: string | null;
  webhook_url: string | null;
  webhook_url_approved: boolean;
  max_amount_cents: number | null;
  api_key: { id: string; hint: string; last_used_at: string | null } | null;
  created_at: string;
  credentials?: { api_key: string; signing_secret: string };
  self_registered?: boolean;
  registration?: {
    registration_number: string;
    website: string;
    contact_name: string;
    contact_email: string;
    email_verified_at: string | null;
  } | null;
}

/** The signed-in operator's own merchant (D8). */
export type KeyMode = "test" | "live";

export interface OwnApiKey {
  id: string;
  mode?: KeyMode;
  hint: string;
  created_at: string;
  last_used_at: string | null;
}

export interface OwnMerchant {
  id: string;
  name: string;
  status: Merchant["status"];
  registration_number: string | null;
  webhook_url: string | null;
  webhook_url_approved: boolean;
  /** The test key (`sp_test_`), as in v1. */
  api_key: OwnApiKey | null;
  /** v2: the live key (`sp_live_`). Older gateways leave it out. */
  live_api_key?: OwnApiKey | null;
  can_generate_key: boolean;
  can_generate_live_key?: boolean;
  /** `signing_secret` is null when a second key was generated: the shared secret is kept. */
  credentials?: { api_key: string; signing_secret: string | null };
  revoked_keys?: { id: string; mode?: KeyMode; hint: string; created_at: string; revoked_at: string }[];
}

export interface AuditRow {
  id: number;
  at: string;
  action: string;
  actor_type: string;
  actor_id: string | null;
  merchant_id: string | null;
  target_type: string | null;
  target_id: string | null;
  request_id: string | null;
  details: Record<string, unknown>;
}

/** FR-FRD-08: one alert per flagged payment; read state is per staff member. */
export interface AlertRow {
  id: string;
  created_at: string;
  read: boolean;
  case: CaseRow;
}

export interface AlertList extends Page<AlertRow> {
  unread: number;
}

/** FR-FRD-10: counts for a period, by decision, risk level, finding and reason, with a trend. */
export interface FraudReport {
  generated_at: string;
  period: { from: string; to: string; interval: "day" | "week"; time_zone: string };
  merchant: MerchantRef | null;
  merchants: MerchantRef[];
  totals: {
    screened: number; allowed: number; flagged: number; blocked: number; verify: number; not_screened: number;
    flagged_amount: number; blocked_amount: number; flag_rate: number;
  };
  decisions: { blocked: number; verify_failed: number; verify_passed: number; verify_pending: number };
  risk_levels: { level: string; label: string; count: number }[];
  findings: { confirmed_fraud: number; false_positive: number; inconclusive: number; unreviewed: number };
  top_reasons: { reason: string; count: number }[];
  trend: { start: string; screened: number; flagged: number; blocked: number; confirmed_fraud: number }[];
}

/** FR-ADM-08: a security alert for administrators; repeats are grouped with a count. */
export interface SecurityAlert {
  id: string;
  kind: string;
  kind_label: string;
  severity: "low" | "medium" | "high";
  source: string;
  merchant_id: string | null;
  summary: string;
  details: Record<string, unknown>;
  count: number;
  first_seen_at: string;
  last_seen_at: string;
  status: "open" | "resolved";
  resolved_by: string | null;
  resolved_at: string | null;
  resolution_note: string | null;
  related_audit?: AuditRow[];
}

export interface SecurityAlertList extends Page<SecurityAlert> {
  open: number;
}

/** FR-ADM-02 to FR-ADM-05: a staff account as administrators manage it. */
export interface StaffRow {
  id: string;
  name: string;
  email: string;
  role: Role;
  role_label: string;
  merchant: MerchantRef | null;
  status: "active" | "suspended" | "deactivated";
  status_reason: string | null;
  status_changed_at: string | null;
  last_sign_in_at: string | null;
  created_at: string;
}

export interface StaffList {
  data: StaffRow[];
  roles: { value: Role; label: string }[];
}

/** FR-ADM-07, FR-ADM-12: limits an administrator sets. */
export interface LimitValues {
  max_amount_cents: number;
  api_key_requests_per_minute: number;
  ip_requests_per_minute: number;
}

export interface Limits {
  effective: LimitValues;
  defaults: LimitValues;
  bounds: { max_amount_cents: [number, number]; requests_per_minute: [number, number] };
  updated_at: string | null;
  updated_by: string | null;
}

/** FR-FRD-02: one fraud rule (current revision unless listed as history). */
export interface FraudRule {
  id: string;
  revision: number;
  code: string;
  description: string | null;
  feature: string;
  op: "gt" | "gte" | "lt" | "lte" | "eq";
  value: number;
  action: "block" | "verify";
  enabled: boolean;
  condition: string;
  created_at: string;
  created_by: string | null;
  superseded_at: string | null;
  ruleset_version: string;
}

export interface RuleConflict { rule_id: string; code: string; kind: string; message: string }

export interface RuleSet {
  version: string;
  frozen: { since: string; by: string; note: string } | null;
  rules: FraudRule[];
  features: string[];
  ops: { value: string; label: string }[];
  fixed: string[];
}

/** FR-FRD-09: a block list entry (masked). */
export interface BlocklistEntry {
  id: number;
  kind: "card" | "email" | "ip";
  action: string;
  display: string;
  reason: string;
  active: boolean;
  added_by: string | null;
  source_case: string | null;
  created_at: string;
  removed_by: string | null;
  removed_at: string | null;
  removal_reason: string | null;
}

/** FR-FRD-06: screened attempts sharing a card, email or IP. */
export interface HistorySearch extends Page<{
  case_id: string | null;
  payment: { id: string; reference: string; status: PaymentStatus };
  merchant: MerchantRef;
  amount: number;
  card: string | null;
  outcome: string;
  score: number | null;
  reasons: string[];
  occurred_at: string;
}> {
  by: "card" | "email" | "ip";
  searched: string;
  summary: { attempts: number; merchants: number; blocked: number; flagged: number };
  blocklisted: BlocklistEntry | null;
}

// Integrity, keys, self-checks and clean-up (FR-ADM-09, FR-ADM-14, FR-ADM-15, NFR-PRI-01).
export type RunKind = "integrity" | "self_check" | "retention";

export interface SelfCheckResult {
  id: string;
  name: string;
  practice: string;
  status: "passed" | "failed" | "not_checked";
  detail: string;
}

export interface MaintenanceRun {
  id: string;
  kind: RunKind;
  kind_label: string;
  outcome: "passed" | "problems" | "error";
  started_at: string;
  finished_at: string;
  started_by: string | null;
  summary?: {
    checks?: SelfCheckResult[];
    counts?: Record<string, number>;
    payments?: { checked: number; baselined: number; problems: number; findings: { payment_id: string; reference: string }[] };
    audit?: { checked: number; unsealed_recent: number; problems: number; counts: Record<string, number>;
      findings: { problem: string; position?: number; event_id?: number }[] };
    older_than?: string;
    error?: string;
  };
}

export interface DataKeyRow {
  id: string;
  purpose: "vault" | "field";
  purpose_label: string;
  status: "active" | "decrypt_only";
  created_at: string;
  created_by: string | null;
  retired_at: string | null;
  age_days: number;
  rotate_by: string | null;
  overdue: boolean;
  due_soon: boolean;
}

export interface IntegrityOverview {
  runs: Record<RunKind, MaintenanceRun | null>;
  schedule: Record<RunKind, { every_hours: number; next_at: string | null }>;
  keys: DataKeyRow[];
  key_max_age_days: number;
}

// --- v2 ledger (balances, entries, payouts) -----------------------------------------------

export interface LedgerRules {
  fee_percent_bps: number;
  fee_fixed: number;
  reserve_bps: number;
  reserve_hold_days: number;
  payout_minimum: number;
}

export interface MerchantBalanceRow {
  merchant_id: string;
  merchant_name: string;
  available: number;
  reserve: number;
}

export interface MerchantBalance extends MerchantBalanceRow {
  object: "balance";
  scope: "merchant";
  currency: string;
  next_release: { amount: number; at: string; payment_id: string | null } | null;
  rules: LedgerRules;
}

export interface GatewayBalance {
  object: "balance";
  scope: "gateway";
  currency: string;
  network_clearing: number;
  fee_revenue: number;
  merchants_available: number;
  merchants_reserve: number;
  entries: number;
  check: { ok: boolean; problems: string[] };
  merchants: MerchantBalanceRow[];
  rules: LedgerRules;
}

export type Balance = MerchantBalance | GatewayBalance;

export interface LedgerLine {
  account: "network_clearing" | "fee_revenue" | "merchant_available" | "merchant_reserve";
  memo: string;
  amount: number;
  currency: string;
}

export interface LedgerEntry {
  id: string;
  kind: "capture" | "refund" | "reserve_release" | "payout";
  description: string;
  merchant_id: string;
  merchant_name: string;
  payment_id: string | null;
  payment_reference: string | null;
  refund_id: string | null;
  payout_id: string | null;
  occurred_at: string;
  posted_at: string;
  backfilled: boolean;
  lines: LedgerLine[];
}

export interface LedgerEntryList extends Page<LedgerEntry> {
  kinds: string[];
}

export interface Payout {
  id: string;
  amount: number;
  currency: string;
  status: "paid";
  merchant_id: string;
  created_at: string;
}
