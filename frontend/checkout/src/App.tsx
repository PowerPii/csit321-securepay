// Hosted checkout orchestration: load, poll and display the current payment step.
import { useEffect, useState, type ReactNode } from "react";
import { getSession, type Session } from "./api";
import { sessionIdFromPath } from "./format";
import { Shell, useFocusHeadingOnChange } from "./ui";
import { CardForm } from "./checkout/CardForm";
import { VerifyCode } from "./checkout/VerifyCode";
import { Checking, Approved, NotCompleted } from "./checkout/Results";
import { OrderSummary } from "./checkout/OrderSummary";

export { sessionIdFromPath };

// While the bank's answer is unknown the gateway checks with it (30 s, then 1, 2, 5 ... min);
// the page asks the gateway again on this interval so it shows the result once it is known.
const PENDING_POLL_MS = 10_000;

type View =
  | { kind: "loading" }
  | { kind: "missing" }
  | { kind: "session"; session: Session; checking: boolean; error?: string };

export default function App({ path = window.location.pathname }: { path?: string }) {
  const sessionId = sessionIdFromPath(path);
  const [view, setView] = useState<View>(sessionId ? { kind: "loading" } : { kind: "missing" });
  const stepKey = view.kind === "session"
    ? `${view.checking || view.session.result === "processing" ? "checking" : view.session.result}`
    : view.kind;
  // Not on the first card form: only when the payment moves to another step.
  const panel = useFocusHeadingOnChange(view.kind === "loading" ? "loading" : stepKey === "open" ? "loading" : stepKey);

  useEffect(() => {
    if (!sessionId) return;
    getSession(sessionId)
      .then((session) => setView({ kind: "session", session, checking: false }))
      .catch(() => setView({ kind: "missing" }));
  }, [sessionId]);

  const pending = view.kind === "session" && view.session.result === "pending";
  useEffect(() => {
    if (!sessionId || !pending) return;
    const t = window.setInterval(() => {
      getSession(sessionId)
        .then((s) => setView((v) => (v.kind === "session" ? { ...v, session: s } : v)))
        .catch(() => undefined);
    }, PENDING_POLL_MS);
    return () => window.clearInterval(t);
  }, [sessionId, pending]);

  if (view.kind === "loading") {
    return <Shell status="Secure checkout"><p className="muted center">Loading…</p></Shell>;
  }
  if (view.kind === "missing" || !sessionId) {
    return (
      <Shell status="Secure checkout">
        <div className="panel-right only">
          <div className="result">
            <h1>This checkout link isn't valid</h1>
            <p className="muted">Go back to the shop and start the payment again. No money was taken.</p>
          </div>
        </div>
      </Shell>
    );
  }

  const { session, checking, error } = view;
  const update = (s: Session, c = false, e?: string) =>
    setView({ kind: "session", session: s, checking: c, error: e });
  const inFlight = checking || session.result === "processing";

  let status = "Secure checkout";
  let body: ReactNode;
  if (inFlight) {
    body = <Checking session={session} />;
  } else if (session.result === "open") {
    body = <CardForm sessionId={sessionId} session={session} onUpdate={update} formError={error} />;
  } else if (session.result === "verification_required") {
    status = "Verify your payment";
    body = <VerifyCode sessionId={sessionId} session={session} onUpdate={update} />;
  } else if (session.result === "approved") {
    status = "Payment complete";
    body = <Approved session={session} />;
  } else {
    status = "Payment not completed";
    body = <NotCompleted session={session} />;
  }

  return (
    <Shell status={status} livemode={session.livemode}>
      <OrderSummary session={session} />
      <section className="panel-right" ref={panel}>{body}</section>
    </Shell>
  );
}
