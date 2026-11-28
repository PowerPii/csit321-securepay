// v2 3-D Secure (docs/v2/THREE-DS.md): the card's bank asks the shopper for the code it sent
// by text message. In a real checkout this screen comes from the bank's own server in a
// frame; the simulator is private, so the checkout draws it and relays the code.
import type { FormEvent } from "react";
import type { IssuerChallenge, Session } from "../api";
import { money } from "../format";
import { Field } from "../ui";

interface Props {
  session: Session;
  challenge: IssuerChallenge;
  code: string;
  setCode: (code: string) => void;
  error?: string;
  busy: boolean;
  attemptsRemaining: number;
  onSubmit: (ev: FormEvent) => void;
}

export function BankChallenge({ session, challenge, code, setCode, error, busy, attemptsRemaining, onSubmit }: Props) {
  return (
    <form className="form bank-challenge" onSubmit={onSubmit} noValidate aria-labelledby="bank-title">
      <div className="bank-head">
        <span className="bank-name">{challenge.bank}</span>
        <span className="bank-badge">3-D Secure</span>
      </div>
      <h1 id="bank-title">Confirm this payment</h1>
      <p className="muted">
        Your bank wants to check it's you. We sent a 6-digit code by text message to your phone ending {challenge.phone_hint.replace(/[^0-9]/g, "")}.
      </p>
      <dl className="bank-facts">
        <div><dt>Merchant</dt><dd>{session.merchant.name}</dd></div>
        <div><dt>Amount</dt><dd>{money(session.amount, session.currency)}</dd></div>
        <div><dt>Card</dt><dd>Visa ending {challenge.card_last4}</dd></div>
      </dl>
      {challenge.simulated_text_code && (
        <div className="sim-text" role="note" aria-label="Simulated text message">
          <p className="tiny muted">Simulated text message (there is no real phone in the sandbox)</p>
          <p>{challenge.bank}: your code to approve {money(session.amount, session.currency)} at {session.merchant.name} is <strong>{challenge.simulated_text_code}</strong>. Never share it.</p>
        </div>
      )}
      <Field label="Code from your bank" error={error} id="code">
        <input id="code" inputMode="numeric" autoComplete="one-time-code" placeholder="123456"
          maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} />
      </Field>
      <p className="muted tiny">{attemptsRemaining} attempt{attemptsRemaining === 1 ? "" : "s"} left</p>
      <button className="btn primary" type="submit" disabled={busy}>Confirm with my bank</button>
      <a className="cancel-link" href={session.return_url}>
        Cancel and return to {session.merchant.name}
      </a>
    </form>
  );
}
