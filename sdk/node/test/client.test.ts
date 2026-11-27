import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  SecurePay,
  SecurePayAPIError,
  SecurePayConnectionError,
  SecurePayError,
  SecurePaySignatureError,
  VERSION,
  type CreatePaymentParams,
} from "../src/index.js";
import { example, fakeFetch, SECRET } from "./helpers.js";

const PARAMS: CreatePaymentParams = {
  amount: 4590,
  currency: "SGD",
  order_reference: "ORD-10042",
  return_url: "https://shop.example.com/orders/ORD-10042/return",
  line_items: [
    { name: "Wireless mouse", quantity: 1, unit_amount: 2990 },
    { name: "Cable tie (pack)", quantity: 2, unit_amount: 800 },
  ],
};

function client(replies: Parameters<typeof fakeFetch>[0], extra: Partial<ConstructorParameters<typeof SecurePay>[0]> = {}) {
  const f = fakeFetch(replies);
  const sp = new SecurePay({ apiKey: "sp_test_abc", signingSecret: SECRET, baseUrl: "https://gw.test/", fetch: f.impl, maxRetries: 2, ...extra });
  return { sp, calls: f.calls };
}

test("VERSION matches package.json", () => {
  assert.equal(VERSION, JSON.parse(readFileSync("package.json", "utf8")).version);
});

test("payments.create sends the key, an idempotency key and the body, and returns the payment", async () => {
  const { sp, calls } = client([{ status: 201, body: example("api/create-payment.201.json") }]);
  const payment = await sp.payments.create(PARAMS);
  assert.equal(payment.status, "created");
  assert.ok(payment.checkout_url);
  assert.equal(calls.length, 1);
  const c = calls[0]!;
  assert.equal(c.url, "https://gw.test/v1/payments");
  assert.equal(c.init.method, "POST");
  assert.equal(c.headers["authorization"], "Bearer sp_test_abc");
  assert.match(c.headers["idempotency-key"]!, /^[0-9a-f-]{36}$/);
  assert.equal(c.headers["content-type"], "application/json");
  assert.equal(c.headers["user-agent"], `securepaysg-node/${VERSION}`);
  assert.deepEqual(JSON.parse(String(c.init.body)), PARAMS);
  assert.equal(c.init.redirect, "error");
  assert.deepEqual(payment.lastResponse, {
    status: 201,
    requestId: "req_TEST00000000000000000001",
    idempotentReplayed: false,
    signatureVerified: true,
    idempotencyKey: c.headers["idempotency-key"],
  });
  assert.equal("lastResponse" in JSON.parse(JSON.stringify(payment)), false);
});

test("uses the caller's idempotency key and reports a replay", async () => {
  const { sp, calls } = client([{ status: 201, body: example("api/create-payment.201.json"), headers: { "Idempotent-Replayed": "true" } }]);
  const payment = await sp.payments.create(PARAMS, { idempotencyKey: "order-ORD-10042-create" });
  assert.equal(calls[0]!.headers["idempotency-key"], "order-ORD-10042-create");
  assert.equal(payment.lastResponse.idempotentReplayed, true);
});

