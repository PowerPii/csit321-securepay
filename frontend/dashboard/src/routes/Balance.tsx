// Balance (v2 ledger). No wireframe yet: it reuses the Overview tiles and the D3 list.
// Merchant operators see what they are owed (available and held in reserve), when the next
// reserve is released, every ledger entry behind it and their payouts, and can pay out the
// available balance. Administrators see the gateway's books: money in from the network, our
// fee revenue and every merchant's balances, with the ledger self-check, read-only.
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Paper, Stack, Table, TableBody,
  TableCell, TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  api, post, query, type Balance, type GatewayBalance, type LedgerEntry, type LedgerEntryList, type LedgerRules,
  type MerchantBalance, type Page, type Payout,
} from "../api/client";
import { useStaff } from "../auth";
import { Pagination } from "../components/Pagination";
import { ErrorAlert, PageHeader, ScrollBox, Section, Tile } from "../components/ui";
import { longDate, money, parseMoney, shortDate } from "../format";

export const KIND_LABEL: Record<string, string> = {
  capture: "Capture",
  refund: "Refund",
  reserve_release: "Reserve released",
  payout: "Payout",
};

export const ACCOUNT_LABEL: Record<string, string> = {
  network_clearing: "Network clearing",
  fee_revenue: "SecurePay fees",
  merchant_available: "Merchant available",
  merchant_reserve: "Merchant reserve",
};

const KINDS = [{ key: "", label: "All" }, ...Object.entries(KIND_LABEL).map(([key, label]) => ({ key, label }))];

/** "2.9% + S$ 0.30 per capture, 10% held for 7 days". */
export function rulesText(r: LedgerRules): string {
  const pct = (bps: number) => `${(bps / 100).toLocaleString("en-SG", { maximumFractionDigits: 2 })}%`;
  return `Fee ${pct(r.fee_percent_bps)} + ${money(r.fee_fixed)} per capture. `
    + `${pct(r.reserve_bps)} of each capture is held in reserve for ${r.reserve_hold_days} days.`;
}

/** The merchant's side of an entry: what it did to available and reserve. */
function merchantEffect(e: LedgerEntry): { available: number; reserve: number } {
  let available = 0;
  let reserve = 0;
  for (const l of e.lines) {
    if (l.account === "merchant_available") available -= l.amount; // credit-normal
    if (l.account === "merchant_reserve") reserve -= l.amount;
  }
  return { available, reserve };
}

function signed(cents: number): string {
  if (cents === 0) return "–";
  return cents > 0 ? `+${money(cents)}` : money(cents);
}

function LinesTable({ entry }: { entry: LedgerEntry }) {
  const total = entry.lines.reduce((a, l) => a + l.amount, 0);
  return (
    <Table size="small" aria-label={`Lines of ${entry.id}`}>
      <TableHead>
        <TableRow><TableCell>Account</TableCell><TableCell>Line</TableCell><TableCell align="right">Debit</TableCell><TableCell align="right">Credit</TableCell></TableRow>
      </TableHead>
      <TableBody>
        {entry.lines.map((l, i) => (
          <TableRow key={i}>
            <TableCell>{ACCOUNT_LABEL[l.account] ?? l.account}</TableCell>
            <TableCell>{l.memo}</TableCell>
            <TableCell align="right">{l.amount > 0 ? money(l.amount) : ""}</TableCell>
            <TableCell align="right">{l.amount < 0 ? money(-l.amount) : ""}</TableCell>
          </TableRow>
        ))}
        <TableRow>
          <TableCell colSpan={2} sx={{ fontWeight: 600 }}>Total (debits minus credits)</TableCell>
          <TableCell colSpan={2} align="right" sx={{ fontWeight: 600 }}>{total === 0 ? "S$ 0.00, balanced" : money(total)}</TableCell>
        </TableRow>
      </TableBody>
    </Table>
  );
}

