// Issued cards page (v2 issuing, docs/v2/ISSUING.md).
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });

function mockFetch(routes: Record<string, () => Response>) {
  const calls: { key: string; body: unknown; headers: Record<string, string> }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url.replace(/^\/internal/, "").split("?")[0]}`;
    calls.push({ key, body: init?.body ? JSON.parse(String(init.body)) : null,
      headers: (init?.headers ?? {}) as Record<string, string> });
    const reply = routes[key];
    if (!reply) throw new Error(`unexpected ${key}`);
    return reply();
  }));
  return calls;
}

const person = (role: string, label: string) => ({ id: "stf_3", email: "x@securepay.test", name: "Sam Tan", role,
  role_label: label, merchant: role === "merchant_operator" ? { id: "mer_1", name: "Demo Shop" } : null });
const card = {
  id: "ic_7Hq2Lw9XcV4bN8mK3pR6tY1z", object: "issued_card", livemode: false, description: "Agent order", brand: "visa",
  last4: "4218", expiry_month: 11, expiry_year: 2029, currency: "SGD", spending_limit: 3000, spent: 0, remaining: 3000,
  single_use: true, status: "active", created_at: "2026-11-28T03:10:00Z", merchant_id: "mer_1", merchant_name: "Demo Shop",
};
const empty = { count: 0, page: 1, page_size: 25, data: [] };

beforeEach(() => { document.cookie = "csrftoken=tok123"; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function at(path: string) {
  window.history.pushState({}, "", `${import.meta.env.BASE_URL.replace(/\/$/, "")}${path}`);
  render(<App />);
}

describe("issued cards", () => {
  it("lets an operator make a single-use card and shows the number once", async () => {
    const calls = mockFetch({
      "GET /me": () => json(person("merchant_operator", "Merchant operator")),
      "GET /alerts/unread": () => json({ unread: 0, latest_at: null }),
      "GET /issuing/cards": () => json(empty),
      "POST /issuing/cards": () => json({ ...card, number: "4571203391724218", cvc: "512" }, 201),
    });
    at("/cards");
    fireEvent.click(await screen.findByRole("button", { name: "Make a card" }));
    fireEvent.change(screen.getByLabelText(/Spending limit/), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "Make card" }));
    expect(await screen.findByText("4571 2033 9172 4218")).toBeTruthy();
    expect(screen.getByText(/shown only once/)).toBeTruthy();
    const sent = calls.find((c) => c.key === "POST /issuing/cards")!;
    expect(sent.body).toEqual({ spending_limit: 3000, single_use: true, description: "", mode: "test" });
    expect(sent.headers["Idempotency-Key"]).toMatch(/^dash-card-/);
  });

  it("shows the bank's answers on a card and lets the operator cancel it", async () => {
    const calls = mockFetch({
      "GET /me": () => json(person("merchant_operator", "Merchant operator")),
      "GET /alerts/unread": () => json({ unread: 0, latest_at: null }),
      "GET /issuing/cards": () => json({ ...empty, count: 1, data: [{ ...card, status: "used", spent: 3000, remaining: 0 }] }),
      [`GET /issuing/cards/${card.id}`]: () => json({ ...card, status: "used", spent: 3000, remaining: 0, bank_reachable: true,
        authorisations: [
          { amount: 500, approved: false, response_code: "62", response_text: "Restricted card", captured: null, reversed: false, created_at: "2026-11-28T03:12:00Z" },
          { amount: 3000, approved: true, response_code: "00", response_text: "Approved", captured: null, reversed: false, created_at: "2026-11-28T03:11:00Z" },
        ] }),
      [`POST /issuing/cards/${card.id}/cancel`]: () => json({ ...card, status: "cancelled" }),
    });
    at("/cards");
    fireEvent.click(await screen.findByRole("button", { name: "Open card ending 4218" }));
    expect(await screen.findByText("62 Restricted card")).toBeTruthy();
    expect(screen.getByText("S$ 30.00 of S$ 30.00 (S$ 0.00 left)")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel card" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST /issuing/cards/${card.id}/cancel`)).toBe(true));
  });

  it("lets an administrator look but not make cards", async () => {
    mockFetch({
      "GET /me": () => json(person("administrator", "Administrator")),
      "GET /alerts/unread": () => json({ unread: 0, latest_at: null }),
      "GET /issuing/cards": () => json({ ...empty, count: 1, data: [card] }),
    });
    at("/cards");
    expect(await screen.findByText("Demo Shop")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Make a card" })).toBeNull();
  });
});
