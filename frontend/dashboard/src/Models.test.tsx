// Fraud models page (v2 learning loop, docs/v2/LEARNING-LOOP.md).
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });

function mockFetch(routes: Record<string, (init?: RequestInit) => Response>) {
  const calls: { key: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url.replace(/^\/internal/, "").split("?")[0]}`;
    calls.push({ key, body: init?.body ? JSON.parse(String(init.body)) : null });
    const reply = routes[key];
    if (!reply) throw new Error(`unexpected ${key}`);
    return reply(init);
  }));
  return calls;
}

const person = (role: string, label: string) => ({ id: "stf_2", email: "x@securepay.test", name: "Priya Nair", role,
  role_label: label, merchant: null });
const analyst = person("fraud_analyst", "Fraud analyst");
const admin = person("administrator", "Administrator");

const holdout = {
  attacks: 30, genuine: 25,
  champion: { attacks_stopped: 0, genuine_stopped: 0, caught_rate: 0, false_positive_rate: 0 },
  challenger: { attacks_stopped: 29, genuine_stopped: 0, caught_rate: 0.9667, false_positive_rate: 0 },
  by_scenario: { bin_attack: { attacks: 30, champion_stopped: 0, challenger_stopped: 29 } },
};
const configured = { version: "hgb-fs02-2026-11-13", role: "champion", source: "configured",
  policy: { verify: 0.1136, block: 0.2222 } };
const challenger = {
  version: "ll-20261128-0950-ab12", role: "challenger", created_at: "2026-11-28T01:50:00Z", created_by: "stf_2",
  promoted_at: null, promoted_by: null, retired_at: null, replaced: null, policy: { verify: 0.5, block: 0.9 },
  training: { rows: 220, attacks: 120, genuine: 100, scenarios: { bin_attack: 120 }, holdout },
  shadow: { screened: 130, differs: 118, attacks: 120, attacks_champion: 0, attacks_challenger: 117,
    legit: 10, legit_champion: 0, legit_challenger: 0 },
};
const data = { attacks: 120, genuine: 100, needed: 20, ready: true, feature_schema_version: "fs-0.3" };
const before = { active: configured, challenger: null, history: [], data, feature_schema_version: "fs-0.3" };
const trained = { ...before, challenger, history: [challenger] };
const promoted = {
  ...before, history: [{ ...challenger, role: "champion" }],
  active: { ...challenger, role: "champion", source: "promoted", promoted_at: "2026-11-28T02:00:00Z",
    promoted_by: "stf_9", replaced: "hgb-fs02-2026-11-13" },
};

beforeEach(() => { document.cookie = "csrftoken=tok123"; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function at(path: string) {
  window.history.pushState({}, "", `${import.meta.env.BASE_URL.replace(/\/$/, "")}${path}`);
  render(<App />);
}

describe("fraud models", () => {
  it("lets an analyst train a challenger and compare it, but not promote it", async () => {
    const calls = mockFetch({
      "GET /me": () => json(analyst),
      "GET /alerts/unread": () => json({ unread: 0, latest_at: null }),
      "GET /risk-models": () => json(before),
      "POST /risk-models/train": () => json({ trained: challenger, ...trained }, 201),
    });
    at("/models");
    expect(await screen.findByText("hgb-fs02-2026-11-13")).toBeTruthy();
    expect(screen.getByText("No challenger yet.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Train a challenger" }));
    expect((await screen.findAllByText("ll-20261128-0950-ab12")).length).toBeGreaterThan(0);
    expect(calls.some((c) => c.key === "POST /risk-models/train")).toBe(true);
    const table = screen.getByRole("table", { name: "Holdout comparison" }).textContent ?? "";
    expect(table).toContain("29 of 30 · 96.7%");
    expect(screen.getByText("117 of 120 (now: 0)")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Promote to decide" })).toBeNull();
    expect(screen.getByText("An administrator can promote it.")).toBeTruthy();
  });

  it("asks an administrator for the code before promoting", async () => {
    const calls = mockFetch({
      "GET /me": () => json(admin),
      "GET /alerts/unread": () => json({ unread: 0, latest_at: null }),
      "GET /risk-models": () => json(trained),
      "POST /risk-models/promote": () => json(promoted),
    });
    at("/models");
    fireEvent.click(await screen.findByRole("button", { name: "Promote to decide" }));
    const button = screen.getByRole("button", { name: "Promote" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Code from your authenticator app"), { target: { value: "123 456" } });
    fireEvent.click(button);
    await waitFor(() => expect(screen.getByText("Promoted")).toBeTruthy());
    expect(calls.find((c) => c.key === "POST /risk-models/promote")?.body)
      .toEqual({ version: "ll-20261128-0950-ab12", code: "123456" });
    expect(screen.getByRole("button", { name: "Roll back" })).toBeTruthy();
  });

  it("says what is missing before there is enough data", async () => {
    mockFetch({
      "GET /me": () => json(analyst),
      "GET /alerts/unread": () => json({ unread: 0, latest_at: null }),
      "GET /risk-models": () => json({ ...before, data: { ...data, attacks: 3, ready: false } }),
    });
    at("/models");
    expect(await screen.findByText(/There are 3 attacks and 100 genuine attempts/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Train a challenger" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
