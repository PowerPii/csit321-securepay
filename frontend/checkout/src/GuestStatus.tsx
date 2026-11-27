// Guest payment status, screen C9 (contracts/API-CONTRACT.md section 8, FR-ACC-05/06).
// Step 1: payment reference + checkout email. Step 2: the 6-digit code we emailed. Then a
// read-only view of that one payment for 15 minutes. The receipt link (?ref=SP-...) only
// fills in the reference. No order contents and no fraud reasons are ever shown here.
import { useEffect, useState, type FormEvent } from "react";
import { Field, Row, Shell, useFocusHeadingOnChange } from "./ui";
import { brandName, countdown, formatDate, hasReceipt, money } from "./format";
import { getGuestPayment, GuestApiError, requestCode, signOut, verifyCode, type GuestPayment } from "./guestApi";

type Step =
  | { kind: "loading" }
  | { kind: "find"; notice?: string }
  | { kind: "code"; challengeId: string; expiresAt: string; message: string }
  | { kind: "locked"; message: string }
  | { kind: "view"; payment: GuestPayment };

export function referenceFromSearch(search: string): string {
  const ref = new URLSearchParams(search).get("ref") ?? "";
  return /^[A-Za-z0-9 -]{1,32}$/.test(ref) ? ref.toUpperCase() : "";
}

const asError = (err: unknown) =>
  err instanceof GuestApiError ? err : new GuestApiError(0, "unknown_error", "Something went wrong.", null, null, null);

export default function GuestStatus({ search = window.location.search }: { search?: string }) {
  const [step, setStep] = useState<Step>({ kind: "loading" });
  const [reference, setReference] = useState(referenceFromSearch(search));
  const [email, setEmail] = useState("");
  const panel = useFocusHeadingOnChange(step.kind === "loading" ? "find" : step.kind);

  useEffect(() => {
    // A view unlocked in the last 15 minutes is still on this browser's cookie.
    getGuestPayment()
      .then((payment) => setStep({ kind: "view", payment }))
      .catch(() => setStep({ kind: "find" }));
  }, []);

  let body;
  if (step.kind === "loading") body = <p className="muted center">Loading…</p>;
  else if (step.kind === "find")
    body = (
      <FindForm reference={reference} email={email} notice={step.notice}
        onChange={(r, e) => { setReference(r); setEmail(e); }}
        onSent={(c) => setStep({ kind: "code", challengeId: c.challenge_id, message: c.message,
          expiresAt: new Date(Date.now() + c.expires_in_seconds * 1000).toISOString() })}
        onLocked={(message) => setStep({ kind: "locked", message })} />
    );
  else if (step.kind === "code")
    body = (
      <CodeForm step={step} reference={reference} email={email}
        onEdit={() => setStep({ kind: "find" })}
        onResent={(c) => setStep({ kind: "code", challengeId: c.challenge_id, message: c.message,
          expiresAt: new Date(Date.now() + c.expires_in_seconds * 1000).toISOString() })}
        onView={(payment) => setStep({ kind: "view", payment })}
        onLocked={(message) => setStep({ kind: "locked", message })} />
    );
  else if (step.kind === "locked")
    body = (
      <div className="form">
        <div className="box grey" role="alert">
          <h1 className="with-icon">Status check locked for 15 minutes</h1>
          <p className="muted">{step.message}</p>
        </div>
        <button className="btn secondary" type="button" onClick={() => setStep({ kind: "find" })}>
          Start again
        </button>
      </div>
    );
  else
    body = (
      <PaymentView payment={step.payment}
        onDone={() => {
          signOut().catch(() => undefined);
          setStep({ kind: "find", notice: "You have left the payment view on this browser." });
        }} />
    );

  return (
    <Shell status="Payment status">
      <section className="panel-right only status-page" ref={panel}>{body}</section>
    </Shell>
  );
}

