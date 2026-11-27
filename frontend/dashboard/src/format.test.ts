import { describe, expect, it } from "vitest";
import {
  cardLabel, featureValue, money, orderedFeatures, parseMoney, riskLabel, shortDate, statusLabel,
} from "./format";

describe("format", () => {
  it("formats SGD cents", () => {
    expect(money(8990)).toBe("S$ 89.90");
    expect(money(41200)).toBe("S$ 412.00");
    expect(money(123456789)).toBe("S$ 1,234,567.89");
  });

  it("parses amounts typed by staff", () => {
    expect(parseMoney("30")).toBe(3000);
    expect(parseMoney("30.5")).toBe(3050);
    expect(parseMoney("S$ 1,030.05")).toBe(103005);
    expect(parseMoney("30.555")).toBeNull();
    expect(parseMoney("-1")).toBeNull();
    expect(parseMoney("")).toBeNull();
  });

  it("formats and parses other currencies (v2)", () => {
    expect(money(119200, "THB")).toBe("THB 1,192.00");
    expect(money(5000, "JPY")).toBe("JPY 5,000");
    expect(parseMoney("THB 1,000.50", "THB")).toBe(100050);
    expect(parseMoney("5000", "JPY")).toBe(5000);
    expect(parseMoney("50.5", "JPY")).toBeNull();
  });

  it("shows refunds in the status as the wireframes do", () => {
    expect(statusLabel("captured", 12900, 0)).toBe("Captured");
    expect(statusLabel("captured", 12900, 2900)).toBe("Partially refunded");
    expect(statusLabel("captured", 12900, 12900)).toBe("Refunded");
    expect(statusLabel("verification_required")).toBe("Awaiting code");
  });

  it("labels risk decisions and cards", () => {
    expect(riskLabel({ decision: "verify", verification: "passed" })).toBe("Verify · passed");
    expect(riskLabel({ decision: "block", verification: null })).toBe("Block");
    expect(cardLabel({ brand: "mastercard", last4: "8812" })).toBe("MC •••• 8812");
  });

  it("shows Singapore time", () => {
    expect(shortDate("2026-11-02T06:52:00Z")).toMatch(/2 Nov.*14:52/);
  });

  it("shows screening features as an analyst reads them, in schema order", () => {
    expect(featureValue("card_seconds_since_last", -1)).toBe("No attempt in 30 days");
    expect(featureValue("card_seconds_since_last", 8.06)).toBe("8 s");
    expect(featureValue("card_seconds_since_last", 900)).toBe("15 min");
    expect(featureValue("card_mean_amount_30d", 3780)).toBe("S$ 37.80");
    expect(featureValue("amount_to_card_mean_30d", 0.791)).toBe("x 0.79");
    expect(featureValue("card_attempts_1h", 3)).toBe("3");
    expect(orderedFeatures({ zz_new: 1, card_attempts_1h: 2, amount_cents: 5 }).map(([k]) => k))
      .toEqual(["amount_cents", "card_attempts_1h", "zz_new"]);
  });
});
