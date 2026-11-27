import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Account, { pageFromPath, tokenFromHash } from "./Account";
import { assertion, stubPasskeyDevice } from "./testPasskeys";

const ME = { id: "cus_x", full_name: "Jane Tan", email: "jane@example.com", idle_minutes: 15, idle_warning_minutes: 13 };
const ROW = {
  reference: "SP-7QK2-91XD", merchant_name: "Demo Shop", amount: 8990, currency: "SGD",
  status: "captured", status_label: "Captured", created_at: "2026-11-02T06:52:00Z",
};
const TOKEN = "t".repeat(43);

type Reply = () => Response;
function mockFetch(replies: Record<string, Reply | Reply[]>) {
  const calls: { url: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
    const key = Object.keys(replies).find((k) => url.endsWith(k));
    if (!key) throw new Error(`unexpected ${url}`);
    const r = replies[key];
    return Array.isArray(r) ? r.shift()!() : r();
  }));
  return calls;
}

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
const signedOut = () => json({ error: { code: "sign_in_required", message: "x" } }, 401);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("account helpers", () => {
  it("maps paths to pages", () => {
    expect(pageFromPath("/account")).toBe("sign-in");
    expect(pageFromPath("/account/payments")).toBe("payments");
    expect(pageFromPath("/account/reset/")).toBe("reset");
    expect(pageFromPath("/account/whatever")).toBe("sign-in");
  });

  it("reads only a well-formed token from the fragment", () => {
    expect(tokenFromHash(`#token=${TOKEN}`)).toBe(TOKEN);
    expect(tokenFromHash("#token=<script>")).toBeNull();
    expect(tokenFromHash("")).toBeNull();
  });
});

describe("sign in and register (C10)", () => {
  it("shows the generic error when the password is wrong", async () => {
    const calls = mockFetch({
      "/me": signedOut,
      "/sign-in": () => json({ error: { code: "invalid_credentials", message: "Email or password is incorrect. After 5 failed attempts, sign-in is locked for 15 minutes." } }, 401),
    });
    render(<Account path="/account/sign-in" hash="" />);
    fireEvent.change(await screen.findByLabelText("Email", { selector: "#signin-email" }), { target: { value: "jane@example.com" } });
    fireEvent.change(screen.getByLabelText("Password", { selector: "#signin-password" }), { target: { value: "not it at all" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Email or password is incorrect")).toBeTruthy();
    expect(screen.getByText(/After 5 failed attempts/)).toBeTruthy();
    expect(calls[1].body).toEqual({ email: "jane@example.com", password: "not it at all", remember: false });
  });

  it("checks the password length and match before registering", async () => {
    const calls = mockFetch({ "/me": signedOut });
    render(<Account path="/account/sign-in" hash="" />);
    fireEvent.change(await screen.findByLabelText("Full name"), { target: { value: "Jane Tan" } });
    fireEvent.change(screen.getByLabelText("Email", { selector: "#reg-email" }), { target: { value: "jane@example.com" } });
    fireEvent.change(screen.getByLabelText("Password", { selector: "#reg-password" }), { target: { value: "short" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText("Use at least 12 characters.", { selector: ".error" })).toBeTruthy();
    expect(calls).toHaveLength(1);
  });

  it("shows the already-registered error under the email field", async () => {
    mockFetch({
      "/me": signedOut,
      "/register": () => json({ error: { code: "email_taken", param: "email", message: "This email is already registered. Sign in or reset your password." } }, 409),
    });
    render(<Account path="/account/sign-in" hash="" />);
    fireEvent.change(await screen.findByLabelText("Full name"), { target: { value: "Jane Tan" } });
    fireEvent.change(screen.getByLabelText("Email", { selector: "#reg-email" }), { target: { value: "jane@example.com" } });
    fireEvent.change(screen.getByLabelText("Password", { selector: "#reg-password" }), { target: { value: "correct horse battery 7" } });
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "correct horse battery 7" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    // FR-ACC-12: no sign-up without agreeing to the privacy notice.
    expect(await screen.findByText("Agree to the privacy notice to create an account.")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: /keeps my name, email and payment history/ }));
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText(/already registered/)).toBeTruthy();
  });
});

describe("payment history (C11)", () => {
  it("lists payments and opens one with its timeline", async () => {
    mockFetch({
      "/me": () => json(ME),
      "/payments": () => json({ data: [ROW], has_more: false }),
      [`/payments/${ROW.reference}`]: () => json({
        ...ROW, order_reference: "#1042", card: { brand: "visa", last4: "3456" }, refunded_amount: 0,
        timeline: [{ at: ROW.created_at, label: "Payment started" }, { at: ROW.created_at, label: "Charged" }],
      }),
    });
    render(<Account path="/account/payments" hash="" />);
    expect(await screen.findByRole("heading", { name: "Payment history" })).toBeTruthy();
    expect(screen.getByText(/Jane Tan ·/)).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: `Show payment ${ROW.reference}` }));
    expect(await screen.findByText("Visa •••• 3456")).toBeTruthy();
    expect(screen.getByText("#1042")).toBeTruthy();
    expect(screen.getByText("Refunds: none")).toBeTruthy();
    const receipt = screen.getByRole("link", { name: "Download receipt (PDF)" });
    expect(receipt.getAttribute("href")).toBe(`/customer/payments/${ROW.reference}/receipt.pdf`);
  });

  it("shows the empty state", async () => {
    mockFetch({ "/me": () => json(ME), "/payments": () => json({ data: [], has_more: false }) });
    render(<Account path="/account/payments" hash="" />);
    expect(await screen.findByText("You have not made any payments")).toBeTruthy();
  });

  it("sends a signed-out visitor to the sign-in page", async () => {
    mockFetch({ "/me": signedOut });
    render(<Account path="/account/payments" hash="" />);
    expect(await screen.findByRole("button", { name: "Create account" })).toBeTruthy();
  });
});

