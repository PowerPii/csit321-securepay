// Fraud models (v2 learning loop, docs/v2/LEARNING-LOOP.md). Analysts and administrators see
// which model decides, train a challenger from the red team's labels and watch it score in the
// shadow; an administrator promotes it, or rolls back, after entering the authenticator code.
import {
  Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle, Paper, Stack, TextField,
  Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, post } from "../api/client";
import { useStaff } from "../auth";
import { DataTable } from "../components/InsightCharts";
import { Badge, ErrorAlert, PageHeader, Row, ScrollBox, Section, Tile } from "../components/ui";

interface Side { attacks_stopped: number; genuine_stopped: number; caught_rate: number | null; false_positive_rate: number | null }
interface Holdout {
  attacks: number; genuine: number; champion: Side; challenger: Side;
  by_scenario: Record<string, { attacks: number; champion_stopped: number; challenger_stopped: number }>;
}
export interface RiskModelRow {
  version: string; role: "challenger" | "champion" | "retired";
  created_at?: string; created_by?: string | null; promoted_at?: string | null; promoted_by?: string | null;
  retired_at?: string | null; replaced?: string | null; source?: "configured" | "promoted";
  policy: { verify: number | null; block: number | null };
  training?: { rows: number; attacks: number; genuine: number; scenarios: Record<string, number>; holdout: Holdout };
}
interface Shadow {
  screened: number; differs: number; attacks: number; attacks_champion: number; attacks_challenger: number;
  legit: number; legit_champion: number; legit_challenger: number;
}
export interface ModelsOverview {
  active: RiskModelRow;
  challenger: (RiskModelRow & { shadow: Shadow }) | null;
  history: RiskModelRow[];
  data: { attacks: number; genuine: number; needed: number; ready: boolean; feature_schema_version: string };
  feature_schema_version: string;
}

const ZONE = "Asia/Singapore";
const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleString("en-SG", { timeZone: ZONE }) : "–");
const of = (n: number, d: number) => `${n} of ${d}${d ? ` · ${((n / d) * 100).toFixed(1)}%` : ""}`;

function CodeDialog({ title, text, action, open, onClose, run }: {
  title: string; text: string; action: string; open: boolean; onClose: () => void;
  run: (code: string) => Promise<ModelsOverview>;
}) {
  const client = useQueryClient();
  const [code, setCode] = useState("");
  const m = useMutation({
    mutationFn: () => run(code.replace(/\s/g, "")),
    onSuccess: (data) => { client.setQueryData(["risk-models"], data); close(); },
  });
  const close = () => { setCode(""); m.reset(); onClose(); };
  if (!open) return null;
  const valid = /^\d{6}$/.test(code.replace(/\s/g, ""));
  return (
    <Dialog open onClose={close} fullWidth maxWidth="xs" aria-labelledby="model-dialog-title">
      <DialogTitle id="model-dialog-title">{title}</DialogTitle>
      <DialogContent>
        <DialogContentText mb={2}>{text}</DialogContentText>
        <TextField label="Code from your authenticator app" value={code} onChange={(e) => setCode(e.target.value)}
          fullWidth autoFocus inputProps={{ inputMode: "numeric", autoComplete: "one-time-code", maxLength: 7 }} />
        <ErrorAlert error={m.error} />
      </DialogContent>
      <DialogActions>
        <Button onClick={close}>Cancel</Button>
        <Button variant="contained" disabled={!valid || m.isPending} onClick={() => m.mutate()}>{action}</Button>
      </DialogActions>
    </Dialog>
  );
}

function Comparison({ h }: { h: Holdout }) {
  const rows = [
    ["Deciding now", of(h.champion.attacks_stopped, h.attacks), of(h.champion.genuine_stopped, h.genuine)],
    ["Challenger", of(h.challenger.attacks_stopped, h.attacks), of(h.challenger.genuine_stopped, h.genuine)],
  ];
  const scenarios = Object.entries(h.by_scenario);
  return (
    <Stack gap={1.5}>
      <Typography variant="body2" color="text.secondary">
        On a quarter of the labelled attempts kept out of training. "Deciding now" is what screening actually did;
        the challenger is what it would have done, with the same rule hits.
      </Typography>
      <ScrollBox label="Holdout comparison, scrolls sideways">
        <DataTable label="Holdout comparison" head={["Model", "Attacks stopped", "Genuine stopped"]} rows={rows} />
      </ScrollBox>
      {scenarios.length > 0 && (
        <ScrollBox label="Holdout by attack pattern, scrolls sideways">
          <DataTable label="Holdout by attack pattern" head={["Pattern", "Attacks", "Deciding now", "Challenger"]}
            rows={scenarios.map(([name, s]) => [name.replace(/_/g, " "), s.attacks, s.champion_stopped, s.challenger_stopped])} />
        </ScrollBox>
      )}
    </Stack>
  );
}

function ShadowView({ s }: { s: Shadow }) {
  if (s.screened === 0) {
    return <Typography variant="body2" color="text.secondary">No attempts screened since it was trained. Run the red team to see it work.</Typography>;
  }
  return (
    <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: "repeat(3, 1fr)" } }}>
      <Tile label="Screened in the shadow" value={`${s.screened} · ${s.differs} decided differently`} />
      <Tile label="Red-team attacks stopped" value={`${s.attacks_challenger} of ${s.attacks} (now: ${s.attacks_champion})`} />
      <Tile label="Red-team shoppers stopped" value={`${s.legit_challenger} of ${s.legit} (now: ${s.legit_champion})`} />
    </Box>
  );
}

