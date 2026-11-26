// Customer account pages, wireframes C10 to C12 (FR-ACC-01 to FR-ACC-04, FR-ACC-07, FR-ACC-08):
//   /account/sign-in   C10 sign in or create an account
//   /account/payments  C11 payment history (only payments made while signed in)
//   /account/spending  spending insights: last 12 months by month and by shop, after refunds
//   /account/profile   name, email, sign out on every device
//   /account/password  change password (signs out every other device)
//   /account/reset     C12 forgot password: request a link, then set a new password
// Served from the checkout build (Caddy), same CSP. An account is optional: guests still pay
// and check a payment with the emailed code (/guest/status).
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { getMe, signOut, type Customer } from "./accountApi";
import { ApiError } from "./http";
import { minSec, useIdleSignOut } from "./idle";
import { Brand } from "./Brand";
import { SignInPage } from "./account/SignIn";
import { History } from "./account/History";
import { Spending } from "./account/Spending";
import { Profile } from "./account/Profile";
import { PasswordPage } from "./account/Password";
import { ResetPage } from "./account/Reset";
export { newPasswordProblems } from "./account/forms";

export type Page = "sign-in" | "payments" | "spending" | "profile" | "password" | "reset";
const PAGES: Page[] = ["sign-in", "payments", "spending", "profile", "password", "reset"];
export function pageFromPath(path: string): Page {
  const name = path.replace(/^\/account\/?/, "").replace(/\/$/, "");
  return (PAGES as string[]).includes(name) ? (name as Page) : "sign-in";
}

/** The reset token from `#token=...`; the fragment never reaches a server. */
export function tokenFromHash(hash: string): string | null {
  const token = new URLSearchParams(hash.replace(/^#/, "")).get("token");
  return token && /^[\w-]{20,100}$/.test(token) ? token : null;
}
const ENDED_NOTICE = "Your session has ended. Sign in again to see your account.";

export default function Account({ path = window.location.pathname, hash = window.location.hash }: { path?: string; hash?: string }) {
  const [page, setPage] = useState<Page>(pageFromPath(path));
  const [me, setMe] = useState<Customer | null | undefined>(undefined); // undefined: loading
  const [notice, setNotice] = useState<string>();
  // Demo core-only mode on the gateway: accounts are paused, so every page shows one notice.
  const [unavailable, setUnavailable] = useState(false);
  const [resetToken] = useState(() => tokenFromHash(hash));

  useEffect(() => {
    // Keep the token out of the address bar and the browser history.
    if (resetToken && typeof window !== "undefined" && window.history?.replaceState) {
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, [resetToken]);

  const go = useCallback((next: Page, message?: string) => {
    setNotice(message);
    setPage(next);
    if (typeof window !== "undefined" && window.history?.pushState) window.history.pushState(null, "", `/account/${next}`);
  }, []);

  useEffect(() => {
    const onPop = () => setPage(pageFromPath(window.location.pathname));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  useEffect(() => {
    getMe().then(setMe).catch((e) => {
      if (e instanceof ApiError && e.code === "accounts_unavailable") setUnavailable(true);
      setMe(null);
    });
  }, []);

  const ended = useCallback(() => {
    setMe(null);
    go("sign-in", ENDED_NOTICE);
  }, [go]);

  const signedInPage = page === "payments" || page === "spending" || page === "profile" || page === "password";
  useEffect(() => {
    if (me === undefined) return;
    if (signedInPage && me === null) go("sign-in", notice);
    if (page === "sign-in" && me) go("payments");
  }, [me, page, signedInPage, go, notice]);

  async function onSignOut() {
    await signOut().catch(() => undefined);
    setMe(null);
    go("sign-in", "You have signed out.");
  }

  // FR-ACC-10: warn at 13 idle minutes, sign out at 15 (the server's limit too).
  const keepAlive = useCallback(() => { getMe().catch(() => undefined); }, []);
  const onIdleEnd = useCallback(() => {
    signOut().catch(() => undefined);
    setMe(null);
    go("sign-in", "You were signed out after 15 minutes without activity.");
  }, [go]);
  const idle = useIdleSignOut({
    enabled: !!me && page !== "reset",
    warnMinutes: me?.idle_warning_minutes ?? 13,
    endMinutes: me?.idle_minutes ?? 15,
    keepAlive,
    onEnd: onIdleEnd,
  });

  let right: ReactNode = "My account";
  if (page === "reset") right = "Account recovery";
  else if (me) right = (
    <>
      {me.full_name} · <button className="link inverse" type="button" onClick={onSignOut}>Sign out</button>
    </>
  );

  let body: ReactNode;
  if (unavailable) body = (
    <div className="result">
      <h1>Accounts aren't available right now</h1>
      <p className="muted">You can still pay at any shop as a guest. Your receipt email has a link to check a payment.</p>
    </div>
  );
  else if (me === undefined && page !== "reset") body = <p className="muted center">Loading…</p>;
  else if (page === "reset") body = <ResetPage token={resetToken} onSignIn={(m) => go("sign-in", m)} />;
  else if (!me) body = <SignInPage notice={notice} onSignedIn={(c) => { setMe(c); go("payments"); }} onForgot={() => go("reset")} />;
  else
    body = (
      <div className="account-grid">
        <nav className="side-nav" aria-label="Account">
          {(["payments", "spending", "profile", "password"] as Page[]).map((p) => (
            <button key={p} type="button" className={`nav-item${page === p ? " active" : ""}`}
              aria-current={page === p ? "page" : undefined} onClick={() => go(p)}>
              {{ payments: "Payment history", spending: "Spending", profile: "Profile", password: "Password" }[p as "payments"]}
            </button>
          ))}
          <button type="button" className="nav-item" onClick={onSignOut}>Sign out</button>
        </nav>
        <section className="account-main">
          {notice && <p className="muted small" role="status">{notice}</p>}
          {page === "payments" && <History onEnded={ended} />}
          {page === "spending" && <Spending onEnded={ended} />}
          {page === "profile" && (
            <Profile onChanged={(d) => setMe((m) => (m ? { ...m, full_name: d.full_name, email: d.email } : m))}
              onSignedOut={() => { setMe(null); go("sign-in", "You have signed out on every device."); }} onEnded={ended} />
          )}
          {page === "password" && <PasswordPage onEnded={ended} onDone={(m) => setNotice(m)} />}
        </section>
      </div>
    );

  return (
    <>
      <header className="topbar">
        <Brand />
        <span className="topbar-status">{right}</span>
      </header>
      <main className="account-page">{body}</main>
      {idle.warning && (
        <div className="idle-backdrop">
          <div className="idle-dialog" role="alertdialog" aria-modal="true" aria-labelledby="idle-title" aria-describedby="idle-text">
            <h2 id="idle-title">Are you still there?</h2>
            <p id="idle-text" className="muted">You will be signed out in {minSec(idle.secondsLeft)} because of inactivity.</p>
            <div className="btn-row">
              <button className="btn secondary" type="button" onClick={onSignOut}>Sign out</button>
              <button className="btn primary" type="button" onClick={idle.stay} autoFocus>Stay signed in</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
