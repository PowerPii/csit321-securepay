// API key rotation and revocation (FR-MER-04, FR-MER-05) and security alerts (FR-ADM-08).
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { expectAccessible } from "./a11y.testutil";

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });

function mockFetch(routes: Record<string, () => Response>) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    const key = `${method} ${url.replace(/^\/internal/, "").split("?")[0]}`;
    const reply = routes[key];
    if (!reply) throw new Error(`unexpected ${key}`);
    return reply();
  }));
  return calls;
}

const operator = {
  id: "stf_1", email: "aisha@newshop.sg", name: "Aisha Rahman", role: "merchant_operator",
  role_label: "Merchant operator", merchant: { id: "mer_1", name: "New Shop", status: "active" },
};
const admin = { id: "stf_9", email: "admin@securepay.sg", name: "Admin", role: "administrator",
  role_label: "Administrator", merchant: null };
const key = { id: "key_1", hint: "sp_test_AbCd…", created_at: "2026-11-16T04:00:00Z", last_used_at: null };
const own = {
  id: "mer_1", name: "New Shop", status: "active", registration_number: null, webhook_url: null,
  webhook_url_approved: false, api_key: key, can_generate_key: false, revoked_keys: [],
};
const alert = {
  id: "sal_1", kind: "cross_merchant_access", kind_label: "Repeated cross-merchant access", severity: "high",
  source: "key_7", merchant_id: "mer_1", summary: "API key key_7 of New Shop asked for 3 records of other merchants.",
  details: {}, count: 4, first_seen_at: "2026-11-16T04:00:00Z", last_seen_at: "2026-11-16T04:05:00Z",
  status: "open", resolved_by: null, resolved_at: null, resolution_note: null,
};

beforeEach(() => { document.cookie = "csrftoken=tok123"; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function at(path: string) {
  window.history.pushState({}, "", `${import.meta.env.BASE_URL.replace(/\/$/, "")}${path}`);
  render(<App />);
}

describe("API key rotation and revocation", () => {
  it("rotates after the authenticator code and shows the new key once", async () => {
    const calls = mockFetch({
      "GET /me": () => json(operator),
      "GET /merchant": () => json(own),
      "POST /merchant/api-key/rotate": () => json({ ...own,
        api_key: { ...key, id: "key_2", hint: "sp_test_ZyXw…" },
        revoked_keys: [{ id: "key_1", hint: "sp_test_AbCd…", created_at: key.created_at, revoked_at: "2026-11-16T05:00:00Z" }],
        credentials: { api_key: "sp_test_ZyXwNEW", signing_secret: "whsec_NEW" } }),
    });
    at("/api-keys");
    fireEvent.click(await screen.findByRole("button", { name: "Rotate test key" }));
    await expectAccessible();
    const dialog = await screen.findByRole("dialog", { name: "Rotate test API key" });
    const submit = within(dialog).getByRole("button", { name: "Rotate key" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText("Code from your authenticator app"), { target: { value: "123456" } });
    fireEvent.click(submit);
    expect((await screen.findByTestId("new-api-key")).textContent).toBe("sp_test_ZyXwNEW");
    expect(screen.getByTestId("new-signing-secret").textContent).toBe("whsec_NEW");
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ code: "123456", mode: "test" });
    expect(screen.getByText("Revoked")).toBeTruthy();
  });

  it("warns before revoking the only key and sends the confirmation", async () => {
    const calls = mockFetch({
      "GET /me": () => json(operator),
      "GET /merchant": () => json(own),
      "POST /merchant/api-key/revoke": () => json({ ...own, api_key: null, can_generate_key: true }),
    });
    at("/api-keys");
    fireEvent.click(await screen.findByRole("button", { name: "Revoke test key" }));
    const dialog = await screen.findByRole("dialog", { name: "Revoke test API key" });
    expect(within(dialog).getByText(/only active test API key/)).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText("Code from your authenticator app"), { target: { value: "654321" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Revoke key" }));
    expect(await screen.findByText(/The test API key is revoked/)).toBeTruthy();
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ code: "654321", mode: "test", confirm_last_key: true });
    expect(await screen.findByRole("button", { name: "Generate test key" })).toBeTruthy();
  });

  it("shows a wrong code without closing the dialog", async () => {
    mockFetch({
      "GET /me": () => json(operator),
      "GET /merchant": () => json(own),
      "POST /merchant/api-key/rotate": () => json({ error: { code: "code_incorrect",
        message: "That code is not right, so nothing was changed." } }, 403),
    });
    at("/api-keys");
    fireEvent.click(await screen.findByRole("button", { name: "Rotate test key" }));
    const dialog = await screen.findByRole("dialog", { name: "Rotate test API key" });
    fireEvent.change(within(dialog).getByLabelText("Code from your authenticator app"), { target: { value: "000000" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Rotate key" }));
    expect(await within(dialog).findByText(/nothing was changed/)).toBeTruthy();
  });
});

describe("security alerts", () => {
  it("shows the open count and resolves an alert with a note", async () => {
    let resolved = false;
    const calls = mockFetch({
      "GET /me": () => json(admin),
      "GET /alerts/unread": () => json({ unread: 0, latest_at: null }),
      "GET /security/alerts/open-count": () => json({ open: resolved ? 0 : 1, latest_at: alert.last_seen_at }),
      "GET /security/alerts": () => json({ count: 1, page: 1, page_size: 25, open: resolved ? 0 : 1,
        data: [{ ...alert, status: resolved ? "resolved" : "open" }] }),
      "GET /security/alerts/sal_1": () => json({ ...alert, status: resolved ? "resolved" : "open",
        resolved_by: resolved ? "Admin" : null, resolution_note: resolved ? "Merchant told." : null,
        resolved_at: resolved ? "2026-11-16T06:00:00Z" : null,
        related_audit: [{ id: 1, at: "2026-11-16T04:05:00Z", action: "security.cross_merchant_access",
          actor_type: "merchant_api", actor_id: "key_7", merchant_id: "mer_1", target_type: "payment",
          target_id: "pay_x", request_id: null, details: {} }] }),
      "POST /security/alerts/sal_1/resolve": () => { resolved = true; return json({ ...alert, status: "resolved" }); },
    });
    at("/security");
    expect(await screen.findByRole("link", { name: "1 open security alert" })).toBeTruthy();
    await expectAccessible();
    expect(screen.getByRole("status").textContent).toContain("1 open security alert");
    fireEvent.click(await screen.findByRole("row", { name: /Repeated cross-merchant access: key_7/ }));
    const dialog = await screen.findByRole("dialog", { name: "Repeated cross-merchant access" });
    expect(await within(dialog).findByText("security.cross_merchant_access")).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText(/Resolution note/), { target: { value: "Merchant told." } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Mark as resolved" }));
    expect(await within(dialog).findByText(/Resolved by Admin/)).toBeTruthy();
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ note: "Merchant told." });
  });

  it("is not shown to analysts", async () => {
    mockFetch({
      "GET /me": () => json({ ...admin, role: "fraud_analyst", role_label: "Fraud analyst" }),
      "GET /alerts/unread": () => json({ unread: 0, latest_at: null }),
      "GET /payments": () => json({ count: 0, page: 1, page_size: 20, data: [],
        summary: { awaiting_capture: 0, unresolved: 0, blocked_today: 0, refunded_this_month: 0, escalated: 0 } }),
    });
    at("/security");
    await waitFor(() => expect(window.location.pathname).toMatch(/\/payments$/));
    expect(screen.queryByText("Security alerts")).toBeNull();
  });
});
