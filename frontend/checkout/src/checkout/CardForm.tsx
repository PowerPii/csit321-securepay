import { useEffect, useState, type FormEvent } from "react";
import { getMe, type Customer } from "../accountApi";
import { addCard, ApiError, getSession, submit } from "../api";
import { brandName, cardBrand, countdown, digitsOnly, formatCardNumber, formatExpiry, luhnOk, money, parseExpiry } from "../format";
import { ConsentBox } from "../Privacy";
import { Field, invalidProps } from "../ui";
import { LockIcon } from "./icons";
import { useSessionExpiry, type SessionFormProps } from "./session";

const LOST_REPLY =
  "We lost the connection while sending your payment, so it may still go through. Reload this page to see the result before you do anything else. Paying again on this page won't charge you twice.";

export function CardForm({
  sessionId,
  session,
  onUpdate,
  formError,
}: SessionFormProps & { formError?: string }) {
  const [email, setEmail] = useState("");
  const [number, setNumber] = useState("");
  const [expiry, setExpiry] = useState("");
  const [cvc, setCvc] = useState("");
  const [name, setName] = useState(""); // shown as in the wireframe; not sent, the simulator does not need it
  const [errors, setErrors] = useState<Record<string, string>>(formError ? { form: formError } : {});
  const [busy, setBusy] = useState(false);
  const now = useSessionExpiry({ sessionId, session, onUpdate });
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [consent, setConsent] = useState(false);
  const brand = cardBrand(digitsOnly(number));

  useEffect(() => {
    // Signed in to a SecurePay account on this browser (C10): the payment joins its history
    // (FR-ACC-04) and the receipt email is filled in. Signed out, nothing changes.
    let live = true;
    getMe()
      .then((c) => {
        if (!live) return;
        setCustomer(c);
        setEmail((e) => e || c.email);
      })
      .catch(() => undefined);
    return () => { live = false; };
  }, []);

  function check(): Record<string, string> {
    const e: Record<string, string> = {};
    if (session.email_required && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) e.email = "Enter a valid email address.";
    if (!luhnOk(digitsOnly(number))) e.number = "Enter a valid card number.";
    if (!parseExpiry(expiry)) e.expiry = "Enter the expiry date as MM / YY.";
    if (!/^\d{3,4}$/.test(cvc)) e.cvc = "Enter the 3 or 4 digit security code.";
    if (!name.trim()) e.name = "Enter the name shown on the card.";
    if (!consent) e.privacy_consent = "Agree to the privacy notice to pay.";
    return e;
  }

  async function onSubmit(ev: FormEvent) {
    ev.preventDefault();
    const found = check();
    setErrors(found);
    if (Object.keys(found).length || busy) return;
    const exp = parseExpiry(expiry)!;
    setBusy(true);
    let current = session;
    try {
      current = await addCard(sessionId, {
        number: digitsOnly(number),
        expiry_month: exp.month,
        expiry_year: exp.year,
        cvc,
        privacy_consent: true,
        ...(session.email_required ? { email } : {}),
      });
    } catch (err) {
      const e = err instanceof ApiError ? err : new ApiError(0, "unknown_error", "Something went wrong.");
      if (e.status === 410 || e.status === 409) {
        getSession(sessionId).then((s) => onUpdate(s)).catch(() => undefined);
        return;
      }
      const field = { expiry_month: "expiry", expiry_year: "expiry" }[e.param ?? ""] ?? e.param ?? "form";
      setErrors({ [field]: e.message });
      setBusy(false);
      return;
    }
    // The card is in the vault; from here the form is replaced by "Checking your payment".
    onUpdate(current, true);
    try {
      onUpdate(await submit(sessionId));
    } catch (err) {
      const e = err instanceof ApiError ? err : new ApiError(0, "unknown_error", "Something went wrong.");
      // Reload: the payment may have gone ahead even if this reply was lost. If we can't tell,
      // say so: "try again" here would read as an invitation to pay twice. (Submitting this
      // checkout again is safe, it is the same payment, but the customer can't know that.)
      const unsure = e.status === 0 || e.status >= 500;
      const message = unsure ? LOST_REPLY : e.message;
      getSession(sessionId)
        .then((s) => onUpdate(s, false, s.result === "open" ? message : undefined))
        .catch(() => onUpdate(current, false, message));
    }
  }

  return (
    <form className="form" onSubmit={onSubmit} noValidate aria-labelledby="card-form-title">
      <h1 id="card-form-title" className="sr-only">Pay {money(session.amount, session.currency)} to {session.merchant.name} by card</h1>
      {errors.form && <p className="alert" role="alert">{errors.form}</p>}
      {customer && (
        <p className="muted small" role="status">
          Signed in as {customer.full_name}. This payment will appear in your payment history.
        </p>
      )}
      {session.email_required ? (
        <Field label="Email (for your receipt)" error={errors.email} id="email">
          <input id="email" type="email" autoComplete="email" placeholder="you@example.com"
            value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
      ) : (
        session.customer_email_masked && (
          <p className="muted small">Receipt to {session.customer_email_masked}</p>
        )
      )}
      <Field label="Card number" error={errors.number} id="number">
        <div className="card-input">
          <input id="number" inputMode="numeric" autoComplete="cc-number" placeholder="1234 5678 9012 3456"
            {...invalidProps("number", errors.number, "card-brands")}
            value={number} onChange={(e) => setNumber(formatCardNumber(e.target.value))} />
          {brand && <span className={`card-chip ${brand}`} data-testid="card-brand">{brandName(brand)}</span>}
        </div>
        <span id="card-brands" className="card-brands muted tiny">
          <span className="card-chip visa">Visa</span>
          <span className="card-chip mastercard">Mastercard</span>
          Test cards only: no real money moves.
        </span>
      </Field>
      <div className="pair">
        <Field label="Expiry" error={errors.expiry} id="expiry">
          <input id="expiry" inputMode="numeric" autoComplete="cc-exp" placeholder="MM / YY"
            value={expiry} onChange={(e) => setExpiry(formatExpiry(e.target.value))} />
        </Field>
        <Field label="CVV" error={errors.cvc} id="cvc">
          <input id="cvc" inputMode="numeric" autoComplete="cc-csc" placeholder="123" maxLength={4}
            value={cvc} onChange={(e) => setCvc(digitsOnly(e.target.value))} />
        </Field>
      </div>
      <Field label="Name on card" error={errors.name} id="name">
        <input id="name" autoComplete="cc-name" placeholder="As shown on card"
          value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <ConsentBox id="privacy-consent" checked={consent} onChange={setConsent} error={errors.privacy_consent}>
        I agree that SecurePay uses my details to process and protect this payment, as the privacy notice says.
      </ConsentBox>
      <button className="btn primary with-lock" type="submit" disabled={busy}>
        <span className="pay-label"><LockIcon /> {busy ? "Processing…" : `Pay ${money(session.amount, session.currency)}`}</span>
        {busy && <span className="pay-meter" aria-hidden="true"><b /><b /><b /></span>}
      </button>
      <a className="cancel-link" href={session.return_url}>
        Cancel and return to {session.merchant.name}
      </a>
      <p className="muted tiny">
        {/* Updates every second, so it is not a live region; the page reloads the result when it ends. */}
        Session expires in {countdown(session.expires_at, now)} · Card details are encrypted and never shared with the shop.
      </p>
    </form>
  );
}
