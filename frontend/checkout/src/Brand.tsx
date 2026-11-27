/** The securepay. wordmark and the test-mode pill shown in the top bar of every customer page
 * (Slate rebrand: a lowercase name with a brass full stop, no separate symbol). */
export function Wordmark() {
  return (
    <span className="wordmark">
      <span>securepay</span>
      <span className="wordmark-dot">.</span>
    </span>
  );
}

/** `livemode` comes from the payment (v2): a payment made with a live key shows "Demo"
 * instead of "Test mode", because even live payments are simulated in this project. Pages
 * without a payment (account, guest status) leave it out and keep "Test mode". */
export function Brand({ livemode = false }: { livemode?: boolean }) {
  return (
    <span className="brand">
      <Wordmark />
      {livemode ? (
        <span className="testmode" title="Live mode of a demo gateway: simulated payments, no real money moves">Demo</span>
      ) : (
        <span className="testmode" title="Simulated payments: no real money moves">Test mode</span>
      )}
    </span>
  );
}
