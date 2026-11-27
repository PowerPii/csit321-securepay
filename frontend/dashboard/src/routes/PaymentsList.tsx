// D3 Payments: tiles, filters (date, status, risk decision, search), table, paging.
// Merchant operators see their merchant; analysts and administrators see every merchant.
import {
  Box, MenuItem, Paper, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField,
  Typography,
} from "@mui/material";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, query, type Merchant, type PaymentList } from "../api/client";
import { useStaff } from "../auth";
import { useMode } from "../mode";
import { Pagination } from "../components/Pagination";
import { Badge, ErrorAlert, PageHeader, Tile } from "../components/ui";
import { cardLabel, money, riskLabel, shortDate, STATUS_LABELS, statusLabel, statusTone } from "../format";

const FILTERS = ["status", "decision", "from", "to", "q", "merchant", "attention", "page"] as const;

export default function PaymentsList() {
  const staff = useStaff();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const filters = Object.fromEntries(FILTERS.map((k) => [k, params.get(k) ?? ""])) as Record<
    (typeof FILTERS)[number], string>;
  const page = Number(filters.page || 1);
  const allMerchants = staff.merchant === null;
  const isAdmin = staff.role === "administrator";

  // A search finds a payment in either mode (a receipt doesn't say which); otherwise the list
  // shows the mode chosen in the sidebar.
  const { mode: chosen } = useMode();
  const mode = filters.q ? "" : chosen;
  const list = useQuery({
    queryKey: ["payments", mode, filters],
    queryFn: () => api<PaymentList>(`/payments${query({ ...filters, mode, page_size: 20 })}`),
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  });
  const merchants = useQuery({
    queryKey: ["merchants"],
    queryFn: () => api<{ data: Merchant[] }>("/merchants"),
    enabled: isAdmin,
  });

  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "page") next.delete("page");
    setParams(next);
  };

  const data = list.data;
  const summary = data?.summary;
  const first = data && data.count ? (data.page - 1) * data.page_size + 1 : 0;
  const last = data ? Math.min(data.count, data.page * data.page_size) : 0;

  return (
    <>
      <PageHeader
        title="Payments"
        subtitle={`${mode === "" ? "Test and live" : mode === "live" ? "Live mode" : "Test mode"}. ${staff.merchant ? `All payments for ${staff.merchant.name}` : "Payments across all merchants (read-only)"}`}
      />
      {summary && (
        <Stack direction="row" gap={2} flexWrap="wrap" mb={3}>
          <Tile label="Awaiting capture" value={summary.awaiting_capture} />
          <Tile label="Unresolved" value={summary.unresolved} />
          <Tile label="Blocked today" value={summary.blocked_today} />
          <Tile label="Refunded this month" value={money(summary.refunded_this_month)} />
          {summary.escalated !== null && (
            <Paper
              component="button"
              onClick={() => set("attention", filters.attention ? "" : "escalated")}
              sx={{ p: 2, borderRadius: 2, flex: 1, minWidth: 150, textAlign: "left", cursor: "pointer",
                bgcolor: filters.attention ? "#e6e6e6" : "#fff", font: "inherit" }}
              aria-pressed={!!filters.attention}
            >
              <Typography variant="body2" color="text.secondary">Escalated operations</Typography>
              <Typography variant="h5" component="p" mt={0.5}>{summary.escalated}</Typography>
            </Paper>
          )}
        </Stack>
      )}

      <Box sx={{ display: "grid", gap: 2, mb: 2,
        gridTemplateColumns: { xs: "1fr 1fr", md: isAdmin ? "repeat(6, 1fr)" : "repeat(5, 1fr)" } }}>
        <TextField label="From" type="date" value={filters.from} onChange={(e) => set("from", e.target.value)}
          InputLabelProps={{ shrink: true }} />
        <TextField label="To" type="date" value={filters.to} onChange={(e) => set("to", e.target.value)}
          InputLabelProps={{ shrink: true }} />
        <TextField select label="Status" value={filters.status} onChange={(e) => set("status", e.target.value)}>
          <MenuItem value="">All statuses</MenuItem>
          {Object.entries(STATUS_LABELS).map(([value, label]) => (
            <MenuItem key={value} value={value}>{label}</MenuItem>
          ))}
        </TextField>
        <TextField select label="Risk decision" value={filters.decision} onChange={(e) => set("decision", e.target.value)}>
          <MenuItem value="">All</MenuItem>
          <MenuItem value="allow">Allow</MenuItem>
          <MenuItem value="verify">Verify (any)</MenuItem>
          <MenuItem value="verify_passed">Verify · passed</MenuItem>
          <MenuItem value="verify_failed">Verify · failed</MenuItem>
          <MenuItem value="block">Block</MenuItem>
          <MenuItem value="not_screened">Not screened</MenuItem>
        </TextField>
        {isAdmin && (
          <TextField select label="Merchant" value={filters.merchant} onChange={(e) => set("merchant", e.target.value)}>
            <MenuItem value="">All merchants</MenuItem>
            {(merchants.data?.data ?? []).map((m) => <MenuItem key={m.id} value={m.id}>{m.name}</MenuItem>)}
          </TextField>
        )}
        <TextField label="Search" placeholder="SP- or RF- reference, order # or email" defaultValue={filters.q} key={filters.q}
          onKeyDown={(e) => { if (e.key === "Enter") set("q", (e.target as HTMLInputElement).value.trim()); }}
          onBlur={(e) => { if (e.target.value.trim() !== filters.q) set("q", e.target.value.trim()); }} />
      </Box>

      <ErrorAlert error={list.error} />
      <TableContainer component={Paper} sx={{ borderRadius: 2 }}>
        <Table size="medium" aria-label="Payments">
          <TableHead>
            <TableRow>
              <TableCell>Reference</TableCell>
              <TableCell>Created</TableCell>
              <TableCell>Order</TableCell>
              {allMerchants && <TableCell>Merchant</TableCell>}
              <TableCell align="right">Amount</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Card</TableCell>
              <TableCell>Risk decision</TableCell>
              <TableCell>Customer</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {data?.data.map((p) => (
              <TableRow key={p.id} hover sx={{ cursor: "pointer" }} onClick={() => navigate(`/payments/${p.id}`)}>
                <TableCell sx={{ whiteSpace: "nowrap" }}>
                  <Typography component="a" href={`${import.meta.env.BASE_URL}payments/${p.id}`} variant="body2"
                    onClick={(e) => { e.preventDefault(); }} sx={{ color: "inherit", textDecoration: "none" }}>
                    {p.reference}
                  </Typography>
                </TableCell>
                <TableCell sx={{ whiteSpace: "nowrap" }}>{shortDate(p.created_at)}</TableCell>
                <TableCell>
                  {p.order_reference}
                  {mode === "" && p.livemode && <> <Badge label="Live" /></>}
                </TableCell>
                {allMerchants && <TableCell>{p.merchant.name}</TableCell>}
                <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>{money(p.amount)}</TableCell>
                <TableCell>
                  <Badge label={statusLabel(p.status, p.amount, p.refunded_amount)}
                    tone={p.refunded_amount ? "dark" : statusTone(p.status)} />
                </TableCell>
                <TableCell sx={{ whiteSpace: "nowrap" }}>{cardLabel(p.card)}</TableCell>
                <TableCell>{p.risk && <Badge label={riskLabel(p.risk)} />}</TableCell>
                <TableCell>{p.customer ?? "guest"}</TableCell>
              </TableRow>
            ))}
            {data && data.data.length === 0 && (
              <TableRow>
                <TableCell colSpan={9}>
                  <Typography color="text.secondary" py={2} textAlign="center">No payments match these filters.</Typography>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </TableContainer>
      <Stack direction="row" alignItems="center" justifyContent="space-between" mt={2}>
        <Typography variant="body2" color="text.secondary">
          {data ? `Showing ${first}–${last} of ${data.count}` : "Loading…"}
        </Typography>
        <Pagination page={page} data={data} onChange={(next) => set("page", String(next))} mt={0} />
      </Stack>
    </>
  );
}
