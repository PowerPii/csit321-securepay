import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { Session } from "./api";
import { assertion, stubPasskeyDevice } from "./testPasskeys";

const SID = "cs_" + "A".repeat(24);

function session(over: Partial<Session> = {}): Session {
  return {
    session_id: SID, status: "created", result: "open", merchant: { name: "Demo Shop" },
    amount: 8990, currency: "SGD", order_reference: "ORD-1042", description: "Wireless mouse × 1", line_items: null,
    reference: null, email_required: false, customer_email_masked: "a•••@example.com", card: null,
    expires_at: new Date(Date.now() + 3600_000).toISOString(), submitted_at: null, authorised_at: null,
    return_url: "https://shop.localhost/orders/1042/return?payment_id=pay_x", verification: null, ...over,
  };
}

function mockFetch(replies: Record<string, () => Response>) {
  const calls: { url: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const key = Object.keys(replies).find((k) => url.endsWith(k));
    // The card form asks once whether a customer is signed in; by default nobody is.
    if (url === "/customer/me" && !key) return json({ error: { code: "sign_in_required", message: "x" } }, 401);
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
    return replies[key!]();
  }));
  return calls;
}

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("checkout page", () => {
  it.each(["open", "verification_required"] as const)("refreshes an expired %s session and stops its timer", async (result) => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const expiry = new Date(Date.now() + 1000).toISOString();
      let checks = 0;
      mockFetch({ [SID]: () => {
        checks += 1;
        return json(checks > 1 ? session({ result: "expired", status: "expired" }) : session({
          result, expires_at: expiry,
          verification: result === "verification_required" ? {
            sent_to: "a•••@example.com", code_expires_at: expiry,
            attempts_remaining: 3, resend_available_at: null,
          } : null,
        }));
      } });
      const view = render(<App path={`/pay/${SID}`} />);
      await screen.findByRole("button", { name: result === "open" ? "Pay S$ 89.90" : "Verify and pay S$ 89.90" });
      await act(() => vi.advanceTimersByTimeAsync(1000));
      expect(screen.getByText("This checkout has ended")).toBeTruthy();
      expect(checks).toBe(2);
      await act(() => vi.advanceTimersByTimeAsync(2000));
      expect(checks).toBe(2);
      view.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows the order the merchant created (C3)", async () => {
    mockFetch({ [SID]: () => json(session()) });
    render(<App path={`/pay/${SID}`} />);
    expect(await screen.findByText("Demo Shop")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Pay S$ 89.90" })).toBeTruthy();
    expect(screen.getByText("Receipt to a•••@example.com")).toBeTruthy();
    expect(screen.getByText("Wireless mouse × 1")).toBeTruthy();
  });

  it("marks a test payment \"Test mode\" and a live one \"Demo\" (v2)", async () => {
    mockFetch({ [SID]: () => json(session()) });
    const view = render(<App path={`/pay/${SID}`} />);
    expect(await screen.findByText("Test mode")).toBeTruthy();
    view.unmount();
    mockFetch({ [SID]: () => json(session({ livemode: true })) });
    render(<App path={`/pay/${SID}`} />);
    expect(await screen.findByText("Demo")).toBeTruthy();
    expect(screen.queryByText("Test mode")).toBeNull();
  });

  it("tells a signed-in customer the payment joins their history and fills in the email", async () => {
    mockFetch({
      [SID]: () => json(session({ email_required: true, customer_email_masked: null })),
      "/customer/me": () => json({ id: "cus_x", full_name: "Jane Tan", email: "jane@example.com", idle_minutes: 15 }),
    });
    render(<App path={`/pay/${SID}`} />);
    expect(await screen.findByText(/Signed in as Jane Tan/)).toBeTruthy();
    expect((screen.getByLabelText("Email (for your receipt)") as HTMLInputElement).value).toBe("jane@example.com");
  });

  it("lists the line items with their amounts when the shop sent them", async () => {
    mockFetch({ [SID]: () => json(session({ line_items: [
      { name: "Wireless mouse", quantity: 1, unit_amount: 2990 },
      { name: "USB-C hub", quantity: 2, unit_amount: 3000 },
    ] })) });
    render(<App path={`/pay/${SID}`} />);
    expect(await screen.findByText("USB-C hub × 2")).toBeTruthy();
    expect(screen.getByText("S$ 60.00")).toBeTruthy();
  });

  it("checks the card locally before sending anything", async () => {
    const calls = mockFetch({ [SID]: () => json(session()) });
    render(<App path={`/pay/${SID}`} />);
    fireEvent.click(await screen.findByRole("button", { name: "Pay S$ 89.90" }));
    expect(await screen.findByText("Enter a valid card number.")).toBeTruthy();
    expect(calls).toHaveLength(1); // only the initial GET
  });

  it("sends the card, pays, and shows the approval (C6)", async () => {
    const approved = session({ status: "authorised", result: "approved", reference: "SP-7Q2K-91XD",
      card: { brand: "visa", last4: "4242" }, authorised_at: "2026-11-13T05:00:00Z" });
    const calls = mockFetch({
      "/card": () => json(session({ card: { brand: "visa", last4: "4242" } })),
      "/submit": () => json(approved),
      [SID]: () => json(session()),
    });
    render(<App path={`/pay/${SID}`} />);
    fireEvent.change(await screen.findByLabelText("Card number"), { target: { value: "4242 4242 4242 4242" } });
    fireEvent.change(screen.getByLabelText("Expiry"), { target: { value: "1230" } });
    fireEvent.change(screen.getByLabelText("CVV"), { target: { value: "123" } });
    fireEvent.change(screen.getByLabelText("Name on card"), { target: { value: "Tan Wei Ming" } });
    fireEvent.click(screen.getByRole("button", { name: "Pay S$ 89.90" }));
    // FR-ACC-12: nothing is sent until the privacy notice is agreed to.
    expect(await screen.findByText("Agree to the privacy notice to pay.")).toBeTruthy();
    expect(calls.filter((c) => c.url.endsWith("/card"))).toHaveLength(0);
    fireEvent.click(screen.getByRole("checkbox", { name: /I agree that SecurePay uses my details/ }));
    fireEvent.click(screen.getByRole("button", { name: "Pay S$ 89.90" }));
    expect(await screen.findByText("Payment successful")).toBeTruthy();
    expect(screen.getByText("SP-7Q2K-91XD")).toBeTruthy();
    const cardCall = calls.find((c) => c.url.endsWith("/card"))!;
    expect(cardCall.body).toEqual({ number: "4242424242424242", expiry_month: 12, expiry_year: 2030, cvc: "123", privacy_consent: true });
  });

  it("never reads a lost pay reply as a failure to try again", async () => {
    mockFetch({
      "/card": () => json(session({ card: { brand: "visa", last4: "4242" } })),
      "/submit": () => { throw new TypeError("connection reset"); },
      [SID]: () => json(session({ card: { brand: "visa", last4: "4242" } })),
    });
    render(<App path={`/pay/${SID}`} />);
    fireEvent.change(await screen.findByLabelText("Card number"), { target: { value: "4242 4242 4242 4242" } });
    fireEvent.change(screen.getByLabelText("Expiry"), { target: { value: "1230" } });
    fireEvent.change(screen.getByLabelText("CVV"), { target: { value: "123" } });
    fireEvent.change(screen.getByLabelText("Name on card"), { target: { value: "Tan Wei Ming" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /I agree that SecurePay uses my details/ }));
    fireEvent.click(screen.getByRole("button", { name: "Pay S$ 89.90" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("it may still go through");
    expect(alert.textContent).toContain("won't charge you twice");
    expect(alert.textContent).not.toMatch(/try again/i);
  });

  it("explains a decline in plain words (C7)", async () => {
    mockFetch({ [SID]: () => json(session({ status: "declined", result: "declined", reference: "SP-AAAA-BBBB" })) });
    render(<App path={`/pay/${SID}`} />);
    expect(await screen.findByText("Payment declined")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Try another card" }).getAttribute("href")).toContain("payment_id=");
  });

  it("asks the customer not to pay again when the bank did not answer", async () => {
    mockFetch({ [SID]: () => json(session({ status: "unresolved", result: "pending", reference: "SP-AAAA-BBBB" })) });
    render(<App path={`/pay/${SID}`} />);
    expect(await screen.findByText("We're checking your payment")).toBeTruthy();
    expect(document.body.textContent).toContain("Please don't pay again");
    // The receipt's reference and status page, never a "try again" or a new payment.
    expect(screen.getByText("Payment reference SP-AAAA-BBBB")).toBeTruthy();
    expect(screen.getByRole("link", { name: /check this payment's status/i }).getAttribute("href"))
      .toBe("/guest/status?ref=SP-AAAA-BBBB");
    expect(screen.queryByRole("link", { name: /try (again|another card)/i })).toBeNull();
    expect(document.body.textContent).not.toMatch(/try again/i);
  });

  it("shows the result once reconciliation settles a pending payment", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let checks = 0;
      mockFetch({
        [SID]: () => {
          checks += 1;
          return checks < 2
            ? json(session({ status: "unresolved", result: "pending", reference: "SP-AAAA-BBBB" }))
            : json(session({ status: "authorised", result: "approved", reference: "SP-AAAA-BBBB",
                card: { brand: "visa", last4: "0119" }, authorised_at: new Date().toISOString() }));
        },
      });
      render(<App path={`/pay/${SID}`} />);
      expect(await screen.findByText("We're checking your payment")).toBeTruthy();
      await vi.advanceTimersByTimeAsync(10_000);
      expect(await screen.findByText("Payment successful")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("asks for the emailed code, shows a wrong code, then pays (C5)", async () => {
    const waiting = (attempts: number) => session({
      status: "verification_required", result: "verification_required", reference: "SP-AAAA-BBBB",
      card: { brand: "visa", last4: "4242" },
      verification: { sent_to: "a•••@example.com", code_expires_at: new Date(Date.now() + 600_000).toISOString(),
        attempts_remaining: attempts, resend_available_at: new Date(Date.now() + 30_000).toISOString() },
    });
    let verifyReplies = 0;
    let attempts = 3;
    const calls = mockFetch({
      "/verify": () => {
        verifyReplies += 1;
        if (verifyReplies === 1) {
          attempts = 2;
          return json({ error: { code: "code_incorrect", message: "That code isn't right. 2 attempts left.", param: "code" } }, 400);
        }
        return json(session({ status: "authorised", result: "approved", reference: "SP-AAAA-BBBB", authorised_at: "2026-11-13T05:00:00Z" }));
      },
      [SID]: () => json(waiting(attempts)),
    });
    render(<App path={`/pay/${SID}`} />);
    expect(await screen.findByText("Check your email")).toBeTruthy();
    expect(screen.getByText(/sent to a•••@example.com/)).toBeTruthy();
    expect((screen.getByRole("button", { name: /Send a new code in/ }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("Verification code"), { target: { value: "12a3" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify and pay S$ 89.90" }));
    expect(await screen.findByText("Enter the 6-digit code from the email.")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Verification code"), { target: { value: "111111" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify and pay S$ 89.90" }));
    expect(await screen.findByText("That code isn't right. 2 attempts left.")).toBeTruthy();
    await waitFor(() => expect(screen.getByText(/2 attempts left$/)).toBeTruthy());

    fireEvent.change(screen.getByLabelText("Verification code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify and pay S$ 89.90" }));
    expect(await screen.findByText("Payment successful")).toBeTruthy();
    expect(calls.filter((c) => c.url.endsWith("/verify")).map((c) => c.body)).toEqual([{ code: "111111" }, { code: "123456" }]);
  });

  it("shows the bank's 3-D Secure challenge and sends its code (v2)", async () => {
    const calls = mockFetch({
      "/verify": () => json(session({ status: "authorised", result: "approved", reference: "SP-AAAA-BBBB", authorised_at: "2026-11-13T05:00:00Z" })),
      [SID]: () => json(session({
        status: "verification_required", result: "verification_required", reference: "SP-AAAA-BBBB",
        card: { brand: "visa", last4: "3220" },
        verification: { method: "three_ds", passkey: false, code_sent: true, sent_to: null,
          code_expires_at: new Date(Date.now() + 600_000).toISOString(), attempts_remaining: 3, resend_available_at: null,
          issuer_challenge: { bank: "SecurePay Bank", card_last4: "3220", phone_hint: "•••• 21", simulated_text_code: "135790" } },
      })),
    });
    render(<App path={`/pay/${SID}`} />);
    expect(await screen.findByText("Confirm this payment")).toBeTruthy();
    expect(screen.getByText(/phone ending 21/)).toBeTruthy();
    expect(screen.getByRole("note", { name: "Simulated text message" }).textContent).toContain("135790");
    fireEvent.change(screen.getByLabelText("Code from your bank"), { target: { value: "135790" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm with my bank" }));
    expect(await screen.findByText("Payment successful")).toBeTruthy();
    expect(calls.filter((c) => c.url.endsWith("/verify")).map((c) => c.body)).toEqual([{ code: "135790" }]);
  });

  it("lets a signed-in shopper approve with a passkey instead of the code (v2)", async () => {
    stubPasskeyDevice({ get: assertion });
    const calls = mockFetch({
      "/verify/passkey/options": () => json({ options: { challenge: "AAEC", rpId: "gateway.localhost", allowCredentials: [] } }),
      "/verify/passkey": () => json(session({ status: "authorised", result: "approved", reference: "SP-AAAA-BBBB", authorised_at: "2026-11-13T05:00:00Z" })),
      [SID]: () => json(session({
        status: "verification_required", result: "verification_required", reference: "SP-AAAA-BBBB",
        verification: { passkey: true, code_sent: false, sent_to: null, code_expires_at: new Date(Date.now() + 600_000).toISOString(),
          attempts_remaining: 3, resend_available_at: new Date(Date.now() - 1000).toISOString() },
      })),
    });
    render(<App path={`/pay/${SID}`} />);
    expect(await screen.findByText("Approve with your passkey")).toBeTruthy();
    expect(screen.queryByLabelText("Verification code")).toBeNull();
    expect(screen.getByRole("button", { name: "Email me a code instead" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Approve and pay S\$ 89.90/ }));
    expect(await screen.findByText("Payment successful")).toBeTruthy();
    const sent = calls.find((c) => c.url.endsWith("/verify/passkey"))!.body as { credential: { id: string } };
    expect(sent.credential.id).toBe("AQID");
  });

  it("tells the customer to try later when screening was unavailable", async () => {
    mockFetch({ [SID]: () => json(session({ status: "blocked", result: "unavailable", reference: "SP-AAAA-BBBB" })) });
    render(<App path={`/pay/${SID}`} />);
    expect(await screen.findByText("We couldn't check this payment right now")).toBeTruthy();
    expect(screen.queryByText(/fraud/i)).toBeNull();
  });

  it("says the code was not confirmed when verification failed, without naming fraud", async () => {
    mockFetch({ [SID]: () => json(session({ status: "blocked", result: "verification_failed", reference: "SP-AAAA-BBBB" })) });
    render(<App path={`/pay/${SID}`} />);
    expect(await screen.findByText("We couldn't confirm it was you")).toBeTruthy();
    expect(screen.getByText(/No money was taken/)).toBeTruthy();
    expect(screen.queryByText(/fraud/i)).toBeNull();
  });

  it("shows the shop's monogram and the card brand as the number is typed", async () => {
    mockFetch({ [SID]: () => json(session({ merchant: { name: "Volt & Co." } })) });
    render(<App path={`/pay/${SID}`} />);
    expect(await screen.findByText("VC")).toBeTruthy();
    expect(screen.queryByTestId("card-brand")).toBeNull();
    fireEvent.change(screen.getByLabelText("Card number"), { target: { value: "5555 5555" } });
    expect(screen.getByTestId("card-brand").textContent).toBe("Mastercard");
    fireEvent.change(screen.getByLabelText("Card number"), { target: { value: "4242" } });
    expect(screen.getByTestId("card-brand").textContent).toBe("Visa");
  });

  it("folds the order details behind a toggle (phones show it, CSS hides it on desktop)", async () => {
    mockFetch({ [SID]: () => json(session({ line_items: [
      { name: "Wireless mouse", quantity: 1, unit_amount: 2990 },
      { name: "USB-C hub", quantity: 2, unit_amount: 3000 },
    ] })) });
    render(<App path={`/pay/${SID}`} />);
    const toggle = await screen.findByRole("button", { name: "Show order details (3 items)" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Hide order details (3 items)" }).getAttribute("aria-expanded")).toBe("true");
  });

  it("handles an unknown link", async () => {
    mockFetch({ [SID]: () => json({ error: { code: "resource_not_found" } }, 404) });
    render(<App path={`/pay/${SID}`} />);
    await waitFor(() => expect(screen.getByText("This checkout link isn't valid")).toBeTruthy());
  });
});

describe("privacy policy link", () => {
  it("points at the website next to the gateway", async () => {
    const { privacyPolicyUrl } = await import("./Privacy");
    expect(privacyPolicyUrl("gateway.securepaysg.me")).toBe("https://www.securepaysg.me/privacy");
    expect(privacyPolicyUrl("gateway.example.org")).toBe("https://www.example.org/privacy");
    expect(privacyPolicyUrl("gateway.localhost")).toBe("https://www.securepaysg.me/privacy");
  });
});
