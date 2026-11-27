import { randomUUID } from "node:crypto";
import { SecurePayAPIError, SecurePayConnectionError, SecurePayError, SecurePaySignatureError } from "./errors.js";
import { checkSignature, REASONS, SIGNATURE_HEADER } from "./signature.js";
import type {
  ApiErrorType,
  CapturePaymentParams,
  CreatePaymentParams,
  CreateRefundParams,
  Balance,
  FxRates,
  CreatePayoutParams,
  Payout,
  PayoutList,
  Payment,
  Refund,
  RefundList,
  SecurePayEvent,
} from "./types.js";
import { VERSION } from "./version.js";
import { constructEvent, type ConstructEventOptions } from "./webhooks.js";

export const DEFAULT_BASE_URL = "https://gateway.securepaysg.me";

export interface SecurePayOptions {
  /**
   * Secret API key from the dashboard: `sp_test_...` for test mode, `sp_live_...` for live
   * mode. Keep it on your server. The key alone decides the mode of everything you create.
   */
  apiKey: string;
  /**
   * Webhook signing secret (`whsec_...`). When set, every reply from the gateway is checked
   * against its `SecurePay-Signature` (a reply that fails throws `SecurePaySignatureError`),
   * and `webhooks.constructEvent` uses it by default. Recommended.
   */
  signingSecret?: string;
  /**
   * Gateway address. Default `https://gateway.securepaysg.me`. Must be `https://`: the API
   * key is sent with every request. Plain `http://` is accepted only for a gateway on this
   * machine (`localhost`, `*.localhost`, `127.0.0.1`, `[::1]`) and only with
   * `allowInsecureHttp: true`.
   */
  baseUrl?: string;
  /** Development only: allow an `http://` `baseUrl` on this machine. Default `false`. */
  allowInsecureHttp?: boolean;
  /** Time limit for one attempt, in milliseconds: 1 to 600,000. Default 30,000. */
  timeoutMs?: number;
  /**
   * Extra attempts after a network failure, a timeout, a 429, a 5xx, or a 409
   * `idempotency_request_in_progress`: a whole number from 0 to 10. Every attempt reuses the
   * same `Idempotency-Key`, so a retry never charges twice. Default 2.
   */
  maxRetries?: number;
  /** A fetch implementation. Default: the global `fetch` (Node.js 20.19 or later). */
  fetch?: typeof fetch;
  /** The API key is a secret: by default the client refuses to run in a web browser. */
  dangerouslyAllowBrowser?: boolean;
}

export interface RequestOptions {
  /**
   * For POST requests: 8 to 255 printable ASCII characters, no spaces. Default: a new UUID v4
   * for this call only, which is lost if your process stops before the reply arrives.
   *
   * Recommended: create one key per operation (one per order for `payments.create`, one per
   * refund for `refunds.create`), save it with that order or refund in your database
   * *before* the call, and send the saved key again on every retry, even after a restart.
   * The gateway keeps a key for 24 hours from its first use and replays the first result.
   */
  idempotencyKey?: string;
  /** Override `timeoutMs` for this call (1 to 600,000 ms). */
  timeoutMs?: number;
  /** Abort the call. */
  signal?: AbortSignal;
}

/** Facts about the HTTP reply behind a returned object. */
export interface ResponseMeta {
  /** HTTP status: 200, 201, or 202 when the simulated bank did not answer in time. */
  status: number;
  /** `SecurePay-Request-Id`. Quote it when asking for support. */
  requestId: string | null;
  /** `true` when the gateway replayed the stored reply to an earlier call with the same key. */
  idempotentReplayed: boolean;
  /** `true` when the reply's signature was checked (that is, `signingSecret` is set). */
  signatureVerified: boolean;
  /** The `Idempotency-Key` sent, for POST requests. */
  idempotencyKey: string | null;
}

/** A returned object, with `lastResponse` (not enumerable, so JSON.stringify leaves it out). */
export type WithResponse<T> = T & { readonly lastResponse: ResponseMeta };

const RETRY_STATUSES = new Set([429, 500, 502, 503, 504]);
const ID_PATTERN = /^[A-Za-z0-9_]+$/;
/** The gateway's rule for `Idempotency-Key` (API contract section 5). */
const IDEMPOTENCY_KEY_PATTERN = /^[\x21-\x7E]{8,255}$/;
const MAX_TIMEOUT_MS = 600_000;
const MAX_RETRIES = 10;
const LOOPBACK_HOSTS = /^(localhost|[a-z0-9-]+(\.[a-z0-9-]+)*\.localhost|127(\.\d{1,3}){3}|\[::1\])$/i;

