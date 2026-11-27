// Red team live view (v2, docs/v2/RED-TEAM.md).
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { visibleBuckets } from "./routes/RedTeam";

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });

function mockFetch(routes: Record<string, () => Response>) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(url);
    const key = `${init?.method ?? "GET"} ${url.replace(/^\/internal/, "").split("?")[0]}`;
    const reply = routes[key];
    if (!reply) throw new Error(`unexpected ${key}`);
    return reply();
  }));
  return calls;
}

const analyst = { id: "stf_2", email: "priya@securepay.test", name: "Priya Nair", role: "fraud_analyst",
  role_label: "Fraud analyst", merchant: null };
const operator = { ...analyst, id: "stf_1", role: "merchant_operator", role_label: "Merchant operator",
  merchant: { id: "mer_1", name: "Demo Shop" } };

const run = (id: string, scenario: string, live: boolean) => ({
  id, object: "red_team_run", scenario, note: null, started_at: "2026-11-28T02:00:00Z",
  expires_at: "2026-11-28T03:00:00Z", ended_at: live ? null : "2026-11-28T02:00:30Z", live,
});
const empty = { attacks: 0, attacks_blocked: 0, attacks_verify: 0, attacks_allowed: 0, legit: 0, legit_stopped: 0 };
const live = (id: string, scenario: string) => ({
  run: run(id, scenario, true), bucket_seconds: 10,
  buckets: [
    { ...empty, at: "2026-11-28T02:00:00Z", attacks: 12, attacks_allowed: 10, attacks_blocked: 2, legit: 3 },
    { ...empty, at: "2026-11-28T02:00:10Z", attacks: 15, attacks_allowed: 3, attacks_blocked: 12, legit: 2 },
  ],
  scenarios: [{ scenario, attacks: 27, blocked: 14, verify: 0, allowed: 13, pending: 0 }],
  totals: { attacks: 27, attacks_screened: 27, stopped: 14, legit: 5, legit_stopped: 0,
    caught_rate: 0.5, false_positive_rate: 0 },
});

beforeEach(() => { document.cookie = "csrftoken=tok123"; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function at(path: string) {
  window.history.pushState({}, "", `${import.meta.env.BASE_URL.replace(/\/$/, "")}${path}`);
  render(<App />);
}

describe("red team", () => {
  it("shows the latest run's score and switches runs", async () => {
    const calls = mockFetch({
      "GET /me": () => json(analyst),
      "GET /alerts/unread": () => json({ unread: 0, latest_at: null }),
      "GET /red-team/runs": () => json({ object: "list", enabled: true, data: [
        { ...run("rt_new", "card_testing", true), attacks: 27 },
        { ...run("rt_old", "bin_attack", false), attacks: 4 }] }),
      "GET /red-team/runs/rt_new": () => json(live("rt_new", "card_testing")),
      "GET /red-team/runs/rt_old": () => json(live("rt_old", "bin_attack")),
    });
    at("/red-team");
    expect(await screen.findByText("50.0%")).toBeTruthy();
    expect(screen.getByText("0 of 5 · 0.0%")).toBeTruthy();
    expect(screen.getByRole("img", { name: /27 attacks, 14 stopped/ })).toBeTruthy();
    expect(screen.getByRole("table", { name: "Attacks by pattern" }).textContent).toContain("card testing");
    expect(screen.getByText("Live")).toBeTruthy();

    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Run" }));
    fireEvent.click(await screen.findByRole("option", { name: /bin attack/ }));
    await waitFor(() => expect(calls).toContain("/internal/red-team/runs/rt_old"));
  });

  it("says how to start a run when there is none", async () => {
    mockFetch({
      "GET /me": () => json(analyst),
      "GET /alerts/unread": () => json({ unread: 0, latest_at: null }),
      "GET /red-team/runs": () => json({ object: "list", enabled: false, data: [] }),
    });
    at("/red-team");
    expect(await screen.findByText(/turned off on this gateway/)).toBeTruthy();
    expect(screen.getByText(/No runs yet/)).toBeTruthy();
  });

  it("is not offered to a merchant operator", async () => {
    mockFetch({
      "GET /me": () => json(operator),
      "GET /payments": () => json({ count: 0, page: 1, page_size: 20, data: [],
        summary: { awaiting_capture: 0, unresolved: 0, blocked_today: 0, refunded_this_month: 0, escalated: null } }),
    });
    at("/red-team");
    await waitFor(() => expect(window.location.pathname).toMatch(/\/payments$/));
    expect(screen.queryByRole("link", { name: "Red team" })).toBeNull();
  });

  it("draws an ended run only up to its last payment", () => {
    const b = (n: number) => ({ attacks: n, legit: 0 });
    expect(visibleBuckets([b(1), b(2), b(0), b(0)], false)).toHaveLength(2);
    expect(visibleBuckets([b(1), b(2), b(0), b(0)], true)).toHaveLength(4);
    expect(visibleBuckets(Array.from({ length: 100 }, () => b(1)), true)).toHaveLength(60);
  });
});
