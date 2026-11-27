import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { expectAccessible } from "./a11y.testutil";

type Reply = (init?: RequestInit) => Response;
const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
const unauthorised = () => json({ error: { code: "not_signed_in", message: "Sign in to the dashboard." } }, 401);

function mockFetch(routes: Record<string, Reply | Reply[]>) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  const used: Record<string, number> = {};
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    const path = url.replace(/^\/internal/, "").split("?")[0];
    const key = `${method} ${path}`;
    const reply = routes[key];
    if (!reply) throw new Error(`unexpected ${key}`);
    if (Array.isArray(reply)) {
      const i = Math.min(used[key] ?? 0, reply.length - 1);
      used[key] = (used[key] ?? 0) + 1;
      return reply[i](init);
    }
    return reply(init);
  }));
  return calls;
}

const operator = {
  id: "stf_1", email: "ops@demoshop.sg", name: "Aisha Rahman", role: "merchant_operator",
  role_label: "Merchant operator", merchant: { id: "mer_1", name: "Demo Shop" },
};
const analyst = { ...operator, id: "stf_2", name: "Daniel Lim", role: "fraud_analyst", role_label: "Fraud analyst", merchant: null };

const row = {
  id: "pay_1", reference: "SP-7Q2K-91XD", order_reference: "ORD-1042", merchant: { id: "mer_1", name: "Demo Shop" },
  amount: 8990, currency: "SGD", status: "authorised", refunded_amount: 0, card: { brand: "visa", last4: "3456" },
  risk: { decision: "verify", verification: "passed" }, customer: "j•••@example.com", outcome_reason: null,
  created_at: "2026-11-02T06:52:00Z",
};
const list = {
  count: 1, page: 1, page_size: 20, data: [row],
  summary: { awaiting_capture: 4, unresolved: 1, blocked_today: 3, refunded_this_month: 41200, escalated: null },
};
const screening = {
  id: "scr_1", outcome: "verify", score: 0.47, reasons: ["high_amount"], features: { card_attempts_10m: 0 },
  model_version: "stub-0", rules_version: "rules-0.1", policy_version: "policy-0.1", feature_schema_version: "fs-0.1",
  thresholds: { verify: 0.5, block: 0.9 }, elapsed_ms: 12, error: null, occurred_at: "2026-11-02T06:52:00Z",
};
function detail(over: Record<string, unknown> = {}) {
  return {
    ...row, line_items: null, captured_amount: 0, refundable_amount: 0, refund_reserved_amount: 0,
    card: { token: "tok_1", brand: "visa", last4: "3456" }, outcome: null, pending_operation: null,
    authorisation_expires_at: "2026-11-09T06:52:00Z", authorised_at: "2026-11-02T06:52:05Z", captured_at: null,
    cancelled_at: null, description: null, screening, screening_decision: "verify", verification: "passed",
    operations: [], refunds: [], webhooks: [{ id: "evt_1", type: "payment.authorised", sequence: 2, status: "delivered",
      attempts: 1, last_http_status: 200, last_error: null, next_attempt_at: null, delivered_at: "2026-11-02T06:52:06Z" }],
    timeline: [{ at: "2026-11-02T06:52:00Z", action: "payment.created", actor_type: "merchant_api", actor: "key_abcdefgh",
      target_type: "payment", target_id: "pay_1", details: {} }],
    correlation_id: "pay_1",
    trail: [
      { at: "2026-11-02T06:52:00Z", kind: "payment", id: "pay_1", title: "Payment created", status: "created",
        request_id: null, correlation_id: "pay_1" },
      { at: "2026-11-02T06:52:03Z", kind: "screening", id: "scr_1", title: "Screening: verify", status: "verify",
        detail: "score 0.4100", request_id: null, correlation_id: "pay_1" },
      { at: "2026-11-02T06:52:06Z", kind: "webhook", id: "evt_1", title: "Webhook payment.authorised", status: "delivered",
        detail: "1 attempt(s)", request_id: null, correlation_id: "pay_1" },
    ],
    actions: { capture: true, cancel: true, refund: false, recheck: false, review: false },
    ...over,
  };
}