describe("account recovery (C12)", () => {
  it("gives the same message after asking for a link", async () => {
    const message = "If an account exists for this email, a reset link is on its way. The link works once, for 30 minutes.";
    mockFetch({ "/me": signedOut, "/password-reset/request": () => json({ message }, 202) });
    render(<Account path="/account/reset" hash="" />);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "jane@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(await screen.findByText(/a reset link is on its way/)).toBeTruthy();
  });

  it("sets a new password from the emailed link", async () => {
    const calls = mockFetch({
      "/me": signedOut,
      "/password-reset/check": () => json({ email: "jane@example.com" }),
      "/password-reset/confirm": () => json({ email: "jane@example.com", message: "Your password has been changed. Sign in with the new password." }),
    });
    render(<Account path="/account/reset" hash={`#token=${TOKEN}`} />);
    expect(await screen.findByText(/For jane@example.com/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "a brand new passphrase" } });
    fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "a brand new passphrase" } });
    fireEvent.click(screen.getByRole("button", { name: "Save new password" }));
    expect(await screen.findByText(/Your password has been changed/)).toBeTruthy();
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/confirm"))).toBe(true));
    expect(calls.find((c) => c.url.endsWith("/confirm"))!.body).toEqual({ token: TOKEN, password: "a brand new passphrase" });
  });

  it("shows the used-or-expired state for a dead link", async () => {
    mockFetch({
      "/me": signedOut,
      "/password-reset/check": () => json({ error: { code: "link_invalid", message: "x" } }, 400),
    });
    render(<Account path="/account/reset" hash={`#token=${TOKEN}`} />);
    expect(await screen.findByText("This reset link can no longer be used")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Request a new link" }));
    expect(await screen.findByRole("button", { name: "Send reset link" })).toBeTruthy();
  });
});

