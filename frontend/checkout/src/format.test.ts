import { describe, expect, it } from "vitest";
import { cardBrand, countdown, initials, formatCardNumber, formatExpiry, luhnOk, money, parseExpiry, sessionIdFromPath } from "./format";

describe("sessionIdFromPath", () => {
  it("reads the session id from the hosted checkout URL", () => {
    expect(sessionIdFromPath("/pay/cs_" + "a".repeat(24))).toBe("cs_" + "a".repeat(24));
    expect(sessionIdFromPath("/pay/cs_short")).toBeNull();
    expect(sessionIdFromPath("/elsewhere")).toBeNull();
  });
});

describe("money", () => {
  it("formats integer cents as SGD", () => {
    expect(money(8990)).toBe("S$ 89.90");
    expect(money(50)).toBe("S$ 0.50");
    expect(money(1000000)).toBe("S$ 10,000.00");
  });
  it("formats other currencies with their code (v2)", () => {
    expect(money(119200, "THB")).toBe("THB 1,192.00");
    expect(money(5000, "JPY")).toBe("JPY 5,000");
    expect(money(2000000, "IDR")).toBe("IDR 2,000,000");
    expect(money(1999, "USD")).toBe("USD 19.99");
  });
});

describe("card input helpers", () => {
  it("groups card digits and strips anything else", () => {
    expect(formatCardNumber("4242424242424242")).toBe("4242 4242 4242 4242");
    expect(formatCardNumber("4242-42x")).toBe("4242 42");
  });
  it("checks Luhn", () => {
    expect(luhnOk("4242424242424242")).toBe(true);
    expect(luhnOk("4242424242424241")).toBe(false);
    expect(luhnOk("4242")).toBe(false);
  });
  it("formats and parses the expiry", () => {
    expect(formatExpiry("1230")).toBe("12 / 30");
    expect(parseExpiry("12 / 30")).toEqual({ month: 12, year: 2030 });
    expect(parseExpiry("13 / 30")).toBeNull();
    expect(parseExpiry("1")).toBeNull();
  });
  it("counts down and stops at zero", () => {
    const now = Date.parse("2026-11-13T05:00:00Z");
    expect(countdown("2026-11-13T05:58:41Z", now)).toBe("58:41");
    expect(countdown("2026-11-13T04:00:00Z", now)).toBe("00:00");
  });
});

describe("store and card helpers (demo sprint 14)", () => {
  it("finds the card brand like the vault does", () => {
    expect(cardBrand("4242")).toBe("visa");
    expect(cardBrand("5555")).toBe("mastercard");
    expect(cardBrand("2223")).toBe("mastercard");
    expect(cardBrand("22")).toBeNull();
    expect(cardBrand("3782")).toBe("amex");
    expect(cardBrand("6011")).toBeNull();
  });
  it("makes a two-letter monogram", () => {
    expect(initials("Volt & Co.")).toBe("VC");
    expect(initials("Demo Shop")).toBe("DS");
    expect(initials("Gadgets")).toBe("GA");
    expect(initials("")).toBe("?");
  });
});
