// Sprint 10 screens: D7 registration and verification, forgot / reset password, D8 API keys.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { expectAccessible } from "./a11y.testutil";

type Reply = (init?: RequestInit) => Response;
const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
const unauthorised = () => json({ error: { code: "not_signed_in", message: "Sign in to the dashboard." } }, 401);

function mockFetch(routes: Record<string, Reply>) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    const key = `${method} ${url.replace(/^\/internal/, "").split("?")[0]}`;
    const reply = routes[key];
    if (!reply) throw new Error(`unexpected ${key}`);
    return reply(init);
  }));
  return calls;
}

const operator = {
  id: "stf_1", email: "aisha@newshop.sg", name: "Aisha Rahman", role: "merchant_operator",
  role_label: "Merchant operator", merchant: { id: "mer_1", name: "New Shop", status: "active" },
};
const own = {
  id: "mer_1", name: "New Shop", status: "active", registration_number: "202612345K", webhook_url: null,
  webhook_url_approved: false, api_key: null, can_generate_key: true,
};

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

function type(label: RegExp | string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe("account pages", () => {
  it("registers a business (D7) and shows where the link went", async () => {
    const calls = mockFetch({
      "POST /register": () => json({ message: "We have sent a verification link to aisha@newshop.sg." }, 202),
    });
    at("/register");
    expect(await screen.findByText("Register your business")).toBeTruthy();
    await expectAccessible();
    type(/Business name/, "New Shop Pte. Ltd.");
    type(/Registration number/, "202612345K");
    type(/Website/, "https://newshop.sg");
    type(/Contact name/, "Aisha Rahman");
    type(/Contact email/, "aisha@newshop.sg");
    type(/^Password/, "long enough passphrase");
    type(/Confirm password/, "long enough passphrase");
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Register business" }));
    expect((await screen.findByRole("status")).textContent).toMatch(/aisha@newshop.sg/);
    expect(calls[0].body).toEqual({
      business_name: "New Shop Pte. Ltd.", registration_number: "202612345K", website: "https://newshop.sg",
      contact_name: "Aisha Rahman", email: "aisha@newshop.sg", password: "long enough passphrase", confirmed: true,
    });
  });

  it("marks the field the server refused", async () => {
    mockFetch({
      "POST /register": () => json({ error: { code: "business_already_registered", message: "This business is already registered.",
        param: "registration_number" } }, 409),
    });
    at("/register");
    for (const [label, value] of [[/Business name/, "A"], [/Registration number/, "202612345K"], [/Website/, "https://a.sg"],
      [/Contact name/, "A B"], [/Contact email/, "a@a.sg"], [/^Password/, "long enough passphrase"],
      [/Confirm password/, "long enough passphrase"]] as const) type(label, value);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Register business" }));
    expect(await screen.findByText("This business is already registered.")).toBeTruthy();
  });

  it("verifies the email from the link fragment and shows the pending state", async () => {
    const calls = mockFetch({
      "POST /register/verify": () => json({ merchant: { id: "mer_1", name: "New Shop", status: "pending" }, email: "aisha@newshop.sg" }),
    });
    at("/register/verify#token=abc123abc123abc123abc123");
    expect(await screen.findByText("Registration received")).toBeTruthy();
    expect(calls[0].body).toEqual({ token: "abc123abc123abc123abc123" });
    expect(window.location.hash).toBe(""); // the token is taken out of the address bar
  });

  it("asks for a reset link and shows the same message for any email", async () => {
    const calls = mockFetch({
      "POST /auth/password-reset": () => json({ message: "If an account uses this email address, we have sent it a link." }, 202),
    });
    at("/forgot-password");
    type(/Email/, "who@ever.sg");
    fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));
    expect((await screen.findByRole("status")).textContent).toMatch(/If an account uses/);
    expect(calls[0].body).toEqual({ email: "who@ever.sg" });
  });

  it("resets the password with the authenticator code", async () => {
    const calls = mockFetch({
      "POST /auth/password-reset/check": () => json({ email: "aisha@newshop.sg", mfa_required: true }),
      "POST /auth/password-reset/confirm": () => json({ email: "aisha@newshop.sg", message: "ok" }),
    });
    at("/reset-password#token=tok-tok-tok-tok-tok-tok");
    expect(await screen.findByText(/Choose a new password for aisha@newshop.sg/)).toBeTruthy();
    type(/^New password/, "a brand new passphrase");
    type(/Confirm new password/, "a brand new passphrase");
    fireEvent.change(screen.getByLabelText("Digit 1"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect((await screen.findByRole("status")).textContent).toMatch(/every signed-in session has ended/);
    expect(calls[1].body).toEqual({ token: "tok-tok-tok-tok-tok-tok", password: "a brand new passphrase", code: "123456" });
  });

  it("explains an expired reset link", async () => {
    mockFetch({
      "POST /auth/password-reset/check": () => json({ error: { code: "link_invalid",
        message: "This link has expired or has already been used. Ask for a new one.", param: "token" } }, 400),
    });
    at("/reset-password#token=old-old-old-old-old-old");
    expect(await screen.findByText(/has expired or has already been used/)).toBeTruthy();
    expect(screen.getByText("Ask for a new link")).toBeTruthy();
  });

  it("generates the API key once the merchant is approved (D8) and shows it once", async () => {
    mockFetch({
      "GET /me": () => json(operator),
      "GET /merchant": () => json(own),
      "POST /merchant/api-key": () => json({ ...own, can_generate_key: false,
        api_key: { id: "key_1", hint: "sp_test_AbCd…", created_at: "2026-11-16T04:00:00Z", last_used_at: null },
        credentials: { api_key: "sp_test_AbCdSECRET", signing_secret: "whsec_SECRET" } }, 201),
    });
    at("/api-keys");
    fireEvent.click(await screen.findByRole("button", { name: "Generate test key" }));
    await expectAccessible();
    expect((await screen.findByTestId("new-api-key")).textContent).toBe("sp_test_AbCdSECRET");
    expect(screen.getByTestId("new-signing-secret").textContent).toBe("whsec_SECRET");
    expect(screen.queryByRole("button", { name: "Generate test key" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "I have stored them" }));
    await waitFor(() => expect(screen.queryByTestId("new-api-key")).toBeNull());
    expect(screen.getByText("sp_test_AbCd…")).toBeTruthy();
  });

  it("tells a pending operator that the key comes after approval", async () => {
    mockFetch({
      "GET /me": () => json({ ...operator, merchant: { ...operator.merchant, status: "pending" } }),
      "GET /merchant": () => json({ ...own, status: "pending", can_generate_key: false }),
      "POST /merchant/webhook": () => json({ ...own, status: "pending", can_generate_key: false,
        webhook_url: "https://newshop.sg/hooks", webhook_url_approved: false }),
    });
    at("/api-keys");
    expect(await screen.findByText(/You can generate an API key once an administrator approves/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Generate test key" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Add address" }));
    type(/Webhook URL/, "https://newshop.sg/hooks");
    fireEvent.click(screen.getByRole("button", { name: "Submit for approval" }));
    expect(await screen.findByText("Awaiting approval")).toBeTruthy();
  });

  it("links to the new pages from sign-in", async () => {
    mockFetch({ "GET /me": unauthorised });
    at("/login");
    expect(await screen.findByText("Forgot your password?")).toBeTruthy();
    expect(screen.getByText("Register your business")).toBeTruthy();
  });
});