function checkTimeout(value: unknown, what: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 1 || value > MAX_TIMEOUT_MS) {
    throw new RangeError(`${what} must be a number of milliseconds from 1 to ${MAX_TIMEOUT_MS}; got ${String(value)}.`);
  }
  return value;
}

function checkBaseUrl(raw: unknown, allowInsecureHttp: boolean): string {
  let url: URL;
  try {
    url = new URL(String(raw));
  } catch {
    throw new SecurePayError(`baseUrl is not a valid URL: ${JSON.stringify(raw)}.`);
  }
  if (url.username || url.password) throw new SecurePayError("baseUrl must not contain a user name or password.");
  if (url.search || url.hash) throw new SecurePayError("baseUrl must not contain a query string or #fragment.");
  if (url.protocol === "http:") {
    if (!LOOPBACK_HOSTS.test(url.hostname)) {
      throw new SecurePayError(`baseUrl must use https:// (the API key is sent with every request); got ${url.origin}.`);
    }
    if (!allowInsecureHttp) {
      throw new SecurePayError(`baseUrl ${url.origin} is plain http. For a local development gateway only, also pass allowInsecureHttp: true.`);
    }
  } else if (url.protocol !== "https:") {
    throw new SecurePayError(`baseUrl must use https://; got ${url.protocol}//.`);
  }
  return (url.origin + url.pathname).replace(/\/+$/, "");
}

function pathId(kind: string, id: string): string {
  if (typeof id !== "string" || !ID_PATTERN.test(id)) {
    const example = { paymentId: "pay_...", refundId: "rfd_...", payoutId: "po_..." }[kind] ?? "an ID";
    throw new TypeError(`${kind} must be an ID such as ${example}; got ${JSON.stringify(id)}.`);
  }
  return id;
}

function isBrowser(): boolean {
  const g = globalThis as { window?: unknown; document?: unknown };
  return typeof g.window !== "undefined" && typeof g.document !== "undefined";
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason);
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal?.reason);
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

