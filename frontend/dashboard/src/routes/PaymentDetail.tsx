// D4 Payment detail (summary, status history, refunds, webhook deliveries, processor
// operations, fraud decision) with capture / cancel, and D5 Refund as a dialog.
import {
  Alert, Box, Breadcrumbs, Button, Dialog, DialogActions, DialogContent, DialogTitle, Link as MuiLink, Paper,
  Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, ToggleButton, ToggleButtonGroup, Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, post, type NetworkHop, type Operation, type PaymentDetail as Detail, type Refund, type TrailEntry } from "../api/client";
import { Badge, ErrorAlert, Row, ScrollBox, Section, SrOnly } from "../components/ui";
import {
  actionLabel, actorLabel, cardLabel, longDate, money, parseMoney, riskLabel, statusLabel, statusTone, timeOnly, titleCase,
} from "../format";
import { FraudDecision } from "./FraudDecision";

function usePayment(id: string) {
  return useQuery({
    queryKey: ["payment", id],
    queryFn: () => api<Detail>(`/payments/${id}`),
    // Pending work (unresolved operation, webhook retries) updates by itself.
    refetchInterval: (q) => (q.state.data?.pending_operation || q.state.data?.status === "unresolved" ? 5_000 : 30_000),
  });
}

function subtitle(p: Detail): string {
  if (p.status === "captured") return `Captured ${longDate(p.captured_at)}`;
  const parts = [`Created ${longDate(p.created_at)}`];
  if (p.authorisation_expires_at) parts.push(`Auto-cancels if not captured by ${longDate(p.authorisation_expires_at)}`);
  return parts.join(" · ");
}

function Timeline({ p }: { p: Detail }) {
  const pending = p.status === "authorised" && !p.pending_operation;
  return (
    <Section title="Status history">
      <Box component="ul" sx={{ listStyle: "none", m: 0, p: 0 }}>
        {p.timeline.map((e, i) => (
          <Stack component="li" key={i} direction="row" gap={1.5} py={0.6} alignItems="baseline">
            <Box sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: "#13243F", flexShrink: 0, mt: 0.6 }} />
            <Box flexGrow={1}>
              <Typography variant="body2">
                {actionLabel(e.action)}
                {typeof e.details.amount === "number" && e.action.startsWith("refund") ? ` · ${money(e.details.amount)}` : ""}
                {e.action === "review.finding_recorded" && typeof e.details.finding === "string"
                  ? ` · ${titleCase(e.details.finding)}` : ""}
              </Typography>
              <Typography variant="caption" color="text.secondary">{actorLabel(e.actor_type, e.actor)}</Typography>
            </Box>
            <Typography variant="caption" color="text.secondary" title={longDate(e.at)}>{timeOnly(e.at)}</Typography>
          </Stack>
        ))}
        {pending && (
          <Stack component="li" direction="row" gap={1.5} py={0.6} alignItems="baseline">
            <Box sx={{ width: 8, height: 8, borderRadius: "50%", border: "1.5px solid #13243F", flexShrink: 0 }} />
            <Typography variant="body2" flexGrow={1}>Capture or cancel (merchant action)</Typography>
            <Typography variant="caption" color="text.secondary">pending</Typography>
          </Stack>
        )}
      </Box>
    </Section>
  );
}

const TRAIL_KIND: Record<TrailEntry["kind"], string> = {
  payment: "Payment", screening: "Screening", operation: "Processor", refund: "Refund", webhook: "Webhook",
  email: "Email", audit: "Audit",
};

