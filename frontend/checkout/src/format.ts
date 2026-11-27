// Small pure helpers for the checkout page (tested in format.test.ts).

export function sessionIdFromPath(path: string): string | null {
  return path.match(/^\/pay\/(cs_[0-9A-Za-z]{24})\/?$/)?.[1] ?? null;
}

// v2 multi-currency: currencies whose smallest unit is the whole unit (docs/v2/FX.md).
const NO_MINOR_UNIT = new Set(["JPY", "IDR"]);

/** 8990 -> "S$ 89.90"; (119200, "THB") -> "THB 1,192.00"; (5000, "JPY") -> "JPY 5,000". */
export function money(amount: number, currency = "SGD"): string {
  const prefix = currency === "SGD" ? "S$" : currency;
  if (NO_MINOR_UNIT.has(currency)) return `${prefix} ${amount.toLocaleString("en-SG")}`;
  const whole = Math.floor(amount / 100).toLocaleString("en-SG");
  return `${prefix} ${whole}.${String(amount % 100).padStart(2, "0")}`;
}

export function digitsOnly(value: string): string {
  return value.replace(/\D/g, "");
}

/** Group digits in fours as the customer types: "4242424242424242" -> "4242 4242 4242 4242". */
export function formatCardNumber(value: string): string {
  return digitsOnly(value).slice(0, 19).replace(/(\d{4})(?=\d)/g, "$1 ");
}

/** "1230" or "12/30" -> "12 / 30". */
export function formatExpiry(value: string): string {
  const d = digitsOnly(value).slice(0, 4);
  return d.length > 2 ? `${d.slice(0, 2)} / ${d.slice(2)}` : d;
}

export function parseExpiry(value: string): { month: number; year: number } | null {
  const d = digitsOnly(value);
  if (d.length !== 4) return null;
  const month = Number(d.slice(0, 2));
  if (month < 1 || month > 12) return null;
  return { month, year: 2000 + Number(d.slice(2)) };
}

export function luhnOk(digits: string): boolean {
  let sum = 0;
  for (let i = 0; i < digits.length; i++) {
    let d = Number(digits[digits.length - 1 - i]);
    if (i % 2 === 1) {
      d *= 2;
      if (d > 9) d -= 9;
    }
    sum += d;
  }
  return digits.length >= 13 && digits.length <= 19 && sum % 10 === 0;
}

/** Remaining time as "mm:ss", never negative. */
export function countdown(expiresAt: string, now: number): string {
  const s = Math.max(0, Math.floor((Date.parse(expiresAt) - now) / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/** Card brand from the first digits, the same rule as the gateway's vault (brand_for). */
export function cardBrand(digits: string): "visa" | "mastercard" | "amex" | null {
  if (digits.startsWith("4")) return "visa";
  const two = Number(digits.slice(0, 2));
  const four = Number(digits.slice(0, 4));
  if ((two >= 51 && two <= 55) || (digits.length >= 4 && four >= 2221 && four <= 2720)) return "mastercard";
  if (two === 34 || two === 37) return "amex";
  return null;
}

/** "Volt & Co." -> "VC": the shop's monogram on the payment page. */
export function initials(name: string): string {
  const [first = "?", second] = name.match(/[A-Za-z0-9]+/g) ?? [];
  return (second ? first.charAt(0) + second.charAt(0) : first.slice(0, 2)).toUpperCase();
}

export function brandName(brand: string): string {
  return { visa: "Visa", mastercard: "Mastercard", amex: "Amex" }[brand] ?? "Card";
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("en-SG", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

/** Only a charged payment (approved or captured) has a receipt to download. */
export function hasReceipt(status: string): boolean {
  return status === "authorised" || status === "captured";
}