test("each endpoint calls the right method and path", async () => {
  const pay = example("api/get-payment-authorised.200.json");
  const refund = example("api/create-refund-unresolved.201.json");
  const list = example("api/list-refunds.200.json");
  const { sp, calls } = client([
    { status: 200, body: pay },
    { status: 200, body: pay },
    { status: 202, body: example("api/capture-no-answer.202.json") },
    { status: 200, body: pay },
    { status: 201, body: refund },
    { status: 200, body: list },
    { status: 200, body: refund },
  ]);
  const id = "pay_EXAMPLE00000000000000001";
  await sp.payments.retrieve(id);
  await sp.payments.capture(id);
  const partial = await sp.payments.capture(id, { amount: 1000 });
  await sp.payments.cancel(id);
  await sp.refunds.create(id, { amount: 1500, reason: "One item returned" });
  const refunds = await sp.refunds.list(id);
  await sp.refunds.retrieve("rfd_EXAMPLE00000000000000001");
  assert.deepEqual(
    calls.map((c) => `${c.init.method} ${c.url.replace("https://gw.test", "")} ${c.init.body ?? ""}`),
    [
      `GET /v1/payments/${id} `,
      `POST /v1/payments/${id}/capture {}`,
      `POST /v1/payments/${id}/capture {"amount":1000}`,
      `POST /v1/payments/${id}/cancel {}`,
      `POST /v1/payments/${id}/refunds {"amount":1500,"reason":"One item returned"}`,
      `GET /v1/payments/${id}/refunds `,
      "GET /v1/refunds/rfd_EXAMPLE00000000000000001 ",
    ],
  );
  assert.equal(calls[0]!.headers["idempotency-key"], undefined);
  assert.equal(partial.lastResponse.status, 202);
  assert.equal(partial.pending_operation?.type, "capture");
  assert.equal(refunds.object, "list");
});

test("balance and payouts call the right method and path", async () => {
  const payoutText = example("api/create-payout.201.json");
  const payout = JSON.parse(payoutText) as { id: string; amount: number };
  const listText = JSON.stringify({ object: "list", has_more: false, data: [payout] });
  const { sp, calls } = client([
    { status: 200, body: example("api/get-balance.200.json") },
    { status: 201, body: payoutText },
    { status: 200, body: listText },
    { status: 200, body: listText },
    { status: 200, body: payoutText },
  ]);
  const balance = await sp.balance.retrieve();
  const made = await sp.payouts.create({ amount: 5000 }, { idempotencyKey: "payout-2026-11-27-1" });
  await sp.payouts.list();
  const listed = await sp.payouts.list({ limit: 5 });
  await sp.payouts.retrieve(payout.id);
  assert.deepEqual(
    calls.map((c) => `${c.init.method} ${c.url.replace("https://gw.test", "")} ${c.init.body ?? ""}`),
    [
      "GET /v1/balance ",
      `POST /v1/payouts {"amount":5000}`,
      "GET /v1/payouts ",
      "GET /v1/payouts?limit=5 ",
      `GET /v1/payouts/${payout.id} `,
    ],
  );
  assert.equal(calls[1]!.headers["idempotency-key"], "payout-2026-11-27-1");
  assert.equal(balance.available, 10422);
  assert.equal(balance.next_release?.amount, 1200);
  assert.equal(made.status, "paid");
  assert.equal(listed.data[0]!.amount, 5000);
});

test("refuses IDs that would change the path", async () => {
  const { sp, calls } = client([]);
  await assert.rejects(() => sp.payments.retrieve("../refunds/x"), TypeError);
  await assert.rejects(() => sp.refunds.retrieve(""), TypeError);
  await assert.rejects(() => sp.payouts.retrieve("po_x/../../payments"), /payoutId must be an ID such as po_/);
  assert.equal(calls.length, 0);
});

test("maps an error reply to SecurePayAPIError", async () => {
  const { sp } = client([{ status: 409, body: example("api/error-idempotency-key-reused.409.json") }]);
  const err = await sp.payments.create(PARAMS).then(
    () => assert.fail("expected an error"),
    (e: unknown) => e,
  );
  assert.ok(err instanceof SecurePayAPIError);
  assert.ok(err instanceof SecurePayError);
  assert.equal(err.status, 409);
  assert.equal(err.type, "conflict");
  assert.equal(err.code, "idempotency_key_reused");
  assert.equal(err.requestId, "req_EXAMPLE00000000000000005");
  assert.equal(err.name, "SecurePayAPIError");
});

test("a 401 is not signed and is still reported", async () => {
  const body = JSON.stringify({ error: { type: "authentication", code: "api_key_invalid", message: "Unknown key.", param: null, request_id: "req_TEST00000000000000000002" } });
  const { sp } = client([{ status: 401, body }]);
  await assert.rejects(() => sp.payments.retrieve("pay_EXAMPLE00000000000000001"), (e: unknown) => e instanceof SecurePayAPIError && e.code === "api_key_invalid" && e.status === 401);
});