describe("idle warning and personal data (FR-ACC-10, FR-ACC-13)", () => {
  it("warns before signing out an idle customer and stays on request", async () => {
    // Shortened limits: warn after 0.6 s, sign out after 60 s.
    const calls = mockFetch({
      "/me": () => json({ ...ME, idle_warning_minutes: 0.01, idle_minutes: 1 }),
      "/payments": () => json({ data: [], has_more: false }),
    });
    render(<Account path="/account/payments" hash="" />);
    const dialog = await screen.findByRole("alertdialog", {}, { timeout: 3000 });
    expect(dialog.textContent).toMatch(/signed out in (0:5\d|1:00) because of inactivity/);
    const before = calls.filter((c) => c.url.endsWith("/me")).length;
    fireEvent.click(screen.getByRole("button", { name: "Stay signed in" }));
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/me")).length).toBe(before + 1));
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("shows the personal data kept and corrects it with the current password", async () => {
    const data = {
      full_name: "Jane Tan", email: "jane@example.com", created_at: "2026-11-01T02:00:00Z",
      last_sign_in_at: "2026-11-16T02:00:00Z", password_changed_at: null, details_changed_at: null,
      privacy_notice_version: "2026-11-16", privacy_consent_at: "2026-11-01T02:00:00Z", payments: 4, active_sessions: 2,
    };
    const calls = mockFetch({
      "/me": () => json(ME),
      "/profile": [() => json(data), () => json({ ...data, full_name: "Jane Lim" })],
    });
    render(<Account path="/account/profile" hash="" />);
    expect(await screen.findByText(/Version 2026-11-16/)).toBeTruthy();
    expect(screen.getByText("4")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Correct my details" }));
    fireEvent.change(screen.getByLabelText("Full name"), { target: { value: "Jane Lim" } });
    fireEvent.click(screen.getByRole("button", { name: "Save details" }));
    expect(await screen.findByText("Enter your current password to confirm it's you.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "correct horse battery 7" } });
    fireEvent.click(screen.getByRole("button", { name: "Save details" }));
    expect(await screen.findByText("Your details have been updated.")).toBeTruthy();
    expect(calls.at(-1)!.body).toEqual({ current_password: "correct horse battery 7", full_name: "Jane Lim", email: "jane@example.com" });
    expect(screen.getByText(/Jane Lim ·/)).toBeTruthy();
  });
});

describe("passkeys (v2)", () => {
  afterEach(() => Object.defineProperty(navigator, "credentials", { value: undefined, configurable: true }));

  it("signs in with a passkey, without an email or password", async () => {
    stubPasskeyDevice({ get: assertion });
    const calls = mockFetch({
      "/passkeys/sign-in/options": () => json({ options: { challenge: "AAEC", rpId: "gateway.localhost" } }),
      "/passkeys/sign-in": () => json(ME),
      "/me": signedOut,
      "/payments": () => json({ data: [], has_more: false }),
    });
    render(<Account path="/account/sign-in" hash="" />);
    fireEvent.click(await screen.findByRole("button", { name: "Sign in with a passkey" }));
    expect(await screen.findByText(/Jane Tan/)).toBeTruthy();
    const body = calls.find((c) => c.url.endsWith("/passkeys/sign-in"))!.body as { credential: { id: string }; remember: boolean };
    expect(body.credential.id).toBe("AQID");
    expect(body.remember).toBe(false);
  });

  it("hides passkey sign-in where the browser has no WebAuthn", async () => {
    mockFetch({ "/me": signedOut });
    render(<Account path="/account/sign-in" hash="" />);
    await screen.findByRole("button", { name: "Sign in" });
    expect(screen.queryByRole("button", { name: "Sign in with a passkey" })).toBeNull();
  });

  it("lists, adds and removes passkeys on the account page", async () => {
    stubPasskeyDevice({ create: () => ({
      ...assertion(), response: { clientDataJSON: new Uint8Array([1]).buffer, attestationObject: new Uint8Array([2]).buffer },
    }) });
    const phone = { id: "pk_1", name: "Jane's phone", synced: true, created_at: "2026-11-28T02:00:00Z", last_used_at: null };
    const calls = mockFetch({
      "/passkeys/register/options": () => json({ options: { challenge: "AAEC", rp: { id: "x", name: "SecurePay" },
        user: { id: "Y3Vz", name: "jane@example.com", displayName: "Jane" }, pubKeyCredParams: [] } }),
      "/passkeys/register": () => json(phone, 201),
      "/passkeys/pk_1/remove": () => new Response(null, { status: 204 }),
      "/passkeys": [() => json({ data: [], limit: 10 }), () => json({ data: [phone], limit: 10 }), () => json({ data: [], limit: 10 })],
      "/me": () => json(ME),
    });
    render(<Account path="/account/passkeys" hash="" />);
    expect(await screen.findByText("You have no passkeys yet.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Name (optional)"), { target: { value: "Jane's phone" } });
    fireEvent.click(screen.getByRole("button", { name: "Add a passkey" }));
    expect(await screen.findByText(/was added/)).toBeTruthy();
    expect(await screen.findByText("Jane's phone")).toBeTruthy();
    const added = calls.find((c) => c.url.endsWith("/passkeys/register"))!.body as { name: string };
    expect(added.name).toBe("Jane's phone");
    fireEvent.click(screen.getByRole("button", { name: "Remove Jane's phone" }));
    expect(await screen.findByText(/was removed/)).toBeTruthy();
    await waitFor(() => expect(screen.getByText("You have no passkeys yet.")).toBeTruthy());
  });
});
