// Red team live view (v2, docs/v2/RED-TEAM.md), for analysts and administrators. A run is
// started from a laptop (`python redteam/run.py`); this page shows, every few seconds, how
// many attacks arrived and what screening did with them, so the block rate can be seen
// following the attack rate. The labels (attack or background shopper) come from the runner
// and are never read by screening.
import { Alert, Box, MenuItem, Paper, Stack, TextField, Typography } from "@mui/material";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import {
  ChartSection, DataTable, Legend, OUTCOME_COLORS, OUTCOME_LABELS, StackedColumns, type OutcomeKey,
} from "../components/InsightCharts";
import { Badge, ErrorAlert, PageHeader, ScrollBox, Section, Tile } from "../components/ui";

export interface RedTeamRun {
  id: string; scenario: string; note: string | null; started_at: string; expires_at: string;
  ended_at: string | null; live: boolean;
}
interface RunRow extends RedTeamRun { attacks: number }
interface RunList { enabled: boolean; data: RunRow[] }

export interface RedTeamLive {
  run: RedTeamRun;
  bucket_seconds: number;
  buckets: { at: string; attacks: number; attacks_blocked: number; attacks_verify: number;
    attacks_allowed: number; legit: number; legit_stopped: number }[];
  scenarios: { scenario: string; attacks: number; blocked: number; verify: number; allowed: number; pending: number }[];
  totals: { attacks: number; attacks_screened: number; stopped: number; legit: number; legit_stopped: number;
    caught_rate: number | null; false_positive_rate: number | null };
}

const ATTACK_KEYS: OutcomeKey[] = ["allowed", "verify", "blocked"];
const SHOWN = 60; // the last 10 minutes of a run, at 10 seconds a column
const POLL_MS = 3_000;
const ZONE = "Asia/Singapore";

const rate = (r: number | null) => (r === null ? "–" : `${(r * 100).toFixed(1)}%`);
const clock = (iso: string) => new Date(iso).toLocaleTimeString("en-SG", { timeZone: ZONE, hour12: false });
const scenarioLabel = (s: string) => s.replace(/_/g, " ");

/** The columns to draw: from the first column up to the last one with any payment, at most SHOWN. */
export function visibleBuckets<T extends { attacks: number; legit: number }>(buckets: T[], live: boolean): T[] {
  let last = buckets.length - 1;
  if (!live) while (last > 0 && buckets[last].attacks + buckets[last].legit === 0) last -= 1;
  return buckets.slice(Math.max(0, last + 1 - SHOWN), last + 1);
}

function RunView({ id }: { id: string }) {
  const q = useQuery({
    queryKey: ["red-team-run", id],
    queryFn: () => api<RedTeamLive>(`/red-team/runs/${encodeURIComponent(id)}`),
    placeholderData: keepPreviousData,
    refetchInterval: (query) => (query.state.data?.run.live === false ? false : POLL_MS),
  });
  if (q.error) return <ErrorAlert error={q.error} />;
  const d = q.data;
  if (!d) return <Typography color="text.secondary">Loading the run…</Typography>;
  const t = d.totals;
  const shown = visibleBuckets(d.buckets, d.run.live);
  const rows = shown.map((b) => ({
    label: clock(b.at).slice(3), long: clock(b.at),
    values: { allowed: b.attacks_allowed, verify: b.attacks_verify, blocked: b.attacks_blocked },
  }));

  return (
    <Stack gap={2.5}>
      <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr 1fr", md: "repeat(4, 1fr)" } }}>
        <Tile label="Attacks" value={t.attacks} />
        <Tile label="Stopped (blocked or code)" value={t.stopped} />
        <Tile label="Caught rate" value={rate(t.caught_rate)} />
        <Tile label="Shoppers wrongly stopped" value={`${t.legit_stopped} of ${t.legit} · ${rate(t.false_positive_rate)}`} />
      </Box>

      <ChartSection
        title={`Attacks per ${d.bucket_seconds} seconds`}
        subtitle={d.run.live ? `Updates every ${POLL_MS / 1000} seconds while the run is live` : "The run has ended"}
        table={<DataTable label="Attacks over time" head={["Time", "Attacks", "Blocked", "Step-up (code)", "Allowed", "Shoppers", "Shoppers stopped"]}
          rows={shown.map((b) => [clock(b.at), b.attacks, b.attacks_blocked, b.attacks_verify, b.attacks_allowed, b.legit, b.legit_stopped])} />}
      >
        <StackedColumns keys={ATTACK_KEYS} rows={rows}
          ariaLabel={`Attacks per ${d.bucket_seconds} seconds by what screening decided; ${t.attacks} attacks, ${t.stopped} stopped`} />
        <Legend items={ATTACK_KEYS.map((k) => ({ label: OUTCOME_LABELS[k], color: OUTCOME_COLORS[k] }))} />
      </ChartSection>

      <Section title="By attack pattern">
        {d.scenarios.length === 0
          ? <Typography color="text.secondary">No attacks yet.</Typography>
          : <ScrollBox label="Attacks by pattern, scrolls sideways"><DataTable label="Attacks by pattern" head={["Pattern", "Attacks", "Blocked", "Step-up (code)", "Allowed", "Not paid yet"]}
              rows={d.scenarios.map((s) => [scenarioLabel(s.scenario), s.attacks, s.blocked, s.verify, s.allowed, s.pending])} /></ScrollBox>}
      </Section>
    </Stack>
  );
}

export function RedTeamPage() {
  const runs = useQuery({
    queryKey: ["red-team-runs"],
    queryFn: () => api<RunList>("/red-team/runs"),
    refetchInterval: 10_000,
  });
  const [picked, setPicked] = useState<string | null>(null);
  const list = runs.data?.data ?? [];
  const id = picked ?? list[0]?.id ?? null;
  const current = list.find((r) => r.id === id);

  return (
    <>
      <PageHeader title="Red team"
        subtitle="Attacks we run on our own gateway, with test cards only, and how screening handles them"
        action={list.length > 0 && (
          <TextField select size="small" label="Run" value={id ?? ""} onChange={(e) => setPicked(e.target.value)} sx={{ minWidth: 260 }}>
            {list.map((r) => (
              <MenuItem key={r.id} value={r.id}>
                {`${scenarioLabel(r.scenario)} · ${new Date(r.started_at).toLocaleString("en-SG", { timeZone: ZONE })}${r.live ? " · live" : ""}`}
              </MenuItem>
            ))}
          </TextField>
        )} />
      {runs.error && <ErrorAlert error={runs.error} />}
      {runs.data && !runs.data.enabled && (
        <Alert severity="info" sx={{ mb: 2 }}>The red team is turned off on this gateway. It runs on local and staging only.</Alert>
      )}
      {runs.data && list.length === 0 && (
        <Paper sx={{ p: 3, borderRadius: 2 }}>
          <Typography color="text.secondary" textAlign="center">
            No runs yet. Start one from a laptop with <code>python redteam/run.py</code> and a test API key.
          </Typography>
        </Paper>
      )}
      {current && (
        <Stack direction="row" gap={1} alignItems="center" mb={2}>
          <Badge label={current.live ? "Live" : "Ended"} tone={current.live ? "dark" : "neutral"} />
          {current.note && <Typography variant="body2" color="text.secondary">{current.note}</Typography>}
        </Stack>
      )}
      {id && <RunView key={id} id={id} />}
    </>
  );
}