function retryAfterSeconds(headers: Headers): number | null {
  const raw = headers.get("retry-after");
  if (!raw) return null;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

/** Backoff for attempt n (0-based): 0.5 s, 1 s, 2 s ... with jitter, at most 8 s. */
function backoffMs(attempt: number): number {
  const base = Math.min(8000, 500 * 2 ** attempt);
  return base / 2 + Math.random() * (base / 2);
}

function withMeta<T extends object>(value: T, meta: ResponseMeta): WithResponse<T> {
  Object.defineProperty(value, "lastResponse", { value: meta, enumerable: false });
  return value as WithResponse<T>;
}

/**
 * Client for the SecurePay merchant API. Use it on your server only.
 *
 * ```ts
 * const securepay = new SecurePay({ apiKey: process.env.SECUREPAY_API_KEY!, signingSecret: process.env.SECUREPAY_SIGNING_SECRET });
 * const payment = await securepay.payments.create({ amount: 4590, currency: "SGD", order_reference: "ORD-10042", return_url: "https://shop.example.com/return" });
 * // send the customer to payment.checkout_url
 * ```
 */
export class SecurePay {
  readonly payments: Payments;
  readonly refunds: Refunds;
  readonly balance: BalanceResource;
  readonly fxRates: FxRatesResource;
  /** True for a live key (`sp_live_...`), false for a test key. */
  readonly livemode: boolean;
  readonly payouts: Payouts;
  readonly webhooks: Webhooks;

  readonly #apiKey: string;
  readonly #signingSecret: string | undefined;
  readonly #baseUrl: string;
  readonly #timeoutMs: number;
  readonly #maxRetries: number;
  readonly #fetch: typeof fetch;

  constructor(options: SecurePayOptions) {
    if (!options || typeof options.apiKey !== "string" || !options.apiKey.trim()) {
      throw new SecurePayError("An API key is required: new SecurePay({ apiKey: \"sp_test_...\" }).");
    }
    if (isBrowser() && !options.dangerouslyAllowBrowser) {
      throw new SecurePayError(
        "SecurePay's API key is a secret and must stay on your server. Create payments from your backend, " +
          "then send the customer to checkout_url. (Set dangerouslyAllowBrowser only for local experiments.)",
      );
    }
    const fetchImpl = options.fetch ?? (globalThis.fetch as typeof fetch | undefined);
    if (!fetchImpl) throw new SecurePayError("No fetch implementation found. Use Node.js 20.19 or later, or pass options.fetch.");
    this.#apiKey = options.apiKey.trim();
    this.livemode = this.#apiKey.startsWith("sp_live_");
    this.#signingSecret = options.signingSecret || undefined;
    this.#baseUrl = checkBaseUrl(options.baseUrl ?? DEFAULT_BASE_URL, options.allowInsecureHttp === true);
    this.#timeoutMs = checkTimeout(options.timeoutMs ?? 30_000, "timeoutMs");
    const retries = options.maxRetries ?? 2;
    if (!Number.isInteger(retries) || retries < 0 || retries > MAX_RETRIES) {
      throw new RangeError(`maxRetries must be a whole number from 0 to ${MAX_RETRIES}; got ${String(retries)}.`);
    }
    this.#maxRetries = retries;
    this.#fetch = fetchImpl;
    this.payments = new Payments(this);
    this.refunds = new Refunds(this);
    this.balance = new BalanceResource(this);
    this.fxRates = new FxRatesResource(this);
    this.payouts = new Payouts(this);
    this.webhooks = new Webhooks(this.#signingSecret);
  }

  /** @internal Sends one API call, with retries, signature checks and error mapping. */
  async request<T extends object>(method: "GET" | "POST", path: string, body?: object, options: RequestOptions = {}): Promise<WithResponse<T>> {
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.#apiKey}`,
      Accept: "application/json",
    };
    if (!isBrowser()) headers["User-Agent"] = `securepaysg-node/${VERSION}`;
    let idempotencyKey: string | null = null;
    let payload: string | undefined;
    if (method === "POST") {
      if (options.idempotencyKey !== undefined && (typeof options.idempotencyKey !== "string" || !IDEMPOTENCY_KEY_PATTERN.test(options.idempotencyKey))) {
        throw new TypeError(`idempotencyKey must be 8 to 255 printable ASCII characters without spaces; got ${JSON.stringify(options.idempotencyKey)}.`);
      }
      idempotencyKey = options.idempotencyKey ?? randomUUID();
      headers["Idempotency-Key"] = idempotencyKey;
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body ?? {});
    }
    const url = this.#baseUrl + path;
    const timeoutMs = options.timeoutMs === undefined ? this.#timeoutMs : checkTimeout(options.timeoutMs, "timeoutMs");

    for (let attempt = 0; ; attempt++) {
      const last = attempt >= this.#maxRetries;
      const timeout = new AbortController();
      const timer = setTimeout(() => timeout.abort(new Error(`timed out after ${timeoutMs} ms`)), timeoutMs);
      const signal = options.signal ? AbortSignal.any([options.signal, timeout.signal]) : timeout.signal;
      let response: Response;
      let text: string;
      try {
        response = await this.#fetch(url, { method, headers, body: payload, signal, redirect: "error" });
        text = await response.text();
      } catch (cause) {
        if (options.signal?.aborted) throw cause;
        if (!last) {
          await sleep(backoffMs(attempt), options.signal);
          continue;
        }
        const what = timeout.signal.aborted ? `timed out after ${timeoutMs} ms` : "failed";
        const keyNote = idempotencyKey ? ` The gateway may have done it: retry with Idempotency-Key ${idempotencyKey}.` : "";
        throw new SecurePayConnectionError(`${method} ${path} ${what} (${attempt + 1} attempt${attempt ? "s" : ""}).${keyNote}`, {
          cause,
          idempotencyKey,
          attempts: attempt + 1,
        });
      } finally {
        clearTimeout(timer);
      }

      const parsed = parseJson(text);
      const errorCode = errorBody(parsed)?.code;
      const retryable = RETRY_STATUSES.has(response.status) || (response.status === 409 && errorCode === "idempotency_request_in_progress");
      if (retryable && !last) {
        const wait = retryAfterSeconds(response.headers);
        await sleep(wait !== null ? Math.min(wait, 60) * 1000 : backoffMs(attempt), options.signal);
        continue;
      }

      const requestId = response.headers.get("securepay-request-id");
      const verified = this.#checkReply(response, text, requestId, idempotencyKey);
      if (!response.ok) throw this.#apiError(response, parsed, requestId, idempotencyKey);
      if (!parsed || typeof parsed !== "object") {
        throw new SecurePayAPIError({
          message: `The gateway sent a reply that is not JSON (HTTP ${response.status}).`,
          status: response.status,
          type: "unknown",
          code: "invalid_response",
          requestId,
          body: text,
          idempotencyKey,
        });
      }
      return withMeta(parsed as T, {
        status: response.status,
        requestId,
        idempotentReplayed: response.headers.get("idempotent-replayed") === "true",
        signatureVerified: verified,
        idempotencyKey,
      });
    }
  }

  /** Checks the reply's signature when a signing secret is set. 401 replies are never signed. */
  #checkReply(response: Response, text: string, requestId: string | null, idempotencyKey: string | null): boolean {
    if (!this.#signingSecret) return false;
    const header = response.headers.get(SIGNATURE_HEADER);
    // Unsigned 5xx replies can come from the proxy in front of the gateway (for example while it restarts).
    if (!header && (response.status === 401 || response.status >= 500)) return false;
    const result = checkSignature(text, header, this.#signingSecret);
    if (!result.ok) {
      throw new SecurePaySignatureError(`Reply to request ${requestId ?? "(no request ID)"} failed the signature check: ${REASONS[result.reason]}`, {
        idempotencyKey,
        outcomeUnknown: true,
      });
    }
    return true;
  }

  #apiError(response: Response, parsed: unknown, requestId: string | null, idempotencyKey: string | null): SecurePayAPIError {
    const err = errorBody(parsed);
    return new SecurePayAPIError({
      message: err?.message ?? `HTTP ${response.status} from the gateway.`,
      status: response.status,
      type: (err?.type as ApiErrorType | undefined) ?? "unknown",
      code: err?.code ?? `http_${response.status}`,
      param: err?.param ?? null,
      requestId: err?.request_id ?? requestId,
      retryAfter: retryAfterSeconds(response.headers),
      body: parsed,
      idempotencyKey,
    });
  }
}

