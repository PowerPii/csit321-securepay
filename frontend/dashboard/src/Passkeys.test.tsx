import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { assertion, stubPasskeyDevice } from "./testPasskeys";

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
    const key = `${method} ${url.replace(/^\/internal/, "").split("?")[0]}`;
    const reply = routes[key];
    if (!reply) return json({}, 200);
    if (Array.isArray(reply)) {
      const i = Math.min(used[key] ?? 0, reply.length - 1);
      used[key] = (used[key] ?? 0) + 1;
      return reply[i](init);
    }
    return reply(init);
  }));
  return calls;
}

const analyst = {
  id: "stf_2", email: "analyst@securepay.sg", name: "Daniel Lim", role: "fraud_analyst",
  role_label: "Fraud analyst", merchant: null, backup_codes_left: 10,
};
const overview = { today: {}, chart: [], latest: [] };

beforeEach(() => { document.cookie = "csrftoken=tok123"; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function at(path: string) {
  window.history.pushState({}, "", `${import.meta.env.BASE_URL.replace(/\/$/, "")}${path}`);
  render(<App />);
}

async function passwordStep(setup: unknown = null) {
  fireEvent.change(await screen.findByLabelText(/Email/), { target: { value: "analyst@securepay.sg" } });
  fireEvent.change(screen.getByLabelText(/Password/), { target: { value: "correct horse" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByText("Two-step verification")).toBeTruthy();
  return setup;
}

afterEach(() => Object.defineProperty(navigator, "credentials", { value: undefined, configurable: true }));

describe("staff passkeys (v2): password, then passkey", () => {
  it("uses a passkey in place of the authenticator code", async () => {
    stubPasskeyDevice({ get: assertion });
    const calls = mockFetch({
      "GET /me": [unauthorised, unauthorised, () => json(analyst)],
      "POST /auth/login": () => json({ next: "totp", email: analyst.email, setup: null, passkey: true }),
      "POST /auth/passkey/options": () => json({ options: { challenge: "AAEC", rpId: "gateway.localhost", allowCredentials: [] } }),
      "POST /auth/passkey": () => json(analyst),
      "GET /overview": () => json(overview),
    });
    at("/login");
    await passwordStep();
    fireEvent.click(await screen.findByRole("button", { name: "Use your passkey instead" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/internal/auth/passkey")).toBe(true));
    const sent = calls.find((c) => c.url === "/internal/auth/passkey")!.body as { credential: { id: string } };
    expect(sent.credential.id).toBe("AQID");
    expect(calls.some((c) => c.url === "/internal/auth/totp" && c.method === "POST")).toBe(false);
  });

  it("does not offer a passkey to staff without one", async () => {
    stubPasskeyDevice({});
    mockFetch({
      "GET /me": unauthorised,
      "POST /auth/login": () => json({ next: "totp", email: analyst.email, setup: null, passkey: false }),
    });
    at("/login");
    await passwordStep();
    expect(screen.queryByRole("button", { name: "Use your passkey instead" })).toBeNull();
  });

  it("adds a passkey on the Two-step page with the authenticator code", async () => {
    stubPasskeyDevice({ create: () => ({
      ...assertion(), response: { clientDataJSON: new Uint8Array([1]).buffer, attestationObject: new Uint8Array([2]).buffer },
    }) });
    const laptop = { id: "pk_1", name: "Work laptop", synced: false, created_at: "2026-11-28T02:00:00Z", last_used_at: null };
    const calls = mockFetch({
      "GET /me": () => json(analyst),
      "GET /me/backup-codes": () => json({ remaining: 10, total: 10, generated_at: "2026-11-17T06:00:00Z" }),
      "GET /me/passkeys": [() => json({ data: [], limit: 10 }), () => json({ data: [laptop], limit: 10 })],
      "POST /me/passkeys/register/options": () => json({ options: { challenge: "AAEC", rp: { id: "x", name: "SecurePay" },
        user: { id: "c3Rm", name: analyst.email, displayName: "Daniel" }, pubKeyCredParams: [] } }),
      "POST /me/passkeys/register": () => json(laptop, 201),
    });
    at("/two-step");
    expect(await screen.findByText("You have no passkeys yet.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Add a passkey" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText(/Name/), { target: { value: "Work laptop" } });
    fireEvent.change(within(dialog).getByLabelText(/authenticator app/), { target: { value: "123 456" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add passkey" }));
    expect(await screen.findByText("Work laptop")).toBeTruthy();
    const body = calls.find((c) => c.url === "/internal/me/passkeys/register")!.body as { code: string; name: string };
    expect(body).toMatchObject({ code: "123456", name: "Work laptop" });
  });
});
