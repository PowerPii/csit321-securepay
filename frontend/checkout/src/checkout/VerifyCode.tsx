import { useState, type FormEvent } from "react";
import { ApiError, getSession, resendCode, verify } from "../api";
import { countdown, money } from "../format";
import { Field } from "../ui";
import { LockIcon } from "./icons";
import { Checking } from "./Results";
import { useSessionExpiry, type SessionFormProps } from "./session";

export function VerifyCode({
  sessionId,
  session,
  onUpdate,
}: SessionFormProps) {
  const v = session.verification;
  const [code, setCode] = useState("");
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const now = useSessionExpiry({ sessionId, session, onUpdate });

  const fail = (err: unknown) => {
    const e = err instanceof ApiError ? err : new ApiError(0, "unknown_error", "Something went wrong.");
    setError(e.message);
    setNotice(undefined);
    // Refresh attempts left and the resend timer; the attempt may have ended too.
    getSession(sessionId).then((s) => onUpdate(s)).catch(() => undefined);
  };

  async function onSubmit(ev: FormEvent) {
    ev.preventDefault();
    if (!/^\d{6}$/.test(code)) {
      setError("Enter the 6-digit code from the email.");
      return;
    }
    if (busy) return;
    setBusy(true);
    setError(undefined);
    setVerifying(true); // "Checking your payment" while the bank answers
    try {
      onUpdate(await verify(sessionId, code));
    } catch (err) {
      setVerifying(false);
      setCode("");
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  async function onResend() {
    setBusy(true);
    try {
      const s = await resendCode(sessionId);
      setError(undefined);
      setCode("");
      setNotice(`We sent a new code to ${s.verification?.sent_to ?? "your email"}. Earlier codes no longer work.`);
      onUpdate(s);
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  if (!v || verifying) return <Checking session={session} />;
  const codeLeft = Date.parse(v.code_expires_at) - now;
  const resendIn = v.resend_available_at ? Date.parse(v.resend_available_at) - now : null;

  return (
    <form className="form" onSubmit={onSubmit} noValidate>
      <p className="step-pill">One more step</p>
      <h1>Check your email</h1>
      <p className="muted">
        To protect your card, enter the 6-digit code we sent to {v.sent_to ?? "your email"}.
      </p>
      {notice && <p className="muted small" role="status">{notice}</p>}
      <Field label="Verification code" error={error} id="code">
        <input id="code" inputMode="numeric" autoComplete="one-time-code" placeholder="123456"
          maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} />
      </Field>
      <p className="muted tiny">
        {codeLeft > 0 ? `Code expires in ${countdown(v.code_expires_at, now)}` : "This code has expired. Ask for a new one."}
        {" · "}
        {v.attempts_remaining} attempt{v.attempts_remaining === 1 ? "" : "s"} left
      </p>
      <button className="btn primary with-lock" type="submit" disabled={busy}>
        <LockIcon /> Verify and pay {money(session.amount, session.currency)}
      </button>
      {resendIn === null ? (
        <p className="muted tiny">No more codes can be sent for this payment.</p>
      ) : (
        <button className="btn secondary" type="button" onClick={onResend} disabled={busy || resendIn > 0}>
          {resendIn > 0 ? `Send a new code in ${Math.ceil(resendIn / 1000)} s` : "Send a new code"}
        </button>
      )}
      <a className="cancel-link" href={session.return_url}>
        Cancel and return to {session.merchant.name}
      </a>
    </form>
  );
}
