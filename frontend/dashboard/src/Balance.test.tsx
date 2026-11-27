// Balance page (v2 ledger): merchant balances, entries, payouts; the administrator's books.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { expectAccessible } from "./a11y.testutil";
import { rulesText } from "./routes/Balance";

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });

function mockFetch(routes: Record<string, (body?: unknown) => Response>) {
  const calls: { url: string; method: string; body?: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    const key = `${method} ${url.replace(/^\/internal/, "").split("?")[0]}`;
    const reply = routes[key];
    if (!reply) throw new Error(`unexpected ${key}`);
    return reply(body);
  }));
  return calls;
}

const operator = {
  id: "stf_1", email: "aisha@voltco.sg", name: "Aisha Rahman", role: "merchant_operator",
  role_label: "Merchant operator", merchant: { id: "mer_1", name: "Volt & Co.", status: "active" },
};
const admin = { id: "stf_9", email: "admin@securepay.sg", name: "Admin", role: "administrator",
  role_label: "Administrator", merchant: null };
const analyst = { id: "stf_5", email: "an@securepay.sg", name: "Analyst", role: "fraud_analyst",
  role_label: "Fraud analyst", merchant: null };

const rules = { fee_percent_bps: 290, fee_fixed: 30, reserve_bps: 1000, reserve_hold_days: 7, payout_minimum: 100 };
const merchantBalance = {
  object: "balance", scope: "merchant", currency: "SGD", merchant_id: "mer_1", merchant_name: "Volt & Co.",
  available: 10422, reserve: 1200,
  next_release: { amount: 1200, at: "2026-12-04T12:00:00Z", payment_id: "pay_1" }, rules,
};
const capture = {
  id: "jrn_1", kind: "capture", description: "Capture SP-AAAA-BBBB", merchant_id: "mer_1", merchant_name: "Volt & Co.",
  payment_id: "pay_1", payment_reference: "SP-AAAA-BBBB", refund_id: null, payout_id: null,
  occurred_at: "2026-11-27T12:00:00Z", posted_at: "2026-11-27T12:00:00Z", backfilled: false,
  lines: [
    { account: "network_clearing", memo: "gross", amount: 12000, currency: "SGD" },
    { account: "fee_revenue", memo: "fee", amount: -378, currency: "SGD" },
    { account: "merchant_reserve", memo: "reserve", amount: -1200, currency: "SGD" },
    { account: "merchant_available", memo: "net", amount: -10422, currency: "SGD" },
  ],
};
const entries = { object: "list", count: 1, page: 1, page_size: 25, kinds: ["capture"], data: [capture] };
const noPayouts = { object: "list", count: 0, page: 1, page_size: 25, data: [] };

beforeEach(() => { document.cookie = "csrftoken=tok123"; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.localStorage.clear(); });

function at(path: string) {
  window.history.pushState({}, "", `${import.meta.env.BASE_URL.replace(/\/$/, "")}${path}`);
  render(<App />);
}