function FindForm({ reference, email, notice, onChange, onSent, onLocked }: {
  reference: string;
  email: string;
  notice?: string;
  onChange: (reference: string, email: string) => void;
  onSent: (c: { challenge_id: string; message: string; expires_in_seconds: number }) => void;
  onLocked: (message: string) => void;
}) {
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  async function onSubmit(ev: FormEvent) {
    ev.preventDefault();
    const e: Record<string, string> = {};
    if (!reference.trim()) e.reference = "Enter the payment reference from your receipt.";
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) e.email = "Enter the email address you paid with.";
    setErrors(e);
    if (Object.keys(e).length || busy) return;
    setBusy(true);
    try {
      onSent(await requestCode(reference.trim(), email.trim()));
    } catch (err) {
      const x = asError(err);
      if (x.code === "status_check_locked") onLocked(x.message);
      else setErrors({ [x.param ?? "form"]: x.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="form" onSubmit={onSubmit} noValidate>
      <h1>Check a payment</h1>
      <p className="muted">
        Enter the reference from your receipt and the email address you paid with. We'll email you a code to
        open the payment. A reference alone never shows a payment.
      </p>
      {notice && <p className="muted small" role="status">{notice}</p>}
      {errors.form && <p className="alert" role="alert">{errors.form}</p>}
      <div className="box step">
        <p className="step-label">Step 1 · Find your payment</p>
        <Field label="Payment reference" error={errors.reference} id="reference">
          <input id="reference" autoComplete="off" placeholder="SP-XXXX-XXXX" maxLength={32}
            value={reference} onChange={(ev) => onChange(ev.target.value.toUpperCase(), email)} />
        </Field>
        <Field label="Email" error={errors.email} id="email">
          <input id="email" type="email" autoComplete="email" placeholder="you@example.com"
            value={email} onChange={(ev) => onChange(reference, ev.target.value)} />
        </Field>
      </div>
      <button className="btn primary" type="submit" disabled={busy}>Email me a code</button>
    </form>
  );
}

function CodeForm({ step, reference, email, onEdit, onResent, onView, onLocked }: {
  step: Extract<Step, { kind: "code" }>;
  reference: string;
  email: string;
  onEdit: () => void;
  onResent: (c: { challenge_id: string; message: string; expires_in_seconds: number }) => void;
  onView: (p: GuestPayment) => void;
  onLocked: (message: string) => void;
}) {
  const [code, setCode] = useState("");
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  async function onSubmit(ev: FormEvent) {
    ev.preventDefault();
    if (!/^\d{6}$/.test(code)) {
      setError("Enter the 6-digit code from the email.");
      return;
    }
    if (busy) return;
    setBusy(true);
    try {
      onView(await verifyCode(step.challengeId, code));
    } catch (err) {
      const x = asError(err);
      if (x.code === "status_check_locked") onLocked(x.message);
      else {
        setError(x.message);
        setCode("");
      }
    } finally {
      setBusy(false);
    }
  }

  async function onResend() {
    setBusy(true);
    try {
      const c = await requestCode(reference.trim(), email.trim());
      setError(undefined);
      setCode("");
      setNotice("If the details match, we sent a new code. Earlier codes no longer work.");
      onResent(c);
    } catch (err) {
      setError(asError(err).message);
    } finally {
      setBusy(false);
    }
  }

  const left = Date.parse(step.expiresAt) - now;
  return (
    <form className="form" onSubmit={onSubmit} noValidate>
      <div className="box step">
        <p className="step-label">Step 1 · Find your payment</p>
        <dl>
          <Row label="Payment reference">{reference}</Row>
          <Row label="Email">{email}</Row>
        </dl>
        <p className="muted tiny">
          {step.message}{" "}
          <button className="link" type="button" onClick={onEdit}>Edit</button>
        </p>
      </div>
      {notice && <p className="muted small" role="status">{notice}</p>}
      <h1 className="step-label">Step 2 · Enter the 6-digit code</h1>
      <Field label="Code from the email" error={error} id="code">
        <input id="code" className="code-input" inputMode="numeric" autoComplete="one-time-code"
          placeholder="123456" maxLength={6} value={code}
          onChange={(ev) => setCode(ev.target.value.replace(/\D/g, "").slice(0, 6))} />
      </Field>
      <p className="muted tiny center-text">
        {left > 0 ? `Code expires in ${countdown(step.expiresAt, now)}` : "This code has expired."}
        {" · "}
        <button className="link" type="button" onClick={onResend} disabled={busy}>Resend code</button>
      </p>
      <button className="btn primary" type="submit" disabled={busy}>View payment status</button>
    </form>
  );
}

function PaymentView({ payment, onDone }: { payment: GuestPayment; onDone: () => void }) {
  const unresolved = payment.status === "unresolved";
  return (
    <div className="form">
      <div className="view-head">
        <h1>{money(payment.amount, payment.currency)} to {payment.merchant_name}</h1>
        <span className={`badge${payment.status_label === "Paid" ? " dark" : ""}`}>{payment.status_label}</span>
      </div>
      {unresolved && (
        <p className="alert" role="status">
          We are still confirming this payment with the bank. Please don't pay again; this page shows the result
          once we know it.
        </p>
      )}
      <dl className="box">
        <Row label="Payment reference">{payment.reference}</Row>
        {payment.card && <Row label="Card">{brandName(payment.card.brand)} •••• {payment.card.last4}</Row>}
        <Row label="Date">{formatDate(payment.created_at)}</Row>
        {payment.refunded_amount ? <Row label="Refunded">{money(payment.refunded_amount, payment.currency)}</Row> : null}
      </dl>
      <h2 className="small-heading">Timeline</h2>
      <ol className="timeline">
        {payment.timeline.map((t, i) => (
          <li key={i}>
            <span>{t.label}</span>
            <span className="muted small">{formatDate(t.at)}</span>
          </li>
        ))}
      </ol>
      <p className="muted tiny">
        Read-only view of this payment only. For questions about your order, contact {payment.merchant_name}.
      </p>
      {hasReceipt(payment.status) && (
        <a className="btn secondary" href="/guest/status/receipt.pdf" download>
          Download receipt (PDF)
        </a>
      )}
      <button className="btn secondary" type="button" onClick={onDone}>Done</button>
    </div>
  );
}