export function ModelsPage() {
  const staff = useStaff();
  const admin = staff.role === "administrator";
  const client = useQueryClient();
  const q = useQuery({ queryKey: ["risk-models"], queryFn: () => api<ModelsOverview>("/risk-models"), refetchInterval: 10_000 });
  const train = useMutation({
    mutationFn: () => post<ModelsOverview>("/risk-models/train"),
    onSuccess: (data) => client.setQueryData(["risk-models"], data),
  });
  const [dialog, setDialog] = useState<"promote" | "rollback" | null>(null);
  const d = q.data;

  return (
    <>
      <PageHeader title="Fraud models"
        subtitle="The model that decides, and a challenger that learns from the red team and scores in the shadow" />
      {q.error && <ErrorAlert error={q.error} />}
      {d && (
        <Stack gap={2.5}>
          <Section title="Deciding now" action={admin && d.active.source === "promoted" && (
            <Button size="small" onClick={() => setDialog("rollback")}>Roll back</Button>
          )}>
            <Row label="Model" value={<Stack direction="row" gap={1} alignItems="center"><code>{d.active.version}</code>
              <Badge label={d.active.source === "promoted" ? "Promoted" : "Configured"} tone="dark" /></Stack>} />
            <Row label="Step-up from score" value={d.active.policy.verify ?? "–"} />
            <Row label="Block from score" value={d.active.policy.block ?? "–"} />
            {d.active.source === "promoted" && (
              <>
                <Row label="Promoted" value={`${when(d.active.promoted_at)} by ${d.active.promoted_by ?? "–"}`} />
                <Row label="Replaced" value={d.active.replaced ?? "–"} />
              </>
            )}
          </Section>

          <Section title="Challenger" action={(
            <Button size="small" variant={d.challenger ? "text" : "contained"} disabled={!d.data.ready || train.isPending}
              onClick={() => train.mutate()}>
              {train.isPending ? "Training…" : d.challenger ? "Train again" : "Train a challenger"}
            </Button>
          )}>
            <ErrorAlert error={train.error} />
            {!d.data.ready && (
              <Alert severity="info" sx={{ mb: 2 }}>
                Training needs at least {d.data.needed} attacks and {d.data.needed} genuine attempts screened with
                {" "}{d.data.feature_schema_version}. There are {d.data.attacks} attacks and {d.data.genuine} genuine attempts so far:
                run the red team first.
              </Alert>
            )}
            {!d.challenger ? (
              <Typography color="text.secondary">No challenger yet.</Typography>
            ) : (
              <Stack gap={2.5}>
                <Box>
                  <Row label="Model" value={<code>{d.challenger.version}</code>} />
                  <Row label="Trained" value={`${when(d.challenger.created_at)} by ${d.challenger.created_by ?? "–"}`} />
                  {d.challenger.training && (
                    <Row label="Trained on" value={`${d.challenger.training.rows} attempts: ${d.challenger.training.attacks} attacks, ${d.challenger.training.genuine} genuine`} />
                  )}
                </Box>
                {d.challenger.training && <Comparison h={d.challenger.training.holdout} />}
                <Box>
                  <Typography variant="subtitle2" mb={1}>In the shadow since then</Typography>
                  <ShadowView s={d.challenger.shadow} />
                </Box>
                {admin ? (
                  <Box><Button variant="contained" onClick={() => setDialog("promote")}>Promote to decide</Button></Box>
                ) : (
                  <Typography variant="body2" color="text.secondary">An administrator can promote it.</Typography>
                )}
              </Stack>
            )}
          </Section>

          {d.history.length > 0 && (
            <Section title="Recent models">
              <ScrollBox label="Recent models, scrolls sideways">
                <DataTable label="Recent models" head={["Model", "Role", "Trained", "Promoted", "Retired"]}
                  rows={d.history.map((m) => [m.version, m.role, when(m.created_at), when(m.promoted_at), when(m.retired_at)])} />
              </ScrollBox>
            </Section>
          )}
          {d.history.length === 0 && d.active.source === "configured" && (
            <Paper sx={{ p: 2, borderRadius: 2 }}>
              <Typography variant="body2" color="text.secondary">
                The configured model ({d.active.version}) has decided every attempt so far.
              </Typography>
            </Paper>
          )}
        </Stack>
      )}
      <CodeDialog open={dialog === "promote" && !!d?.challenger} title="Promote the challenger" action="Promote"
        text={`${d?.challenger?.version ?? ""} will decide every attempt from now on, with its own thresholds. You can roll back at any time.`}
        onClose={() => setDialog(null)}
        run={(code) => post<ModelsOverview>("/risk-models/promote", { version: d!.challenger!.version, code })} />
      <CodeDialog open={dialog === "rollback"} title="Roll back" action="Roll back"
        text={`${d?.active.version ?? ""} stops deciding, and ${d?.active.replaced ?? "the model it replaced"} decides again.`}
        onClose={() => setDialog(null)}
        run={(code) => post<ModelsOverview>("/risk-models/rollback", { code })} />
    </>
  );
}
