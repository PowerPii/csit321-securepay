import { useEffect, useRef, useState } from "react";
import type { Session } from "../api";
import { brandName, formatDate, money } from "../format";
import { Row } from "../ui";
import { CheckIcon, CrossIcon, WarnIcon, ClockIcon } from "./icons";

const AUTO_RETURN_SECONDS = 5;

export function Checking({ session }: { session: Session }) {
  return (
    <div className="result">
      <span className="spinner" aria-hidden="true" />
      <h1>Checking your payment…</h1>
      <p className="muted">This usually takes a second. Please don't close this page.</p>
      <dl className="box grey">
        <Row label="Merchant">{session.merchant.name}</Row>
        <Row label="Amount">{money(session.amount, session.currency)}</Row>
        {session.card && <Row label="Card">{brandName(session.card.brand)} •••• {session.card.last4}</Row>}
      </dl>
    </div>
  );
}

export function Approved({ session }: { session: Session }) {
  const [left, setLeft] = useState(AUTO_RETURN_SECONDS);
  const [stay, setStay] = useState(false);
  const returned = useRef(false);
  useEffect(() => {
    // WCAG 2.2.1: a time limit the customer can turn off ("Stay on this page").
    if (stay) return;
    const t = window.setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000);
    return () => window.clearInterval(t);
  }, [stay]);
  useEffect(() => {
    if (left === 0 && !returned.current) {
      returned.current = true;
      window.location.assign(session.return_url);
    }
  }, [left, session.return_url]);

  return (
    <div className="result">
      <CheckIcon />
      <h1>Payment successful</h1>
      <p className="muted">{money(session.amount, session.currency)} paid to {session.merchant.name}</p>
      <dl className="box">
        <Row label="Payment reference">{session.reference}</Row>
        {session.authorised_at && <Row label="Date">{formatDate(session.authorised_at)}</Row>}
        {session.card && <Row label="Card">{brandName(session.card.brand)} •••• {session.card.last4}</Row>}
        <Row label="Status"><span className="badge dark">Paid</span></Row>
      </dl>
      {session.customer_email_masked && (
        <p className="muted small">A receipt is on its way to {session.customer_email_masked}.</p>
      )}
      <a className="btn primary" href={session.return_url}>Return to {session.merchant.name}</a>
      {stay ? (
        <p className="muted tiny">You can stay here as long as you like.</p>
      ) : (
        <p className="muted tiny">
          Returning you automatically in {left} s ·{" "}
          <button className="link" type="button" onClick={() => setStay(true)}>Stay on this page</button>
        </p>
      )}
    </div>
  );
}

const NOT_COMPLETED: Record<string, { icon: "cross" | "warn" | "clock"; title: string; text: (m: string) => string; button: (m: string) => string; badge: string }> = {
  declined: {
    icon: "cross",
    title: "Payment declined",
    text: () => "Your bank declined this payment. No money was taken. You can try a different card.",
    button: () => "Try another card",
    badge: "Declined by simulated bank",
  },
  not_completed: {
    icon: "warn",
    title: "We couldn't complete this payment",
    text: (m) => `No money was taken. If you need help, please contact ${m}.`,
    button: (m) => `Return to ${m}`,
    badge: "Not completed",
  },
  verification_failed: {
    icon: "warn",
    title: "We couldn't confirm it was you",
    text: (m) => `The verification code wasn't entered in time or didn't match, so we stopped the payment to protect your card. No money was taken. You can return to ${m} and try again.`,
    button: (m) => `Return to ${m}`,
    badge: "Not completed · not verified",
  },
  pending: {
    icon: "clock",
    // The bank's reply was lost. The payment is not failed: never invite a second one.
    title: "We're checking your payment",
    text: (m) => `We didn't hear back from the bank in time, so we're checking with it now. Please don't pay again: this payment may still go through. This page updates as soon as we know, and ${m} will confirm it too.`,
    button: (m) => `Return to ${m}`,
    badge: "Awaiting bank confirmation",
  },
  unavailable: {
    icon: "warn",
    title: "We couldn't check this payment right now",
    text: (m) => `No money was taken. Please try again in a few minutes, or return to ${m}.`,
    button: (m) => `Return to ${m}`,
    badge: "Not completed · try again later",
  },
  expired: {
    icon: "clock",
    title: "This checkout has ended",
    text: (m) => `No money was taken. Return to ${m} to start the payment again.`,
    button: (m) => `Return to ${m}`,
    badge: "Checkout expired",
  },
  cancelled: {
    icon: "warn",
    title: "This payment was cancelled",
    text: () => "No money was taken.",
    button: (m) => `Return to ${m}`,
    badge: "Cancelled",
  },
};

export function NotCompleted({ session }: { session: Session }) {
  const v = NOT_COMPLETED[session.result] ?? NOT_COMPLETED.not_completed;
  const m = session.merchant.name;
  return (
    <div className="result card-result">
      <h1 className="with-icon">
        {v.icon === "cross" ? <CrossIcon /> : v.icon === "warn" ? <WarnIcon /> : <ClockIcon />}
        {v.title}
      </h1>
      <p className="muted">{v.text(m)}</p>
      {session.reference && <p className="muted small">Payment reference {session.reference}</p>}
      {session.result === "pending" && session.reference && (
        // The same reference and status page as the receipt email (apps/guest/receipts.py).
        <p className="small">
          <a href={`/guest/status?ref=${encodeURIComponent(session.reference)}`}>Check this payment's status later</a>
        </p>
      )}
      <a className={`btn ${session.result === "declined" ? "primary" : "secondary"}`} href={session.return_url}>
        {v.button(m)}
      </a>
      <span className="badge">{v.badge}</span>
    </div>
  );
}
