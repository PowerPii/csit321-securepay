import { useEffect, useState } from "react";
import { Row } from "../ui";
import { getPayments, getPayment, type HistoryRow, type PaymentDetail } from "../accountApi";
import { brandName, formatDate, hasReceipt, money } from "../format";
import { asError } from "./forms";

export function History({ onEnded }: { onEnded: () => void }) {
  const [rows, setRows] = useState<HistoryRow[] | null>(null);
  const [error, setError] = useState<string>();
  const [selected, setSelected] = useState<PaymentDetail | null>(null);
  const [selecting, setSelecting] = useState<string>();

  useEffect(() => {
    getPayments()
      .then((r) => setRows(r.data))
      .catch((err) => (asError(err).status === 401 ? onEnded() : setError(asError(err).message)));
  }, [onEnded]);

  async function open(reference: string) {
    setSelecting(reference);
    try {
      setSelected(await getPayment(reference));
    } catch (err) {
      if (asError(err).status === 401) onEnded();
      else setError(asError(err).message);
    } finally {
      setSelecting(undefined);
    }
  }

  return (
    <>
      <h1>Payment history</h1>
      <p className="muted small">Only payments made while signed in to this account are listed.</p>
      {error && <p className="alert" role="alert">{error}</p>}
      {rows === null && !error && <p className="muted">Loading…</p>}
      {rows && rows.length === 0 && (
        <div className="box grey">
          <strong>You have not made any payments</strong>
          <p className="muted small">Payments you make while signed in will appear here. Another person's payment is never shown, even with its reference.</p>
        </div>
      )}
      {rows && rows.length > 0 && (
        <div className="history">
          <div>
            <table className="history-table">
              <thead>
                <tr><th>Date</th><th>Merchant</th><th>Reference</th><th>Amount</th><th>Status</th></tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.reference} className={selected?.reference === r.reference ? "selected" : undefined}>
                    <td>{formatDay(r.created_at)}</td>
                    <td>{r.merchant_name}</td>
                    <td>
                      <button className="link" type="button" onClick={() => open(r.reference)} disabled={selecting === r.reference}
                        aria-label={`Show payment ${r.reference}`}>{r.reference}</button>
                    </td>
                    <td>{money(r.amount, r.currency)}</td>
                    <td><StatusBadge label={r.status_label} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <ul className="history-cards">
              {rows.map((r) => (
                <li key={r.reference}>
                  <button type="button" className="history-card" onClick={() => open(r.reference)}>
                    <span className="row-between"><strong>{money(r.amount, r.currency)}</strong><StatusBadge label={r.status_label} /></span>
                    <span className="muted small">{r.merchant_name} · {r.reference} · {formatDay(r.created_at)}</span>
                  </button>
                </li>
              ))}
            </ul>
            <p className="muted small">Showing {rows.length} of {rows.length}</p>
          </div>
          {selected && <Detail payment={selected} />}
        </div>
      )}
    </>
  );
}

function Detail({ payment }: { payment: PaymentDetail }) {
  return (
    <aside className="box detail" aria-label={`Payment ${payment.reference}`}>
      <div className="view-head">
        <h2 className="small-heading">{money(payment.amount, payment.currency)} to {payment.merchant_name}</h2>
        <StatusBadge label={payment.status_label} />
      </div>
      {payment.status === "unresolved" && (
        <p className="alert" role="status">We are still confirming this payment with the bank. Please don't pay again.</p>
      )}
      <dl className="box">
        <Row label="Payment reference">{payment.reference}</Row>
        <Row label="Order">{payment.order_reference}</Row>
        {payment.card && <Row label="Card">{brandName(payment.card.brand)} •••• {payment.card.last4}</Row>}
        <Row label="Date">{formatDate(payment.created_at)}</Row>
        {payment.refunded_amount ? <Row label="Refunded">{money(payment.refunded_amount, payment.currency)}</Row> : null}
      </dl>
      <h3 className="small-heading">Timeline</h3>
      <ol className="timeline">
        {payment.timeline.map((t, i) => (
          <li key={i}><span>{t.label}</span><span className="muted small">{formatDate(t.at)}</span></li>
        ))}
        {!payment.refunded_amount && <li className="hollow"><span>Refunds: none</span><span className="muted small">—</span></li>}
      </ol>
      {hasReceipt(payment.status) && (
        <a className="btn secondary" href={`/customer/payments/${encodeURIComponent(payment.reference)}/receipt.pdf`} download>
          Download receipt (PDF)
        </a>
      )}
    </aside>
  );
}

// The customer wording from the gateway (apps/payments/labels.py): settled money is dark.
const DARK = new Set(["Paid", "Refunded", "Partly refunded"]);
function StatusBadge({ label }: { label: string }) {
  return <span className={`badge${DARK.has(label) ? " dark" : ""}`}>{label}</span>;
}

function formatDay(iso: string): string {
  return new Date(iso).toLocaleDateString("en-SG", { day: "numeric", month: "short", year: "numeric" });
}
