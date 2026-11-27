import { useState } from "react";
import type { Session } from "../api";
import { initials, money } from "../format";
import { LockIcon } from "./icons";

export function OrderSummary({ session }: { session: Session }) {
  // Phones show the total and fold the item list away, so the card form starts on the first
  // screen; wider screens always show the list (styles.css).
  const [open, setOpen] = useState(false);
  const items = session.line_items ?? [];
  const count = items.reduce((n, item) => n + item.quantity, 0);
  return (
    <aside className="panel-left" aria-label="Order summary">
      <div>
        <p className="store">
          <span className="store-mark" aria-hidden="true">{initials(session.merchant.name)}</span>
          <span className="store-name">{session.merchant.name}</span>
        </p>
        <p className="muted">{session.result === "approved" ? "Total paid" : "Total to pay"}</p>
        <p className="amount">{money(session.amount, session.currency)}</p>
        <p className="muted small">
          Order {session.order_reference} · {session.currency}
        </p>
        {(items.length > 0 || session.description) && (
          <button type="button" className="summary-toggle" aria-expanded={open} aria-controls="order-lines"
            onClick={() => setOpen((o) => !o)}>
            {open ? "Hide" : "Show"} order details
            {count > 0 && ` (${count} item${count === 1 ? "" : "s"})`}
          </button>
        )}
        <dl id="order-lines" className={`lines${open ? " open" : ""}`}>
          {items.length > 0
            ? items.map((item, i) => (
                <div className="row" key={i}>
                  <dt>{item.name} × {item.quantity}</dt>
                  <dd>{money(item.unit_amount * item.quantity, session.currency)}</dd>
                </div>
              ))
            : session.description && (
                <div className="row">
                  <dt>{session.description}</dt>
                </div>
              )}
          <div className="row total">
            <dt>Total</dt>
            <dd>{money(session.amount, session.currency)}</dd>
          </div>
        </dl>
      </div>
      <p className="muted small lock">
        <LockIcon /> Secured by SecurePay. Card details are encrypted and never shared with the shop.
      </p>
    </aside>
  );
}
