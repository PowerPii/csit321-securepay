// D8 API keys, for merchant operators: approval status, the API key (generated once the
// merchant is approved, shown once) and the webhook address submitted for approval
// (FR-MER-03, FR-MER-10), and rotating or revoking the key after confirming with the
// authenticator code (FR-MER-04), with a warning before the only active key is revoked (FR-MER-05).
// v2: one test key (sp_test_) and one live key (sp_live_), each generated, rotated and revoked
// on its own. Both share the webhook signing secret.
import {
  Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle, Paper, Stack, Table,
  TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, post, type KeyMode, type OwnApiKey, type OwnMerchant } from "../api/client";
import { Badge, ErrorAlert, PageHeader, Section, SrOnly } from "../components/ui";
import { longDate, titleCase } from "../format";

function Secret({ label, value, testId }: { label: string; value: string; testId: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard?.writeText(value).catch(() => undefined);
    setCopied(true);
  };
  return (
    <Box mb={1.5}>
      <Typography variant="body2" color="text.secondary" mb={0.5}>{label}</Typography>
      <Stack direction="row" gap={1}>
        <Paper variant="outlined" sx={{ px: 1.5, py: 1, flexGrow: 1, minWidth: 0 }}>
          <Typography sx={{ fontFamily: "monospace", fontSize: 14, wordBreak: "break-all" }} data-testid={testId}>{value}</Typography>
        </Paper>
        <Button variant="outlined" onClick={copy}>{copied ? "Copied" : "Copy"}</Button>
      </Stack>
    </Box>
  );
}

/** Rotate or revoke, confirmed with the current TOTP code (UC-17). */
const MODE_LABEL: Record<KeyMode, string> = { test: "Test", live: "Live" };

type Change = { action: "rotate" | "revoke"; mode: KeyMode };