/** FR-TXN-16: everything about the payment under one correlation ID, oldest first. */
function FullTimeline({ p }: { p: Detail }) {
  const [open, setOpen] = useState(false);
  const rows = open ? p.trail : p.trail.slice(-8);
  return (
    <Section title="Full timeline" action={
      p.trail.length > 8 ? <Button size="small" onClick={() => setOpen(!open)}>{open ? "Show latest" : `Show all ${p.trail.length}`}</Button> : undefined
    }>
      <Typography variant="caption" color="text.secondary" display="block" mb={1}>
        Correlation ID <Box component="span" sx={{ fontFamily: "monospace" }}>{p.correlation_id}</Box>: screening, processor
        operations, refunds, notifications and audit entries for this payment.
      </Typography>
      <ScrollBox label="Full timeline table">
      <Table size="small" aria-label="Full timeline">
        <TableBody>
          {rows.map((r) => (
            <TableRow key={`${r.kind}-${r.id}`}>
              <TableCell sx={{ whiteSpace: "nowrap", width: 1 }} title={longDate(r.at)}>{timeOnly(r.at)}</TableCell>
              <TableCell sx={{ width: 1 }}><Badge label={TRAIL_KIND[r.kind]} /></TableCell>
              <TableCell sx={{ wordBreak: "break-word", minWidth: 120 }}>
                <Typography variant="body2">{r.kind === "audit" ? actionLabel(r.title) : r.title}</Typography>
                {(r.detail || r.request_id) && (
                  <Typography variant="caption" color="text.secondary">
                    {[r.detail, r.request_id && `request ${r.request_id}`].filter(Boolean).join(" · ")}
                  </Typography>
                )}
              </TableCell>
              <TableCell align="right">{r.status ? titleCase(r.status) : ""}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      </ScrollBox>
    </Section>
  );
}

function Webhooks({ p }: { p: Detail }) {
  if (!p.webhooks.length) return null;
  return (
    <Section title="Webhook deliveries">
      <ScrollBox label="Webhook deliveries table">
        <Table size="small">
          <TableHead>
            <TableRow><TableCell>Event</TableCell><TableCell>Seq</TableCell><TableCell>Result</TableCell><TableCell>Attempts</TableCell></TableRow>
          </TableHead>
          <TableBody>
            {p.webhooks.map((w) => (
              <TableRow key={w.id}>
                <TableCell>{w.type}</TableCell>
                <TableCell>{w.sequence}</TableCell>
                <TableCell>
                  <Badge label={w.status === "delivered" ? `Delivered · ${w.last_http_status ?? 200}`
                    : w.status === "undelivered" ? "Undelivered"
                    : w.attempts ? `Retrying · ${w.last_http_status ?? w.last_error ?? "error"}` : "Queued"} />
                </TableCell>
                <TableCell>{w.attempts}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </ScrollBox>
    </Section>
  );
}

function Refunds({ refunds, refundable }: { refunds: Refund[]; refundable: number }) {
  return (
    <Section title="Refunds">
      {refunds.length ? (
        <ScrollBox label="Refunds table">
          <Table size="small">
            <TableHead>
              <TableRow><TableCell>Refund</TableCell><TableCell>Created</TableCell><TableCell align="right">Amount</TableCell><TableCell>Status</TableCell><TableCell>Reason</TableCell></TableRow>
            </TableHead>
            <TableBody>
              {refunds.map((r) => (
                <TableRow key={r.id}>
                  <TableCell>{r.reference}</TableCell>
                  <TableCell>{longDate(r.created_at)}</TableCell>
                  <TableCell align="right">{money(r.amount)}</TableCell>
                  <TableCell><Badge label={titleCase(r.status)} tone={r.status === "completed" ? "dark" : "neutral"} /></TableCell>
                  <TableCell>{r.reason ?? r.failure_reason ?? ""}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </ScrollBox>
      ) : <Typography variant="body2" color="text.secondary">No refunds yet.</Typography>}
      <Stack direction="row" justifyContent="space-between" mt={1.5}>
        <Typography variant="body2" color="text.secondary">Refundable balance</Typography>
        <Typography variant="body2" fontWeight={700}>{money(refundable)}</Typography>
      </Stack>
    </Section>
  );
}

// v2 network split: each operation's path acquirer -> switch -> issuer, with the ISO 8583
// message types and how long each node took (its time includes the nodes after it).
const NODE_LABEL: Record<NetworkHop["node"], string> = {
  acquirer: "Acquirer", switch: "SecurePay Switch", issuer: "SecurePay Bank",
};

function NetworkPath({ o }: { o: Operation }) {
  const net = o.network!;
  const approved = net.response_code === "00";
  return (
    <Box component="li" sx={{ py: 1.25, borderTop: "1px solid #E8ECF2", "&:first-of-type": { borderTop: "none" } }}>
      <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap" mb={0.75}>
        <Typography variant="body2" fontWeight={700}>{titleCase(o.type)} · {money(o.amount)}</Typography>
        <Badge label={`${net.response_code} ${net.response_text}`} tone={approved ? "dark" : "neutral"} />
        {net.auth_code && <Typography variant="caption" color="text.secondary">auth code {net.auth_code}</Typography>}
        <Typography variant="caption" color="text.secondary">STAN {net.stan} · RRN {net.rrn}</Typography>
      </Stack>
      <Box component="ol" aria-label={`${titleCase(o.type)} network path`}
        sx={{ listStyle: "none", m: 0, p: 0, display: "flex", flexWrap: "wrap", gap: 0.75, alignItems: "center" }}>
        {net.trace.map((h, i) => (
          <Box component="li" key={h.node} sx={{ display: "flex", alignItems: "center", gap: 0.75 }}>
            {i > 0 && <Box component="span" aria-hidden sx={{ color: "text.secondary" }}>→</Box>}
            <Box sx={{ border: "1px solid #C9D1DC", borderRadius: 1, px: 1, py: 0.5, minWidth: 0 }}>
              <Typography variant="body2" fontWeight={600}>{NODE_LABEL[h.node]}</Typography>
              <Typography variant="caption" color="text.secondary" display="block">
                {h.mti_in} → {h.mti_out} · {h.response_code} · {h.elapsed_ms} ms
              </Typography>
            </Box>
          </Box>
        ))}
      </Box>
    </Box>
  );
}

function Network({ p }: { p: Detail }) {
  const traced = p.operations.filter((o) => o.network);
  if (!traced.length) return null;
  return (
    <Section title="Card network">
      <Typography variant="body2" color="text.secondary" mb={0.5}>
        Each call crossed the SecurePay Network: the acquirer sends an ISO 8583 message, the switch routes it, and the
        bank answers with a response code.
      </Typography>
      <Box component="ul" sx={{ listStyle: "none", m: 0, p: 0 }}>
        {traced.map((o) => <NetworkPath key={o.id} o={o} />)}
      </Box>
    </Section>
  );
}

function Operations({ p }: { p: Detail }) {
  const client = useQueryClient();
  const recheck = useMutation({
    mutationFn: (id: string) => post<{ settled: boolean }>(`/operations/${id}/recheck`),
    onSuccess: () => client.invalidateQueries({ queryKey: ["payment", p.id] }),
  });
  // Shown once anything beyond a plain answered authorisation happened (S4 evidence stays).
  const interesting = p.operations.filter((o) => o.type !== "authorise" || o.status !== "succeeded"
    || o.reconciliation_checks > 0 || o.escalated_at);
  if (!interesting.length && !p.actions.recheck) return null;
  return (
    <Section title="Processor operations">
      <Typography variant="body2" color="text.secondary" mb={1}>
        Every call to the simulator has its own operation ID. An unanswered call is checked again with the same ID;
        nobody can set its outcome by hand.
      </Typography>
      <ScrollBox label="Processor operations table">
        <Table size="small">
          <TableHead>
            <TableRow><TableCell>Operation</TableCell><TableCell>Type</TableCell><TableCell>Status</TableCell><TableCell>Checks</TableCell><TableCell><SrOnly>Actions</SrOnly></TableCell></TableRow>
          </TableHead>
          <TableBody>
            {p.operations.map((o) => (
              <TableRow key={o.id}>
                <TableCell sx={{ fontFamily: "monospace", fontSize: 12 }}>{o.id}</TableCell>
                <TableCell>{titleCase(o.type)} · {money(o.amount)}</TableCell>
                <TableCell>
                  <Badge label={o.escalated_at && o.status === "unresolved" ? "Escalated" : titleCase(o.status)} />
                </TableCell>
                <TableCell>
                  {o.reconciliation_checks}
                  {o.last_checked_at && <Typography variant="caption" color="text.secondary" display="block">last {timeOnly(o.last_checked_at)}</Typography>}
                  {o.next_check_at && <Typography variant="caption" color="text.secondary" display="block">next {timeOnly(o.next_check_at)}</Typography>}
                </TableCell>
                <TableCell align="right">
                  {p.actions.recheck && o.status === "unresolved" && (
                    <Button size="small" variant="outlined" disabled={recheck.isPending} onClick={() => recheck.mutate(o.id)}>
                      Re-check with the simulator
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </ScrollBox>
      {recheck.data && (
        <Alert severity={recheck.data.settled ? "success" : "warning"} sx={{ mt: 1 }}>
          {recheck.data.settled ? "The simulator answered; the operation is settled." : "Still no answer from the simulator."}
        </Alert>
      )}
      <ErrorAlert error={recheck.error} />
    </Section>
  );
}

function RefundDialog({ p, open, onClose }: { p: Detail; open: boolean; onClose: () => void }) {
  const client = useQueryClient();
  const [mode, setMode] = useState<"full" | "partial">("partial");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const cents = mode === "full" ? p.refundable_amount : parseMoney(amount) ?? 0;
  const valid = cents > 0 && cents <= p.refundable_amount;
  const refund = useMutation({
    mutationFn: () => post<Refund>(`/payments/${p.id}/refunds`, { amount: cents, ...(reason.trim() ? { reason: reason.trim() } : {}) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["payment", p.id] }),
  });
  const done = refund.data;
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="xs" aria-labelledby="refund-title">
      <DialogTitle id="refund-title" fontWeight={700}>Refund payment</DialogTitle>
      <DialogContent>
        {done ? (
          <Alert severity={done.status === "completed" ? "success" : done.status === "failed" ? "error" : "warning"}>
            {done.status === "completed" && `Refunded ${money(done.amount)} (${done.reference}).`}
            {done.status === "failed" && `The refund failed (${done.failure_reason}). Nothing was refunded.`}
            {done.status === "unresolved" && `No answer from the simulator yet. ${money(done.amount)} stays reserved until it is confirmed.`}
          </Alert>
        ) : (
          <Stack gap={2}>
            <Paper sx={{ p: 1.5, bgcolor: "#f0f0f0", border: "none" }}>
              <Row label="Captured" value={money(p.captured_amount)} />
              <Row label="Already refunded" value={money(p.refunded_amount)} />
              {p.refund_reserved_amount > 0 && <Row label="Reserved (in progress)" value={money(p.refund_reserved_amount)} />}
              <Row label="Refundable" value={<b>{money(p.refundable_amount)}</b>} />
            </Paper>
            <ToggleButtonGroup exclusive size="small" value={mode} onChange={(_, v) => v && setMode(v)} aria-label="Refund type">
              <ToggleButton value="full">Full refund</ToggleButton>
              <ToggleButton value="partial">Partial refund</ToggleButton>
            </ToggleButtonGroup>
            <TextField label="Amount (SGD)" value={mode === "full" ? (p.refundable_amount / 100).toFixed(2) : amount}
              onChange={(e) => setAmount(e.target.value)} disabled={mode === "full"} inputProps={{ inputMode: "decimal" }}
              error={mode === "partial" && amount !== "" && !valid} autoFocus />
            <TextField label="Reason (shown in audit log)" value={reason} onChange={(e) => setReason(e.target.value)}
              inputProps={{ maxLength: 200 }} />
            <Typography variant="caption" color="text.secondary">
              Up to {money(p.refundable_amount)}. The money goes back to {cardLabel(p.card)}. This can't be undone.
            </Typography>
            <ErrorAlert error={refund.error} />
          </Stack>
        )}
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2.5 }}>
        <Button variant="outlined" onClick={onClose}>{done ? "Close" : "Cancel"}</Button>
        {!done && (
          <Button variant="contained" disabled={!valid || refund.isPending} onClick={() => refund.mutate()}>
            Refund {valid ? money(cents) : ""}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}

function ConfirmButton({ label, confirm, onConfirm, busy, variant }: {
  label: string; confirm: string; onConfirm: () => void; busy: boolean; variant: "contained" | "outlined";
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant={variant} size="large" onClick={() => setOpen(true)} disabled={busy}>{label}</Button>
      <Dialog open={open} onClose={() => setOpen(false)}>
        <DialogTitle>{label}?</DialogTitle>
        <DialogContent><Typography>{confirm}</Typography></DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Back</Button>
          <Button variant="contained" onClick={() => { setOpen(false); onConfirm(); }}>{label}</Button>
        </DialogActions>
      </Dialog>
    </>
  );
}

export default function PaymentDetail({ refund = false }: { refund?: boolean }) {
  const { paymentId = "" } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const payment = usePayment(paymentId);
  const settle = useMutation({
    mutationFn: (action: "capture" | "cancel") => post<Detail>(`/payments/${paymentId}/${action}`),
    onSuccess: (data) => client.setQueryData(["payment", paymentId], data),
    onSettled: () => client.invalidateQueries({ queryKey: ["payments"] }),
  });

  if (payment.isPending) return <Typography color="text.secondary">Loading…</Typography>;
  if (payment.isError) {
    return payment.error instanceof ApiError && payment.error.status === 404
      ? <Typography>This payment does not exist or belongs to another merchant.</Typography>
      : <ErrorAlert error={payment.error} />;
  }
  const p = payment.data;
  const label = statusLabel(p.status, p.captured_amount, p.refunded_amount);

  return (
    <>
      <Breadcrumbs sx={{ mb: 1.5, fontSize: 13 }}>
        <MuiLink component={Link} to="/payments" color="text.secondary" underline="hover">Payments</MuiLink>
        <Typography fontSize={13} color="text.secondary">{p.reference}</Typography>
      </Breadcrumbs>
      <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" gap={2} mb={3}>
        <Box>
          <Typography variant="h4" component="h1">{money(p.amount)} · Order {p.order_reference}</Typography>
          <Typography color="text.secondary" variant="body2" mt={0.5}>{subtitle(p)}</Typography>
        </Box>
        <Stack direction="row" gap={1.5} alignItems="center" flexWrap="wrap">
          <Badge label={label} tone={p.refunded_amount ? "dark" : statusTone(p.status)} />
          {p.pending_operation && <Badge label={`${titleCase(p.pending_operation.type)} pending`} />}
          {p.actions.capture && (
            <ConfirmButton label={`Capture ${money(p.amount)}`} variant="contained" busy={settle.isPending}
              confirm={`Charge the customer ${money(p.amount)} now.`} onConfirm={() => settle.mutate("capture")} />
          )}
          {p.actions.cancel && (
            <ConfirmButton label="Cancel authorisation" variant="outlined" busy={settle.isPending}
              confirm="Release the authorisation. Nothing is charged and this can't be undone."
              onConfirm={() => settle.mutate("cancel")} />
          )}
          {p.actions.refund && (
            <Button variant="contained" size="large" onClick={() => navigate(`/payments/${p.id}/refund`)}>Refund</Button>
          )}
        </Stack>
      </Stack>
      <ErrorAlert error={settle.error} />
      {settle.data && settle.data.pending_operation && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          The simulator has not answered yet. The {settle.data.pending_operation.type} is being checked with the same
          operation ID and this page updates by itself.
        </Alert>
      )}
      {p.outcome && <Alert severity="info" sx={{ mb: 2 }}>{p.outcome.message}</Alert>}

      <Box sx={{ display: "grid", gap: 2.5, gridTemplateColumns: { xs: "minmax(0, 1fr)", lg: "minmax(0, 1.6fr) minmax(0, 1fr)" }, alignItems: "start" }}>
        <Stack gap={2.5} minWidth={0}>
          <Section title="Summary">
            <Row label="Payment reference" value={p.reference} />
            <Row label="Merchant" value={p.merchant.name} />
            {p.card && <Row label="Card" value={cardLabel(p.card)} />}
            {p.card && <Row label="Card token" value={<Box component="span" sx={{ fontFamily: "monospace", fontSize: 12 }}>{p.card.token}</Box>} />}
            <Row label="Customer" value={p.customer ? `${p.customer} (guest)` : "guest"} />
            {p.description && <Row label="Description" value={p.description} />}
            <Row label="Payment ID" value={<Box component="span" sx={{ fontFamily: "monospace", fontSize: 12 }}>{p.id}</Box>} />
            <Row label="Captured / refunded" value={`${money(p.captured_amount)} / ${money(p.refunded_amount)}`} />
          </Section>
          <Timeline p={p} />
          {(p.status === "captured" || p.refunds.length > 0) && <Refunds refunds={p.refunds} refundable={p.refundable_amount} />}
          <Operations p={p} />
          <Network p={p} />
          <Webhooks p={p} />
          <FullTimeline p={p} />
        </Stack>
        <Stack gap={2} minWidth={0}>
          {p.screening ? (
            <FraudDecision screening={p.screening} badge={riskLabel({ decision: p.screening.outcome, verification: p.verification })} />
          ) : (
            <Section title="Fraud decision">
              <Typography variant="body2" color="text.secondary">Not screened yet: the customer has not paid.</Typography>
            </Section>
          )}
          {p.actions.review && p.screening && (
            <Button variant="outlined" size="large" component={Link} to={`/reviews/${p.screening.id}`}>Open in fraud review</Button>
          )}
        </Stack>
      </Box>
      {p.actions.refund || refund ? (
        <RefundDialog key={String(refund)} p={p} open={refund} onClose={() => navigate(`/payments/${p.id}`)} />
      ) : null}
    </>
  );
}