function parseJson(text: string): unknown {
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

function errorBody(parsed: unknown): { type?: string; code?: string; message?: string; param?: string | null; request_id?: string } | undefined {
  if (parsed && typeof parsed === "object" && "error" in parsed) {
    const e = (parsed as { error: unknown }).error;
    if (e && typeof e === "object") return e as never;
  }
  return undefined;
}

/** `securepay.payments` */
export class Payments {
  readonly #client: SecurePay;
  constructor(client: SecurePay) {
    this.#client = client;
  }

  /**
   * Creates a payment and returns it with a `checkout_url` (valid for 1 hour). Send the
   * customer there. Never fulfil an order on the return to `return_url`: wait for the
   * `payment.authorised` webhook or call `retrieve`.
   */
  async create(params: CreatePaymentParams, options?: RequestOptions): Promise<WithResponse<Payment>> {
    return this.#client.request<Payment>("POST", "/v1/payments", params, options);
  }

  /** Gets a payment's current status (a signed reply). */
  async retrieve(paymentId: string, options?: Omit<RequestOptions, "idempotencyKey">): Promise<WithResponse<Payment>> {
    return this.#client.request<Payment>("GET", `/v1/payments/${pathId("paymentId", paymentId)}`, undefined, options);
  }

  /**
   * Captures an authorised payment: all of it, or `{ amount }` cents of it (once; the rest of
   * the authorisation is released). A 202 reply (`lastResponse.status`) means the simulated
   * bank did not answer yet: `pending_operation` is set and a `payment.captured` webhook
   * follows. Do not retry it with a new idempotency key.
   */
  async capture(paymentId: string, params: CapturePaymentParams = {}, options?: RequestOptions): Promise<WithResponse<Payment>> {
    return this.#client.request<Payment>("POST", `/v1/payments/${pathId("paymentId", paymentId)}/capture`, params, options);
  }

  /** Cancels (voids) an authorised payment. A captured payment must be refunded instead. */
  async cancel(paymentId: string, options?: RequestOptions): Promise<WithResponse<Payment>> {
    return this.#client.request<Payment>("POST", `/v1/payments/${pathId("paymentId", paymentId)}/cancel`, {}, options);
  }
}

/** `securepay.refunds` */
export class Refunds {
  readonly #client: SecurePay;
  constructor(client: SecurePay) {
    this.#client = client;
  }

  /**
   * Refunds all or part of a captured payment. `status` is `completed` or `failed` when the
   * simulated bank answered, `unresolved` when it did not (the amount stays reserved and a
   * `refund.*` webhook follows).
   */
  async create(paymentId: string, params: CreateRefundParams, options?: RequestOptions): Promise<WithResponse<Refund>> {
    return this.#client.request<Refund>("POST", `/v1/payments/${pathId("paymentId", paymentId)}/refunds`, params, options);
  }

  /** Lists a payment's refunds, oldest first, with what is still refundable. */
  async list(paymentId: string, options?: Omit<RequestOptions, "idempotencyKey">): Promise<WithResponse<RefundList>> {
    return this.#client.request<RefundList>("GET", `/v1/payments/${pathId("paymentId", paymentId)}/refunds`, undefined, options);
  }

  /** Gets one refund (a signed reply). */
  async retrieve(refundId: string, options?: Omit<RequestOptions, "idempotencyKey">): Promise<WithResponse<Refund>> {
    return this.#client.request<Refund>("GET", `/v1/refunds/${pathId("refundId", refundId)}`, undefined, options);
  }
}

/** `securepay.balance` */
export class BalanceResource {
  readonly #client: SecurePay;
  constructor(client: SecurePay) {
    this.#client = client;
  }

  /** What you can pay out now (`available`), what is held in reserve, and the next release. */
  async retrieve(options?: Omit<RequestOptions, "idempotencyKey">): Promise<WithResponse<Balance>> {
    return this.#client.request<Balance>("GET", "/v1/balance", undefined, options);
  }
}

/** `securepay.fxRates` (v2 multi-currency) */
export class FxRatesResource {
  readonly #client: SecurePay;
  constructor(client: SecurePay) {
    this.#client = client;
  }

  /**
   * The rate sheet a payment created now locks: SGD per one unit of each other currency, and
   * the FX fee. Payments already made keep their own rate (`payment.settlement.fx`).
   */
  async retrieve(options?: Omit<RequestOptions, "idempotencyKey">): Promise<WithResponse<FxRates>> {
    return this.#client.request<FxRates>("GET", "/v1/fx_rates", undefined, options);
  }
}

/** `securepay.payouts` */
export class Payouts {
  readonly #client: SecurePay;
  constructor(client: SecurePay) {
    this.#client = client;
  }

  /**
   * Pays out from the available balance to your bank account (at least S$1.00; more than is
   * available is a `422 insufficient_balance`). Save the idempotency key with your payout
   * record before the call, as for refunds.
   */
  async create(params: CreatePayoutParams, options?: RequestOptions): Promise<WithResponse<Payout>> {
    return this.#client.request<Payout>("POST", "/v1/payouts", params, options);
  }

  /** The newest payouts first; `limit` is 1 to 100 (default 10). */
  async list(params: { limit?: number } = {}, options?: Omit<RequestOptions, "idempotencyKey">): Promise<WithResponse<PayoutList>> {
    const q = params.limit === undefined ? "" : `?limit=${encodeURIComponent(String(params.limit))}`;
    return this.#client.request<PayoutList>("GET", `/v1/payouts${q}`, undefined, options);
  }

  /** Gets one payout (a signed reply). */
  async retrieve(payoutId: string, options?: Omit<RequestOptions, "idempotencyKey">): Promise<WithResponse<Payout>> {
    return this.#client.request<Payout>("GET", `/v1/payouts/${pathId("payoutId", payoutId)}`, undefined, options);
  }
}

/** `securepay.webhooks` */
export class Webhooks {
  readonly #secret: string | undefined;
  constructor(secret: string | undefined) {
    this.#secret = secret;
  }

  /**
   * Verifies a webhook with the client's `signingSecret` (or the `secret` option) and returns
   * the event. Throws `SecurePaySignatureError` when the check fails.
   */
  constructEvent(
    payload: string | Uint8Array,
    signatureHeader: string | null | undefined,
    options: ConstructEventOptions & { secret?: string } = {},
  ): SecurePayEvent {
    const secret = options.secret ?? this.#secret;
    if (!secret) throw new SecurePayError("No signing secret: pass signingSecret to new SecurePay(...) or { secret } here.");
    return constructEvent(payload, signatureHeader, secret, options);
  }
}