function KeyChange({ change: target, otherKey, onClose, onDone }: {
  change: Change | null;
  otherKey: boolean;
  onClose: () => void;
  onDone: (m: OwnMerchant) => void;
}) {
  const [code, setCode] = useState("");
  const action = target?.action ?? null;
  const mode = target?.mode ?? "test";
  const word = mode === "live" ? "live" : "test";
  const change = useMutation({
    mutationFn: () => post<OwnMerchant>(`/merchant/api-key/${action}`,
      action === "revoke" ? { code, mode, confirm_last_key: true } : { code, mode }),
    onSuccess: (m) => { setCode(""); onDone(m); },
  });
  const close = () => { setCode(""); change.reset(); onClose(); };
  const valid = /^\d{6}$/.test(code.replace(/\s/g, ""));
  return (
    <Dialog open={!!action} onClose={close} fullWidth maxWidth="xs" aria-labelledby="key-change-title">
      <DialogTitle id="key-change-title">{action === "rotate" ? `Rotate ${word} API key` : `Revoke ${word} API key`}</DialogTitle>
      <DialogContent>
        {action === "rotate" ? (
          <DialogContentText mb={2}>
            You get a new {word} API key and a new webhook signing secret. The current {word} key stops working straight away,
            so update your server as soon as you have copied them.
            {otherKey && ` Your ${mode === "live" ? "test" : "live"} key keeps working, but it shares the signing secret: update it there too.`}
          </DialogContentText>
        ) : (
          <Alert severity="warning" sx={{ mb: 2 }}>
            This is your only active {word} API key. Once it is revoked your server cannot create or check {word} payments
            until you generate a new {word} key.
          </Alert>
        )}
        <TextField label="Code from your authenticator app" value={code} onChange={(e) => setCode(e.target.value)}
          fullWidth autoFocus inputProps={{ inputMode: "numeric", autoComplete: "one-time-code", maxLength: 7 }} />
        <ErrorAlert error={change.error} />
      </DialogContent>
      <DialogActions>
        <Button onClick={close}>Cancel</Button>
        <Button variant="contained" disabled={!valid || change.isPending} onClick={() => change.mutate()}>
          {action === "rotate" ? "Rotate key" : "Revoke key"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export default function ApiKeys() {
  const client = useQueryClient();
  const own = useQuery({ queryKey: ["own-merchant"], queryFn: () => api<OwnMerchant>("/merchant") });
  const [created, setCreated] = useState<OwnMerchant["credentials"] | null>(null);
  const [webhook, setWebhook] = useState<string | null>(null);
  const [changing, setChanging] = useState<Change | null>(null);
  const [notice, setNotice] = useState("");
  const generate = useMutation({
    mutationFn: (mode: KeyMode) => post<OwnMerchant>("/merchant/api-key", { mode }),
    onSuccess: (m) => { setCreated(m.credentials ?? null); client.setQueryData(["own-merchant"], { ...m, credentials: undefined }); },
  });
  const submitWebhook = useMutation({
    mutationFn: (url: string) => post<OwnMerchant>("/merchant/webhook", { webhook_url: url }),
    onSuccess: (m) => { setWebhook(null); client.setQueryData(["own-merchant"], m); },
  });
  const m = own.data;
  const keys = [m?.api_key, m?.live_api_key].filter((k): k is OwnApiKey => !!k);
  const modeOf = (k: OwnApiKey): KeyMode => k.mode ?? (k.hint.startsWith("sp_live_") ? "live" : "test");

  return (
    <>
      <PageHeader title="API keys" subtitle="Keys let your server create payments and check their status."
        action={(m?.can_generate_key || m?.can_generate_live_key) && (
          <Stack direction="row" gap={1} flexWrap="wrap">
            {m.can_generate_key && (
              <Button variant="contained" size="large" onClick={() => generate.mutate("test")} disabled={generate.isPending}>
                Generate test key
              </Button>
            )}
            {m.can_generate_live_key && (
              <Button variant={m.can_generate_key ? "outlined" : "contained"} size="large" onClick={() => generate.mutate("live")}
                disabled={generate.isPending}>
                Generate live key
              </Button>
            )}
          </Stack>
        )} />
      <ErrorAlert error={own.error ?? generate.error} />
      {notice && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setNotice("")}>{notice}</Alert>}
      {m && m.status === "pending" && (
        <Typography variant="body2" color="text.secondary" mb={2}>
          You can generate an API key once an administrator approves {m.name}; we will email you. You can already
          submit your webhook address.
        </Typography>
      )}
      {m && (m.status === "rejected" || m.status === "suspended") && (
        <Alert severity="warning" sx={{ mb: 2 }}>{m.name} is {m.status}. No API key can be generated.</Alert>
      )}
      {created && (
        <Paper sx={{ p: 2.5, borderRadius: 2, mb: 2, border: "2px solid #333" }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}>
            <Typography variant="h6" component="h2">New key created: copy the secret now</Typography>
            <Badge label="Shown once" />
          </Stack>
          <Typography variant="body2" color="text.secondary" mb={2}>
            {created.signing_secret
              ? "The signing secret will not be shown again. Store both in your merchant server's configuration."
              : "The key will not be shown again. Your webhook signing secret stays the same: both keys use it."}
          </Typography>
          <Secret label={created.api_key.startsWith("sp_live_") ? "Live API key" : "Test API key"} value={created.api_key} testId="new-api-key" />
          {created.signing_secret && (
            <Secret label="Webhook signing secret" value={created.signing_secret} testId="new-signing-secret" />
          )}
          <Button variant="contained" onClick={() => setCreated(null)}>{created.signing_secret ? "I have stored them" : "I have stored it"}</Button>
        </Paper>
      )}
      <TableContainer component={Paper} sx={{ borderRadius: 2, mb: 2 }}>
        <Table aria-label="API keys">
          <TableHead>
            <TableRow><TableCell>Key</TableCell><TableCell>Mode</TableCell><TableCell>Created</TableCell><TableCell>Last used</TableCell><TableCell>Status</TableCell><TableCell><SrOnly>Actions</SrOnly></TableCell></TableRow>
          </TableHead>
          <TableBody>
            {keys.length > 0 ? keys.map((k) => {
              const mode = modeOf(k);
              return (
                <TableRow key={k.id}>
                  <TableCell sx={{ fontFamily: "monospace" }}>{k.hint}</TableCell>
                  <TableCell>{MODE_LABEL[mode]}</TableCell>
                  <TableCell>{longDate(k.created_at)}</TableCell>
                  <TableCell>{k.last_used_at ? longDate(k.last_used_at) : "Never"}</TableCell>
                  <TableCell><Badge label="Active" tone="dark" /></TableCell>
                  <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
                    {m?.status === "active" && (
                      <Button size="small" variant="outlined" onClick={() => setChanging({ action: "rotate", mode })} sx={{ mr: 1 }}
                        aria-label={`Rotate ${mode} key`}>Rotate</Button>
                    )}
                    <Button size="small" onClick={() => setChanging({ action: "revoke", mode })} aria-label={`Revoke ${mode} key`}>Revoke</Button>
                  </TableCell>
                </TableRow>
              );
            }) : (
              <TableRow><TableCell colSpan={6}><Typography color="text.secondary" variant="body2">No active API key.</Typography></TableCell></TableRow>
            )}
            {m?.revoked_keys?.map((k) => (
              <TableRow key={k.id}>
                <TableCell sx={{ fontFamily: "monospace", color: "text.secondary" }}>{k.hint}</TableCell>
                <TableCell>{MODE_LABEL[k.mode ?? (k.hint.startsWith("sp_live_") ? "live" : "test")]}</TableCell>
                <TableCell>{longDate(k.created_at)}</TableCell>
                <TableCell>Revoked {longDate(k.revoked_at)}</TableCell>
                <TableCell><Badge label="Revoked" /></TableCell>
                <TableCell />
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
      <KeyChange change={changing} otherKey={keys.length > 1} onClose={() => setChanging(null)} onDone={(next) => {
        if (changing?.action === "rotate") setCreated(next.credentials ?? null);
        else setNotice(`The ${changing?.mode ?? "test"} API key is revoked. Generate a new key when you are ready.`);
        setChanging(null);
        client.setQueryData(["own-merchant"], { ...next, credentials: undefined });
      }} />
      <Section title="Webhook address">
        <Typography variant="body2" color="text.secondary" mb={2}>
          We send payment notifications here, signed with your webhook signing secret. An administrator approves each new
          address before anything is sent to it.
        </Typography>
        {m && webhook === null && (
          <Stack direction={{ xs: "column", sm: "row" }} gap={2} alignItems={{ sm: "center" }}>
            <Typography sx={{ wordBreak: "break-all" }} data-testid="webhook-url">{m.webhook_url ?? "None yet"}</Typography>
            {m.webhook_url && <Badge label={m.webhook_url_approved ? "Approved" : "Awaiting approval"} tone={m.webhook_url_approved ? "dark" : "neutral"} />}
            {m.status !== "rejected" && m.status !== "suspended" && (
              <Button variant="outlined" onClick={() => setWebhook(m.webhook_url ?? "https://")} sx={{ ml: { sm: "auto" } }}>
                {m.webhook_url ? "Change" : "Add address"}
              </Button>
            )}
          </Stack>
        )}
        {webhook !== null && (
          <Stack direction={{ xs: "column", sm: "row" }} gap={2}>
            <TextField label="Webhook URL (https)" value={webhook} onChange={(e) => setWebhook(e.target.value)} fullWidth autoFocus />
            <Button variant="contained" onClick={() => submitWebhook.mutate(webhook)} disabled={submitWebhook.isPending}>Submit for approval</Button>
            <Button onClick={() => { setWebhook(null); submitWebhook.reset(); }}>Cancel</Button>
          </Stack>
        )}
        <ErrorAlert error={submitWebhook.error} />
      </Section>
      <Typography variant="caption" color="text.secondary" display="block" mt={2}>
        Keys are stored as a digest and secrets encrypted. Every key generated, rotated or revoked is recorded in the audit log. No key is issued
        before the merchant is approved. {m && `Merchant status: ${titleCase(m.status)}.`}
      </Typography>
    </>
  );
}
