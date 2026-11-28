// Issued cards (v2 issuing, docs/v2/ISSUING.md). A merchant operator makes virtual cards at
// SecurePay Bank with a spending limit, sees the number once, and watches the bank's answers
// on each card. Administrators see every merchant's cards, read-only.
import {
  Alert, Box, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle, FormControlLabel,
  Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api, post, query } from "../api/client";
import { useStaff } from "../auth";
import { useMode } from "../mode";
import { Pagination } from "../components/Pagination";
import { Badge, ErrorAlert, PageHeader, Row, RowAction, ScrollBox, Section } from "../components/ui";
import { longDate, money, parseMoney, shortDate } from "../format";

export interface IssuedCard {
  id: string; object: "issued_card"; livemode: boolean; description: string; brand: "visa"; last4: string;
  expiry_month: number; expiry_year: number; currency: "SGD"; spending_limit: number; spent: number;
  remaining: number; single_use: boolean; status: "active" | "used" | "cancelled"; created_at: string;
  merchant_id: string; merchant_name: string; number?: string; cvc?: string;
  authorisations?: { amount: number; approved: boolean; response_code: string; response_text: string;
    captured: number | null; reversed: boolean; created_at: string }[];
  bank_reachable?: boolean;
}
interface CardList { count: number; page: number; page_size: number; data: IssuedCard[] }

const STATUS: Record<IssuedCard["status"], string> = { active: "Active", used: "Used", cancelled: "Cancelled" };
const expiry = (c: IssuedCard) => `${String(c.expiry_month).padStart(2, "0")}/${String(c.expiry_year).slice(-2)}`;
const grouped = (n: string) => n.replace(/(\d{4})(?=\d)/g, "$1 ");