test("retries 429 (after Retry-After) and 5xx with the same idempotency key", async () => {
  const limited = JSON.stringify({ error: { type: "rate_limit", code: "rate_limited", message: "Slow down.", param: null, request_id: "req_TEST00000000000000000003" } });
  const { sp, calls } = client([
    { status: 429, body: limited, headers: { "Retry-After": "0" } },
    { status: 503, body: "", sign: false },
    { status: 201, body: example("api/create-payment.201.json") },
  ]);
  const payment = await sp.payments.create(PARAMS);
  assert.equal(payment.status, "created");
  assert.equal(calls.length, 3);
  assert.equal(new Set(calls.map((c) => c.headers["idempotency-key"])).size, 1);
});

test("retries a network failure, then gives up with SecurePayConnectionError", async () => {
  const { sp, calls } = client([new TypeError("fetch failed"), new TypeError("fetch failed")], { maxRetries: 1 });
  await assert.rejects(() => sp.payments.retrieve("pay_EXAMPLE00000000000000001"), SecurePayConnectionError);
  assert.equal(calls.length, 2);
});

test("stops after maxRetries and reports the last error", async () => {
  const limited = JSON.stringify({ error: { type: "rate_limit", code: "rate_limited", message: "Slow down.", param: null, request_id: "req_TEST00000000000000000003" } });
  const { sp, calls } = client([{ status: 429, body: limited, headers: { "Retry-After": "0" } }], { maxRetries: 0 });
  await assert.rejects(() => sp.payments.create(PARAMS), (e: unknown) => e instanceof SecurePayAPIError && e.code === "rate_limited" && e.retryAfter === 0);
  assert.equal(calls.length, 1);
});

test("times out a slow attempt", async () => {
  const slow = (async (_u: unknown, init: RequestInit) =>
    new Promise((_r, reject) => init.signal?.addEventListener("abort", () => reject(init.signal?.reason)))) as typeof fetch;
  const sp = new SecurePay({ apiKey: "sp_test_abc", fetch: slow, timeoutMs: 20, maxRetries: 0 });
  await assert.rejects(() => sp.payments.retrieve("pay_EXAMPLE00000000000000001"), /timed out after 20 ms/);
});

test("checks the reply signature when a signing secret is set", async () => {
  const body = example("api/get-payment-authorised.200.json");
  const tampered = body.replace('"authorised"', '"captured"');
  const bad = client([{ status: 200, body: tampered, sign: false, headers: { "SecurePay-Signature": `t=${Math.floor(Date.now() / 1000)},v1=${"0".repeat(64)}` } }]);
  await assert.rejects(() => bad.sp.payments.retrieve("pay_EXAMPLE00000000000000001"), SecurePaySignatureError);
  const missing = client([{ status: 200, body, sign: false }]);
  await assert.rejects(() => missing.sp.payments.retrieve("pay_EXAMPLE00000000000000001"), /No SecurePay-Signature/);
  // Without a secret nothing is checked.
  const noSecret = client([{ status: 200, body, sign: false }], { signingSecret: undefined });
  const p = await noSecret.sp.payments.retrieve("pay_EXAMPLE00000000000000001");
  assert.equal(p.lastResponse.signatureVerified, false);
});

test("needs an API key and refuses to run in a browser", () => {
  assert.throws(() => new SecurePay({ apiKey: "" }), SecurePayError);
  const g = globalThis as Record<string, unknown>;
  g.window = {};
  g.document = {};
  try {
    assert.throws(() => new SecurePay({ apiKey: "sp_test_abc" }), /must stay on your server/);
    assert.ok(new SecurePay({ apiKey: "sp_test_abc", dangerouslyAllowBrowser: true }));
  } finally {
    delete g.window;
    delete g.document;
  }
});