function EntryDialog({ entry, onClose }: { entry: LedgerEntry | null; onClose: () => void }) {
  return (
    <Dialog open={!!entry} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="entry-title">
      <DialogTitle id="entry-title">{entry ? `${KIND_LABEL[entry.kind] ?? entry.kind}: ${entry.description}` : "Entry"}</DialogTitle>
      <DialogContent>
        {entry && (
          <>
            <Typography variant="body2" color="text.secondary" mb={1}>
              <code>{entry.id}</code> · {longDate(entry.occurred_at)}{entry.backfilled ? " · added for a payment made before the ledger" : ""}
            </Typography>
            {entry.payment_id && (
              <Typography variant="body2" mb={1}>Payment <Link to={`/payments/${entry.payment_id}`}>{entry.payment_reference}</Link></Typography>
            )}
            <Paper variant="outlined"><ScrollBox label="Entry lines table"><LinesTable entry={entry} /></ScrollBox></Paper>
          </>
        )}
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  );
}

function EntriesSection({ admin }: { admin: boolean }) {
  const [params, setParams] = useSearchParams();
  const kind = params.get("kind") ?? "";
  const page = Number(params.get("page") ?? "1") || 1;
  const [open, setOpen] = useState<LedgerEntry | null>(null);
  const list = useQuery({
    queryKey: ["ledger-entries", kind, page],
    queryFn: () => api<LedgerEntryList>(`/ledger/entries${query({ kind, page })}`),
    placeholderData: keepPreviousData,
    refetchInterval: 10_000,
  });
  const set = (next: Record<string, string>) => {
    const merged: Record<string, string> = { kind, ...next };
    if (!("page" in next)) merged.page = "1";
    setParams(new URLSearchParams(Object.entries(merged).filter(([k, v]) => v && !(k === "page" && v === "1"))));
  };
  const data = list.data;
  const cols = admin ? 6 : 5;
  return (
    <Section title="Ledger entries">
      <Stack direction="row" gap={1} flexWrap="wrap" role="group" aria-label="Filter by entry kind" mb={2}>
        {KINDS.map((k) => (
          <Chip key={k.key || "all"} clickable label={k.label} onClick={() => set({ kind: k.key })} aria-pressed={kind === k.key}
            sx={{ fontWeight: 600, bgcolor: kind === k.key ? "#13243F" : "#E8ECF2", color: kind === k.key ? "#fff" : "#13243F",
              border: "1px solid #8a8a8a" }} />
        ))}
      </Stack>
      <ErrorAlert error={list.error} />
      <ScrollBox label="Ledger entries table">
        <Table aria-label="Ledger entries">
          <TableHead>
            <TableRow>
              <TableCell>When</TableCell><TableCell>Entry</TableCell>
              {admin && <TableCell>Merchant</TableCell>}
              {admin ? <TableCell align="right">Amount</TableCell> : <><TableCell align="right">Available</TableCell><TableCell align="right">Reserve</TableCell></>}
              <TableCell>Lines</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {data?.data.map((e) => {
              const effect = merchantEffect(e);
              const gross = e.lines.filter((l) => l.amount > 0).reduce((a, l) => a + l.amount, 0);
              return (
                <TableRow key={e.id} hover sx={{ cursor: "pointer" }} onClick={() => setOpen(e)}>
                  <TableCell sx={{ whiteSpace: "nowrap" }}>{shortDate(e.occurred_at)}</TableCell>
                  <TableCell>
                    <Typography variant="body2" fontWeight={600}>
                      <Box component="button" type="button" aria-label={`Open ${KIND_LABEL[e.kind]} ${e.id}`}
                        onClick={(ev: { stopPropagation: () => void }) => { ev.stopPropagation(); setOpen(e); }}
                        sx={{ font: "inherit", color: "inherit", background: "none", border: 0, p: 0, cursor: "pointer", textAlign: "left",
                          "&:hover": { textDecoration: "underline" }, "&:focus-visible": { outline: "2px solid #13243F", outlineOffset: 2 } }}>
                        {KIND_LABEL[e.kind] ?? e.kind}
                      </Box>
                    </Typography>
                    <Typography variant="caption" color="text.secondary">{e.description}</Typography>
                  </TableCell>
                  {admin && <TableCell>{e.merchant_name}</TableCell>}
                  {admin
                    ? <TableCell align="right">{money(gross)}</TableCell>
                    : <><TableCell align="right">{signed(effect.available)}</TableCell><TableCell align="right">{signed(effect.reserve)}</TableCell></>}
                  <TableCell>{e.lines.length} lines, balanced</TableCell>
                </TableRow>
              );
            })}
            {data && data.data.length === 0 && (
              <TableRow><TableCell colSpan={cols}><Typography color="text.secondary" py={2} textAlign="center">No entries yet. Capture a payment to see its first one.</Typography></TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </ScrollBox>
      <Pagination page={page} data={data} onChange={(next) => set({ page: String(next) })} />
      <EntryDialog entry={open} onClose={() => setOpen(null)} />
    </Section>
  );
}

function PayoutForm({ balance }: { balance: MerchantBalance }) {
  const client = useQueryClient();
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const pay = useMutation({
    mutationFn: (amount: number) => post<Payout>("/payouts", { amount }),
    onSuccess: () => {
      setText("");
      client.invalidateQueries({ queryKey: ["balance"] });
      client.invalidateQueries({ queryKey: ["payouts"] });
      client.invalidateQueries({ queryKey: ["ledger-entries"] });
    },
  });
  const submit = (ev: FormEvent) => {
    ev.preventDefault();
    const amount = parseMoney(text);
    if (amount === null) { setError("Enter an amount like 25.00."); return; }
    if (amount < balance.rules.payout_minimum) { setError(`The smallest payout is ${money(balance.rules.payout_minimum)}.`); return; }
    if (amount > balance.available) { setError(`Only ${money(Math.max(balance.available, 0))} is available.`); return; }
    setError(null);
    pay.mutate(amount);
  };
  return (
    <Stack component="form" onSubmit={submit} direction={{ xs: "column", sm: "row" }} gap={1} alignItems={{ sm: "flex-start" }} aria-label="Pay out">
      <TextField size="small" label="Amount (S$)" value={text} onChange={(ev) => setText(ev.target.value)}
        error={!!error} helperText={error ?? `Up to ${money(Math.max(balance.available, 0))}`} inputProps={{ inputMode: "decimal" }} />
      <Button type="submit" variant="contained" disabled={pay.isPending || balance.available < balance.rules.payout_minimum}>Pay out</Button>
      <Button variant="outlined" disabled={pay.isPending || balance.available < balance.rules.payout_minimum}
        onClick={() => setText((balance.available / 100).toFixed(2))}>Everything</Button>
      <Box role="status" sx={{ flex: 1 }}>
        {pay.isSuccess && <Alert severity="success">Paid out {money(pay.data.amount)} to your bank account.</Alert>}
      </Box>
      <ErrorAlert error={pay.error} />
    </Stack>
  );
}

function PayoutsSection() {
  const [page, setPage] = useState(1);
  const list = useQuery({
    queryKey: ["payouts", page],
    queryFn: () => api<Page<Payout>>(`/payouts${query({ page })}`),
    placeholderData: keepPreviousData,
  });
  return (
    <Section title="Payouts">
      <ErrorAlert error={list.error} />
      <ScrollBox label="Payouts table">
        <Table size="small" aria-label="Payouts">
          <TableHead><TableRow><TableCell>When</TableCell><TableCell>Payout</TableCell><TableCell>Status</TableCell><TableCell align="right">Amount</TableCell></TableRow></TableHead>
          <TableBody>
            {list.data?.data.map((p) => (
              <TableRow key={p.id}>
                <TableCell sx={{ whiteSpace: "nowrap" }}>{shortDate(p.created_at)}</TableCell>
                <TableCell sx={{ fontFamily: "monospace", fontSize: 12 }}>{p.id}</TableCell>
                <TableCell>Paid</TableCell>
                <TableCell align="right">{money(p.amount)}</TableCell>
              </TableRow>
            ))}
            {list.data && list.data.data.length === 0 && (
              <TableRow><TableCell colSpan={4}><Typography color="text.secondary" textAlign="center" py={1}>No payouts yet.</Typography></TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </ScrollBox>
      <Pagination page={page} data={list.data} onChange={setPage} />
    </Section>
  );
}

function MerchantView({ b }: { b: MerchantBalance }) {
  return (
    <>
      <Stack direction={{ xs: "column", md: "row" }} gap={2} mb={2}>
        <Tile label="Available to pay out" value={money(b.available)} />
        <Tile label="Held in reserve" value={money(b.reserve)} />
        <Tile label="Next reserve release" value={b.next_release ? `${money(b.next_release.amount)} on ${shortDate(b.next_release.at)}` : "Nothing held"} />
      </Stack>
      <Box mb={2}>
        <Section title="Pay out to your bank">
          <PayoutForm balance={b} />
        </Section>
      </Box>
      <Box mb={2}><EntriesSection admin={false} /></Box>
      <PayoutsSection />
    </>
  );
}

function GatewayView({ b }: { b: GatewayBalance }) {
  const owed = b.fee_revenue + b.merchants_available + b.merchants_reserve;
  return (
    <>
      <Stack direction={{ xs: "column", md: "row" }} gap={2} mb={2}>
        <Tile label="Network clearing" value={money(b.network_clearing)} />
        <Tile label="SecurePay fees" value={money(b.fee_revenue)} />
        <Tile label="Owed to merchants" value={money(b.merchants_available + b.merchants_reserve)} />
      </Stack>
      <Box mb={2}>
        {b.check.ok
          ? <Alert severity="success">The books balance: every one of the {b.entries} entries sums to zero, and clearing ({money(b.network_clearing)}) equals fees plus what merchants are owed ({money(owed)}).</Alert>
          : (
            <Alert severity="error">
              The ledger check found {b.check.problems.length} problem{b.check.problems.length === 1 ? "" : "s"}:
              <Box component="ul" sx={{ m: 0, pl: 2 }}>{b.check.problems.map((p) => <li key={p}>{p}</li>)}</Box>
            </Alert>
          )}
      </Box>
      <Box mb={2}>
        <Section title="Merchant balances">
          <ScrollBox label="Merchant balances table">
            <Table size="small" aria-label="Merchant balances">
              <TableHead><TableRow><TableCell>Merchant</TableCell><TableCell align="right">Available</TableCell><TableCell align="right">Reserve</TableCell></TableRow></TableHead>
              <TableBody>
                {b.merchants.map((m) => (
                  <TableRow key={m.merchant_id}>
                    <TableCell>{m.merchant_name}</TableCell>
                    <TableCell align="right">{money(m.available)}</TableCell>
                    <TableCell align="right">{money(m.reserve)}</TableCell>
                  </TableRow>
                ))}
                {b.merchants.length === 0 && (
                  <TableRow><TableCell colSpan={3}><Typography color="text.secondary" textAlign="center" py={1}>No captures yet.</Typography></TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          </ScrollBox>
        </Section>
      </Box>
      <EntriesSection admin />
    </>
  );
}

export function BalancePage() {
  const staff = useStaff();
  const admin = staff.role === "administrator";
  const balance = useQuery({
    queryKey: ["balance"],
    queryFn: () => api<Balance>("/balance"),
    refetchInterval: 10_000,
  });
  const b = balance.data;
  return (
    <>
      <PageHeader title="Balance"
        subtitle={b ? rulesText(b.rules) : admin ? "The gateway's books." : "What you are owed, and every movement behind it."} />
      <ErrorAlert error={balance.error} />
      {b?.scope === "merchant" && <MerchantView b={b} />}
      {b?.scope === "gateway" && <GatewayView b={b} />}
    </>
  );
}