function newKey(): string {
  try {
    return `dash-card-${crypto.randomUUID()}`;
  } catch {
    return `dash-card-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
}

function CreateDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const client = useQueryClient();
  const { mode } = useMode();
  const [limit, setLimit] = useState("30.00");
  const [singleUse, setSingleUse] = useState(true);
  const [description, setDescription] = useState("");
  const [key, setKey] = useState(newKey);
  const [made, setMade] = useState<IssuedCard | null>(null);
  const parsed = parseMoney(limit);
  const cents = parsed !== null && parsed >= 100 && parsed <= 1_000_000 ? parsed : null;
  const m = useMutation({
    mutationFn: () => api<IssuedCard>("/issuing/cards", {
      method: "POST", headers: { "Idempotency-Key": key },
      body: JSON.stringify({ spending_limit: cents, single_use: singleUse, description, mode }),
    }),
    onSuccess: (card) => { setMade(card); client.invalidateQueries({ queryKey: ["issued-cards"] }); },
  });
  const close = () => {
    setMade(null); setLimit("30.00"); setSingleUse(true); setDescription(""); setKey(newKey()); m.reset(); onClose();
  };
  const submit = (e: FormEvent) => { e.preventDefault(); if (cents !== null) m.mutate(); };
  return (
    <Dialog open={open} onClose={close} fullWidth maxWidth="xs" aria-labelledby="card-dialog-title">
      <DialogTitle id="card-dialog-title">{made ? "Your new card" : "Make a virtual card"}</DialogTitle>
      {made ? (
        <>
          <DialogContent>
            <Alert severity="warning" sx={{ mb: 2 }}>
              Copy the number now. It is shown only once: neither SecurePay nor the bank keeps it.
            </Alert>
            {made.number ? (
              <Box aria-label="Card details">
                <Row label="Number" value={<code>{grouped(made.number)}</code>} />
                <Row label="Expiry" value={expiry(made)} />
                <Row label="Security code" value={<code>{made.cvc}</code>} />
                <Row label="Spending limit" value={`${money(made.spending_limit)}${made.single_use ? ", single use" : ""}`} />
              </Box>
            ) : (
              <Typography>This card was already made, so its number cannot be shown again. Cancel it and make another.</Typography>
            )}
          </DialogContent>
          <DialogActions><Button variant="contained" onClick={close}>Done</Button></DialogActions>
        </>
      ) : (
        <form onSubmit={submit} noValidate>
          <DialogContent>
            <DialogContentText mb={2}>
              SecurePay Bank approves payments on this card until they reach its limit, in SGD.
            </DialogContentText>
            <Stack gap={2}>
              <TextField label="Spending limit (S$)" value={limit} onChange={(e) => setLimit(e.target.value)} required
                error={cents === null} helperText={cents === null ? "S$ 1.00 to S$ 10,000.00" : " "}
                inputProps={{ inputMode: "decimal" }} />
              <FormControlLabel control={<Checkbox checked={singleUse} onChange={(e) => setSingleUse(e.target.checked)} />}
                label="Single use: used up by its first approved payment" />
              <TextField label="Description (optional)" value={description} onChange={(e) => setDescription(e.target.value)}
                inputProps={{ maxLength: 100 }} />
            </Stack>
            <ErrorAlert error={m.error} />
          </DialogContent>
          <DialogActions>
            <Button onClick={close}>Cancel</Button>
            <Button type="submit" variant="contained" disabled={cents === null || m.isPending}>
              {m.isPending ? "Making…" : "Make card"}
            </Button>
          </DialogActions>
        </form>
      )}
    </Dialog>
  );
}

function CardDialog({ id, operator, onClose }: { id: string | null; operator: boolean; onClose: () => void }) {
  const client = useQueryClient();
  const q = useQuery({ queryKey: ["issued-card", id], queryFn: () => api<IssuedCard>(`/issuing/cards/${id}`),
    enabled: !!id, refetchInterval: 5_000 });
  const cancel = useMutation({
    mutationFn: () => post<IssuedCard>(`/issuing/cards/${id}/cancel`),
    onSuccess: () => { client.invalidateQueries({ queryKey: ["issued-card", id] }); client.invalidateQueries({ queryKey: ["issued-cards"] }); },
  });
  const c = q.data;
  return (
    <Dialog open={!!id} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="card-detail-title">
      <DialogTitle id="card-detail-title">{c ? `Visa ending ${c.last4}` : "Card"}</DialogTitle>
      <DialogContent>
        <ErrorAlert error={q.error ?? cancel.error} />
        {c && (
          <Stack gap={2}>
            <Box>
              <Row label="Status" value={<Badge label={STATUS[c.status]} tone={c.status === "active" ? "dark" : "neutral"} />} />
              <Row label="Spent" value={`${money(c.spent)} of ${money(c.spending_limit)} (${money(c.remaining)} left)`} />
              <Row label="Use" value={c.single_use ? "Single use" : "Until the limit is reached"} />
              <Row label="Expiry" value={expiry(c)} />
              {c.description && <Row label="Description" value={c.description} />}
              <Row label="Made" value={longDate(c.created_at)} />
            </Box>
            {c.bank_reachable === false && <Alert severity="info">The bank did not answer just now; these are the last known figures.</Alert>}
            <Typography variant="subtitle2">The bank's answers</Typography>
            {c.authorisations && c.authorisations.length > 0 ? (
              <ScrollBox label="Bank answers table, scrolls sideways">
                <Table size="small" aria-label="Bank answers">
                  <TableHead><TableRow><TableCell>When</TableCell><TableCell align="right">Amount</TableCell><TableCell>Answer</TableCell></TableRow></TableHead>
                  <TableBody>
                    {c.authorisations.map((a, i) => (
                      <TableRow key={i}>
                        <TableCell>{shortDate(a.created_at)}</TableCell>
                        <TableCell align="right">{money(a.amount)}</TableCell>
                        <TableCell>
                          {a.response_code} {a.response_text}{a.reversed ? ", cancelled" : ""}
                          {a.captured !== null && a.captured < a.amount ? `, captured ${money(a.captured)}` : ""}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </ScrollBox>
            ) : <Typography variant="body2" color="text.secondary">No payments on this card yet.</Typography>}
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        {operator && c && c.status !== "cancelled" && (
          <Button color="error" disabled={cancel.isPending} onClick={() => cancel.mutate()}>Cancel card</Button>
        )}
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
}

export function IssuedCardsPage() {
  const staff = useStaff();
  const operator = staff.role === "merchant_operator";
  const { mode } = useMode();
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const list = useQuery({
    queryKey: ["issued-cards", mode, page],
    queryFn: () => api<CardList>(`/issuing/cards${query({ mode, page })}`),
    placeholderData: keepPreviousData, refetchInterval: 10_000,
  });
  const d = list.data;
  return (
    <>
      <PageHeader title="Issued cards"
        subtitle="Virtual cards from SecurePay Bank, each with its own spending limit"
        action={operator && <Button variant="contained" onClick={() => setCreating(true)}>Make a card</Button>} />
      <ErrorAlert error={list.error} />
      <Section title={`Cards (${mode} mode)`}>
        {d && d.data.length === 0 ? (
          <Typography color="text.secondary">
            {operator ? "No cards yet. Make one to pay with it at any shop on the SecurePay Network." : "No merchant has made a card yet."}
          </Typography>
        ) : (
          <ScrollBox label="Issued cards table, scrolls sideways">
            <Table aria-label="Issued cards">
              <TableHead>
                <TableRow>
                  <TableCell>Card</TableCell>{!operator && <TableCell>Merchant</TableCell>}
                  <TableCell align="right">Spent</TableCell><TableCell align="right">Limit</TableCell>
                  <TableCell>Status</TableCell><TableCell>Made</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {d?.data.map((c) => (
                  <TableRow key={c.id} hover>
                    <TableCell>
                      <RowAction onClick={() => setOpen(c.id)} label={`Open card ending ${c.last4}`}>
                        •••• {c.last4}{c.single_use ? " · single use" : ""}
                      </RowAction>
                      {c.description && <Typography variant="body2" color="text.secondary">{c.description}</Typography>}
                    </TableCell>
                    {!operator && <TableCell>{c.merchant_name}</TableCell>}
                    <TableCell align="right">{money(c.spent)}</TableCell>
                    <TableCell align="right">{money(c.spending_limit)}</TableCell>
                    <TableCell>{STATUS[c.status]}</TableCell>
                    <TableCell>{shortDate(c.created_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </ScrollBox>
        )}
        {d && d.count > d.page_size && <Pagination page={page} data={d} onChange={setPage} />}
      </Section>
      <CreateDialog open={creating} onClose={() => setCreating(false)} />
      <CardDialog id={open} operator={operator} onClose={() => setOpen(null)} />
    </>
  );
}
