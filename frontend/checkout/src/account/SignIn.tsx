import { useState, type FormEvent, type KeyboardEvent } from "react";
import { Field } from "../ui";
import { passkeySignInOptions, signIn, signInWithPasskey, register, type Customer } from "../accountApi";
import { PasskeyCancelled, passkeysSupported, signWithPasskey } from "../passkeys";
import { ConsentBox } from "../Privacy";
import { asError, EMAIL_RE, MIN_PASSWORD, newPasswordProblems } from "./forms";

export function SignInPage({ notice, onSignedIn, onForgot }: { notice?: string; onSignedIn: (c: Customer) => void; onForgot: () => void }) {
  const [tab, setTab] = useState<"sign-in" | "register">("sign-in");
  // Arrow keys move between the two tabs (WAI-ARIA tabs pattern).
  function onTabKey(ev: KeyboardEvent<HTMLDivElement>) {
    if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
    const next = tab === "sign-in" ? "register" : "sign-in";
    setTab(next);
    document.getElementById(`tab-${next}`)?.focus();
  }
  return (
    <div className="signin-layout">
      <aside className="signin-intro">
        <h1>Your SecurePay account</h1>
        <p className="muted small">An account is optional. Guests can still pay and check a payment with the one-time code on their receipt.</p>
        <ul className="ticks small">
          <li>See all your payments in one place</li>
          <li>Check the status of a refund</li>
          <li>Sign out on every device at once</li>
          <li>Sign in and approve payments with a passkey</li>
        </ul>
        <p className="muted tiny">Signed-in sessions end after 15 minutes without activity, and you are brought back to this page.</p>
      </aside>
      <div className="signin-cards">
        {notice && <p className="muted small full" role="status">{notice}</p>}
        {/* Phones show one form at a time behind these tabs; wider screens show both forms. */}
        <div className="tabs" role="tablist" aria-label="Sign in or register" onKeyDown={onTabKey}>
          <button role="tab" type="button" id="tab-sign-in" aria-controls="panel-sign-in" aria-selected={tab === "sign-in"}
            tabIndex={tab === "sign-in" ? 0 : -1} onClick={() => setTab("sign-in")}>Sign in</button>
          <button role="tab" type="button" id="tab-register" aria-controls="panel-register" aria-selected={tab === "register"}
            tabIndex={tab === "register" ? 0 : -1} onClick={() => setTab("register")}>Register</button>
        </div>
        <div className={`card-box${tab === "sign-in" ? " shown" : ""}`} id="panel-sign-in" role="tabpanel" aria-labelledby="tab-sign-in">
          <SignInForm onSignedIn={onSignedIn} onForgot={onForgot} />
        </div>
        <div className={`card-box${tab === "register" ? " shown" : ""}`} id="panel-register" role="tabpanel" aria-labelledby="tab-register">
          <RegisterForm onSignedIn={onSignedIn} />
        </div>
      </div>
    </div>
  );
}

function SignInForm({ onSignedIn, onForgot }: { onSignedIn: (c: Customer) => void; onForgot: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [failure, setFailure] = useState<{ title: string; message: string }>();
  const [busy, setBusy] = useState(false);

  async function onSubmit(ev: FormEvent) {
    ev.preventDefault();
    const e: Record<string, string> = {};
    if (!email.trim()) e.email = "Enter your email address.";
    if (!password) e.password = "Enter your password.";
    setErrors(e);
    if (Object.keys(e).length || busy) return;
    setBusy(true);
    setFailure(undefined);
    try {
      onSignedIn(await signIn(email.trim(), password, remember));
    } catch (err) {
      const x = asError(err);
      setPassword("");
      if (x.code === "invalid_credentials") setFailure({ title: "Email or password is incorrect", message: x.message.replace(/^Email or password is incorrect\.\s*/, "") });
      else if (x.code === "account_locked") setFailure({ title: "Sign-in is locked", message: x.message });
      else if (x.param) setErrors({ [x.param]: x.message });
      else setFailure({ title: "Could not sign in", message: x.message });
    } finally {
      setBusy(false);
    }
  }

  // v2: no email or password; the browser offers the passkeys it has for SecurePay.
  async function onPasskey() {
    if (busy) return;
    setBusy(true);
    setFailure(undefined);
    try {
      const { options } = await passkeySignInOptions();
      onSignedIn(await signInWithPasskey(await signWithPasskey(options), remember));
    } catch (err) {
      if (err instanceof PasskeyCancelled) setFailure({ title: "No passkey used", message: "Try again, or sign in with your email and password." });
      else setFailure({ title: "Could not sign in with a passkey", message: asError(err).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="form" onSubmit={onSubmit} noValidate>
      <h2 className="card-title">Sign in</h2>
      <p className="muted small">Use the email you registered with.</p>
      <Field label="Email" error={errors.email} id="signin-email">
        <input id="signin-email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
      </Field>
      <Field label="Password" error={errors.password} id="signin-password">
        <input id="signin-password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
      </Field>
      <div className="row-between small">
        <label className="check">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> Keep me signed in on this device
        </label>
        <button className="link" type="button" onClick={onForgot}>Forgot password?</button>
      </div>
      <button className="btn primary" type="submit" disabled={busy}>Sign in</button>
      {passkeysSupported() && (
        <>
          <p className="muted tiny or-divider">or</p>
          <button className="btn secondary" type="button" disabled={busy} onClick={onPasskey}>
            Sign in with a passkey
          </button>
        </>
      )}
      {failure && (
        <div className="box grey" role="alert">
          <strong>{failure.title}</strong>
          <p className="muted small">{failure.message}</p>
        </div>
      )}
    </form>
  );
}

function RegisterForm({ onSignedIn }: { onSignedIn: (c: Customer) => void }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [consent, setConsent] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  async function onSubmit(ev: FormEvent) {
    ev.preventDefault();
    const e: Record<string, string> = {};
    if (!consent) e.privacy_consent = "Agree to the privacy notice to create an account.";
    if (!name.trim()) e.full_name = "Enter your full name.";
    if (!EMAIL_RE.test(email.trim())) e.email = "Enter a valid email address.";
    Object.assign(e, newPasswordProblems(password, confirm));
    setErrors(e);
    if (Object.keys(e).length || busy) return;
    setBusy(true);
    try {
      onSignedIn(await register(name.trim(), email.trim(), password));
    } catch (err) {
      const x = asError(err);
      setErrors({ [x.param ?? "form"]: x.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="form" onSubmit={onSubmit} noValidate>
      <h2 className="card-title">Create an account</h2>
      <p className="muted small">Keep your payments with this gateway in one history.</p>
      {errors.form && <p className="alert" role="alert">{errors.form}</p>}
      <Field label="Full name" error={errors.full_name} id="reg-name">
        <input id="reg-name" autoComplete="name" maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="Email" error={errors.email} id="reg-email">
        <input id="reg-email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
      </Field>
      <Field label="Password" error={errors.password} id="reg-password">
        <input id="reg-password" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
      </Field>
      {!errors.password && <p className="muted tiny hint">Use at least {MIN_PASSWORD} characters.</p>}
      <Field label="Confirm password" error={errors.confirm} id="reg-confirm">
        <input id="reg-confirm" type="password" autoComplete="new-password" placeholder="Repeat password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
      </Field>
      <ConsentBox id="register-consent" checked={consent} onChange={setConsent} error={errors.privacy_consent}>
        I agree that SecurePay keeps my name, email and payment history for my account, as the privacy notice says.
      </ConsentBox>
      <button className="btn primary" type="submit" disabled={busy}>Create account</button>
    </form>
  );
}