beforeEach(() => {
  document.cookie = "csrftoken=tok123";
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function at(path: string) {
  window.history.pushState({}, "", `${import.meta.env.BASE_URL.replace(/\/$/, "")}${path}`);
  render(<App />);
}

describe("dashboard", () => {
  it("signs in with password then TOTP (D1, D2) and lands on payments (D3)", async () => {
    const calls = mockFetch({
      "GET /me": [unauthorised, unauthorised, () => json(operator)],
      "POST /auth/login": () => json({ next: "totp", email: "ops@demoshop.sg", setup: null }),
      "POST /auth/totp": () => json(operator),
      "GET /payments": () => json(list),
    });
    at("/payments");
    expect(await screen.findByText("Sign in to the dashboard")).toBeTruthy();
    await expectAccessible();
    fireEvent.change(screen.getByLabelText(/Email/), { target: { value: "ops@demoshop.sg" } });
    fireEvent.change(screen.getByLabelText(/Password/), { target: { value: "correct horse" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Two-step verification")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Digit 1"), { target: { value: "279013" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByText("SP-7Q2K-91XD")).toBeTruthy();
    expect(screen.getByText("Verify · passed")).toBeTruthy();
    expect(screen.getByText("S$ 412.00")).toBeTruthy();
    expect(screen.getByText("Merchant: Demo Shop")).toBeTruthy();
    const login = calls.find((c) => c.url.endsWith("/auth/login"))!;
    expect(login.body).toEqual({ email: "ops@demoshop.sg", password: "correct horse" });
    expect(calls.find((c) => c.url.endsWith("/auth/totp") && c.method === "POST")!.body).toEqual({ code: "279013" });
  });

  it("shows a lockout message from the server", async () => {
    mockFetch({
      "GET /me": unauthorised,
      "POST /auth/login": () => json({ error: { code: "account_locked", message: "This account is locked after too many failed sign-ins. Try again in 15 minutes." } }, 403),
    });
    at("/login");
    fireEvent.change(await screen.findByLabelText(/Email/), { target: { value: "a@b.sg" } });
    fireEvent.change(screen.getByLabelText(/Password/), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/locked/);
  });

  it("warns before the idle sign-out and keeps the session on 'Stay signed in' (FR-ACC-10)", async () => {
    // Shortened limits: warn after 0.6 s, sign out after 60 s.
    const calls = mockFetch({
      "GET /me": () => json({ ...operator, idle_warning_minutes: 0.01, idle_minutes: 1 }),
      "GET /payments": () => json(list),
    });
    at("/payments");
    const dialog = await screen.findByRole("dialog", {}, { timeout: 3000 });
    expect(within(dialog).getByText(/You will be signed out in (0:5\d|1:00) because of inactivity/)).toBeTruthy();
    const before = calls.filter((c) => c.url === "/internal/me").length;
    fireEvent.click(within(dialog).getByRole("button", { name: "Stay signed in" }));
    await waitFor(() => expect(calls.filter((c) => c.url === "/internal/me").length).toBe(before + 1));
  });

  it("shows a THB payment in baht and what the merchant gets in SGD (v2)", async () => {
    mockFetch({
      "GET /me": () => json(operator),
      "GET /payments/pay_1": () => json(detail({ amount: 119200, currency: "THB", captured_amount: 0,
        settlement: { currency: "SGD", amount: 4589, captured_amount: 0,
          fx: { rate: "0.0385", rate_sheet: "fxr_1", fee_percent: "1" } } })),
    });
    at("/payments/pay_1");
    expect(await screen.findByRole("heading", { name: /THB 1,192\.00 · Order/ })).toBeTruthy();
    expect(screen.getByText("S$ 45.89 at 1 THB = S$ 0.0385 · FX fee 1%")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Capture THB 1,192.00" })).toBeTruthy();
  });

  it("shows each operation's path through the card network (v2)", async () => {
    const hop = (node: string, mti_in: string, mti_out: string, ms: number) =>
      ({ node, mti_in, mti_out, response_code: "00", elapsed_ms: ms });
    const op = (id: string, type: string, network: unknown) => ({
      id, type, status: "succeeded", amount: 8990, simulator_reference: "sim_1", reconciliation_checks: 0,
      last_checked_at: null, next_check_at: null, escalated_at: null, created_at: "2026-11-02T06:52:00Z",
      completed_at: "2026-11-02T06:52:01Z", network,
    });
    mockFetch({
      "GET /me": () => json(operator),
      "GET /payments/pay_1": () => json(detail({ status: "captured", operations: [
        op("op_1", "authorise", { stan: "318204", rrn: "K7Q2M9X4B1TZ", auth_code: "A1B2C3", response_code: "00",
          response_text: "Approved", trace: [hop("acquirer", "0100", "0110", 9), hop("switch", "0100", "0110", 5),
            hop("issuer", "0100", "0110", 1)] }),
        op("op_2", "capture", { stan: "702551", rrn: "Q9W4E2R7T1YU", auth_code: null, response_code: "00",
          response_text: "Approved", trace: [hop("acquirer", "0220", "0230", 7), hop("switch", "0220", "0230", 4),
            hop("issuer", "0220", "0230", 1)] }),
        op("op_3", "cancel", null),
      ] })),
    });
    at("/payments/pay_1");
    expect(await screen.findByText("Card network")).toBeTruthy();
    await expectAccessible();
    const auth = screen.getByRole("list", { name: "Authorise network path" });
    expect(within(auth).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Acquirer0100 → 0110 · 00 · 9 ms", "→SecurePay Switch0100 → 0110 · 00 · 5 ms", "→SecurePay Bank0100 → 0110 · 00 · 1 ms",
    ]);
    expect(screen.getByText("auth code A1B2C3")).toBeTruthy();
    expect(within(screen.getByRole("list", { name: "Capture network path" })).getByText("0220 → 0230 · 00 · 4 ms"))
      .toBeTruthy();
    expect(screen.queryByRole("list", { name: "Cancel network path" })).toBeNull();
  });

  it("lets an operator capture from the payment detail (D4)", async () => {
    const calls = mockFetch({
      "GET /me": () => json(operator),
      "GET /payments/pay_1": () => json(detail()),
      "POST /payments/pay_1/capture": () => json(detail({ status: "captured", captured_amount: 8990,
        actions: { capture: false, cancel: false, refund: true, recheck: false, review: false } })),
      "GET /payments": () => json(list),
    });
    at("/payments/pay_1");
    expect(await screen.findByText("Fraud decision")).toBeTruthy();
    await expectAccessible();
    expect(screen.getByText("0.47")).toBeTruthy();
    expect(screen.getByText("Amount over S$ 500")).toBeTruthy();
    expect(screen.getByText("Created by merchant API")).toBeTruthy();
    const trail = screen.getByRole("table", { name: "Full timeline" });  // FR-TXN-16
    expect(within(trail).getByText("Screening: verify")).toBeTruthy();
    expect(within(trail).getByText("Webhook payment.authorised")).toBeTruthy();
    expect(screen.getAllByText("pay_1").length).toBeGreaterThan(1);
    fireEvent.click(screen.getByRole("button", { name: "Capture S$ 89.90" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Capture S$ 89.90" }));
    expect(await screen.findByRole("button", { name: "Refund" })).toBeTruthy();
    expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/capture"))).toBe(true);
  });

  it("refunds part of a captured payment (D5) and checks the amount", async () => {
    const captured = detail({ status: "captured", captured_amount: 12900, refundable_amount: 10000, refunded_amount: 2900,
      actions: { capture: false, cancel: false, refund: true, recheck: false, review: false } });
    const calls = mockFetch({
      "GET /me": () => json(operator),
      "GET /payments/pay_1": () => json(captured),
      "POST /payments/pay_1/refunds": () => json({ id: "rfd_1", reference: "RF-AAAA-BBBB", amount: 3000, status: "completed",
        reason: "One item", failure_reason: null, created_at: "2026-11-02T07:00:00Z", completed_at: "2026-11-02T07:00:01Z" }, 201),
    });
    at("/payments/pay_1/refund");
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("S$ 100.00")).toBeTruthy();
    const amount = within(dialog).getByLabelText(/Amount/);
    fireEvent.change(amount, { target: { value: "101" } });
    expect((within(dialog).getByRole("button", { name: /^Refund/ }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(amount, { target: { value: "30" } });
    fireEvent.change(within(dialog).getByLabelText(/Reason/), { target: { value: "One item" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Refund S$ 30.00" }));
    expect(await within(dialog).findByText(/Refunded S\$ 30.00/)).toBeTruthy();
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ amount: 3000, reason: "One item" });
  });

  it("hides capture from an analyst and records a finding (D6, D6b)", async () => {
    const caseRow = {
      id: "scr_9", payment: { id: "pay_9", reference: "SP-5LKD-2ZZ8", amount: 98000, currency: "SGD", status: "blocked" },
      merchant: { id: "mer_1", name: "Demo Shop" }, score: 0.93, outcome: "block", decision: "blocked",
      reasons: ["blocklisted_card"], occurred_at: "2026-11-02T04:47:00Z",
      review: { status: "open", assigned_to: null, finding: null, notes: "", reviewed_by: null, reviewed_at: null },
    };
    const caseDetail = { ...caseRow, payment: { ...caseRow.payment, order_reference: "ORD-1040", card: null, customer: null,
      outcome_reason: "fraud_rule", created_at: "2026-11-02T04:47:00Z" }, screening: { ...screening, outcome: "block", score: 0.93 },
      linked_activity: { window_hours: 24, card: { attempts: 7, merchants: 3, blocked: 2 }, email: null, ip: { attempts: 11, merchants: 1, blocked: 0 } },
      related: [] };
    const calls = mockFetch({
      "GET /me": () => json(analyst),
      "GET /alerts/unread": () => json({ unread: 0, latest_at: null }),
      "GET /reviews": () => json({ count: 1, page: 1, page_size: 25, counts: { open: 1, mine: 0, closed: 0 }, data: [caseRow] }),
      "GET /reviews/scr_9": () => json(caseDetail),
      "POST /reviews/scr_9": () => json({ ...caseDetail, review: { ...caseDetail.review, status: "closed",
        finding: "confirmed_fraud", reviewed_by: "Daniel Lim", reviewed_at: "2026-11-02T05:00:00Z" } }),
    });
    at("/reviews/scr_9");
    expect(await screen.findByText("Case · SP-5LKD-2ZZ8")).toBeTruthy();
    await expectAccessible();
    expect(screen.getByText("7 other attempts · 3 merchants · 2 blocked")).toBeTruthy();
    expect(screen.queryByText(/Payments/, { selector: "button" })).toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: "Confirmed fraud" }));
    fireEvent.change(screen.getByLabelText("Notes"), { target: { value: "Card tested on several stores" } });
    fireEvent.click(screen.getByRole("button", { name: "Save finding" }));
    expect(await screen.findByText(/recorded by Daniel Lim/)).toBeTruthy();
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({
      action: "save", finding: "confirmed_fraud", notes: "Card tested on several stores" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Reopen case" })).toBeTruthy());
  });
  it("shows the overview: today's tiles, the 14-day chart and the latest payments", async () => {
    const daily = Array.from({ length: 14 }, (_, i) => ({
      date: `2026-11-${String(i + 3).padStart(2, "0")}`, approved: i % 3, declined: i % 2, blocked: i === 13 ? 2 : 0, volume: 1000 * i,
    }));
    mockFetch({
      "GET /me": () => json(operator),
      "GET /overview": () => json({
        today: { payments: 5, approved: 3, declined: 1, blocked: 1, verification_asked: 2, approved_volume: 254890, approval_rate: 0.6 },
        daily, screening_7d: { allow: 6, verify: 2, block: 1, not_screened: 0 }, recent: [row],
      }),
    });
    at("/");
    expect(await screen.findByText("Payments today")).toBeTruthy();
    await expectAccessible();
    expect(screen.getByText("60%")).toBeTruthy();
    expect(screen.getByText("S$ 2,548.90")).toBeTruthy();
    expect(screen.getByRole("img", { name: /last 14 days/ })).toBeTruthy();
    expect(screen.getByText("9 payments screened in the last 7 days.")).toBeTruthy();
    expect(screen.getByText("SP-7Q2K-91XD")).toBeTruthy();
  });
});