describe("Balance", () => {
  it("describes the fee and reserve rules", () => {
    expect(rulesText(rules)).toBe("Fee 2.9% + S$ 0.30 per capture. 10% of each capture is held in reserve for 7 days.");
  });

  it("switches the balance, entries and payouts to live mode, and remembers it (v2)", async () => {
    const calls = mockFetch({
      "GET /me": () => json(operator),
      "GET /balance": () => json(merchantBalance),
      "GET /ledger/entries": () => json(entries),
      "GET /payouts": () => json(noPayouts),
    });
    at("/balance");
    expect(await screen.findByText(/^Test mode\. /)).toBeTruthy();
    await waitFor(() => expect(calls.some((c) => c.url.includes("/balance?mode=test"))).toBe(true));
    fireEvent.click(screen.getByRole("switch", { name: "Live mode" }));
    expect(await screen.findByText(/^Live mode\. /)).toBeTruthy();
    await waitFor(() => {
      for (const path of ["/balance?mode=live", "/ledger/entries?mode=live", "/payouts?mode=live"]) {
        expect(calls.some((c) => c.url.includes(path))).toBe(true);
      }
    });
    expect(window.localStorage.getItem("securepay.dashboard.mode")).toBe("live");
    await expectAccessible();
  });

  it("shows an operator's balances and the lines of an entry", async () => {
    mockFetch({
      "GET /me": () => json(operator),
      "GET /balance": () => json(merchantBalance),
      "GET /ledger/entries": () => json(entries),
      "GET /payouts": () => json(noPayouts),
    });
    at("/balance");
    expect(await screen.findByRole("heading", { name: "Balance" })).toBeTruthy();
    expect(await screen.findByText("S$ 104.22")).toBeTruthy();
    expect(screen.getByText("S$ 12.00")).toBeTruthy();
    const table = await screen.findByRole("table", { name: "Ledger entries" });
    expect(within(table).getByText("+S$ 104.22")).toBeTruthy();
    expect(within(table).getByText("+S$ 12.00")).toBeTruthy();
    await expectAccessible();

    fireEvent.click(within(table).getByRole("button", { name: "Open Capture jrn_1" }));
    const dialog = await screen.findByRole("dialog");
    const lines = within(dialog).getByRole("table", { name: "Lines of jrn_1" });
    expect(within(lines).getByText("S$ 120.00")).toBeTruthy();
    expect(within(lines).getByText("S$ 3.78")).toBeTruthy();
    expect(within(lines).getByText("S$ 0.00, balanced")).toBeTruthy();
  });

  it("pays out after checking the amount", async () => {
    let available = 10422;
    const calls = mockFetch({
      "GET /me": () => json(operator),
      "GET /balance": () => json({ ...merchantBalance, available }),
      "GET /ledger/entries": () => json(entries),
      "GET /payouts": () => json(noPayouts),
      "POST /payouts": (body) => {
        const amount = (body as { amount: number }).amount;
        available -= amount;
        return json({ id: "po_1", object: "payout", amount, currency: "SGD", status: "paid",
          merchant_id: "mer_1", created_at: "2026-11-27T13:00:00Z" }, 201);
      },
    });
    at("/balance");
    const amount = await screen.findByLabelText("Amount (S$)");
    fireEvent.change(amount, { target: { value: "500" } });
    fireEvent.click(screen.getByRole("button", { name: "Pay out" }));
    expect(await screen.findByText("Only S$ 104.22 is available.")).toBeTruthy();
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    fireEvent.change(amount, { target: { value: "50.00" } });
    fireEvent.click(screen.getByRole("button", { name: "Pay out" }));
    expect(await screen.findByText("Paid out S$ 50.00 to your bank account.")).toBeTruthy();
    expect(calls.filter((c) => c.method === "POST").map((c) => c.body)).toEqual([{ amount: 5000, mode: "test" }]);
    await waitFor(() => expect(screen.getByText("S$ 54.22")).toBeTruthy());
  });

  it("shows administrators the gateway books without a payout form", async () => {
    mockFetch({
      "GET /me": () => json(admin),
      "GET /balance": () => json({
        object: "balance", scope: "gateway", currency: "SGD", network_clearing: 12000, fee_revenue: 378,
        merchants_available: 10422, merchants_reserve: 1200, entries: 1, check: { ok: true, problems: [] },
        merchants: [{ merchant_id: "mer_1", merchant_name: "Volt & Co.", available: 10422, reserve: 1200 }], rules,
      }),
      "GET /ledger/entries": () => json(entries),
    });
    at("/balance");
    expect(await screen.findByText(/The books balance/)).toBeTruthy();
    const merchants = screen.getByRole("table", { name: "Merchant balances" });
    expect(within(merchants).getByText("Volt & Co.")).toBeTruthy();
    expect(screen.queryByLabelText("Amount (S$)")).toBeNull();
    await expectAccessible();
  });

  it("is not offered to fraud analysts", async () => {
    mockFetch({
      "GET /me": () => json(analyst),
      "GET /payments": () => json({ count: 0, page: 1, page_size: 25, data: [],
        summary: { awaiting_capture: 0, unresolved: 0, blocked_today: 0 } }),
    });
    at("/balance");
    expect(await screen.findByRole("heading", { name: "Payments" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Balance" })).toBeNull();
  });
});
