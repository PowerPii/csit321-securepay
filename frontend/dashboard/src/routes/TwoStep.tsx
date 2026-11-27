// Two-step verification for the signed-in staff member: how many one-time backup codes are
// left, and a new set (confirmed with the current authenticator code; the old set stops working).
import { Alert, Button, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle, Stack, TextField, Typography } from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, post } from "../api/client";
import { useStaff } from "../auth";
import { BackupCodeList } from "../components/BackupCodes";
import { Passkeys } from "../components/Passkeys";
import { ErrorAlert, PageHeader, Row, Section } from "../components/ui";
import { longDate } from "../format";

interface BackupStatus { remaining: number; total: number; generated_at: string | null; codes?: string[] }

export function TwoStep() {
  const staff = useStaff();
  const client = useQueryClient();
  const status = useQuery({ queryKey: ["backup-codes"], queryFn: () => api<BackupStatus>("/me/backup-codes") });
  const [asking, setAsking] = useState(false);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const make = useMutation({
    mutationFn: () => post<BackupStatus>("/me/backup-codes", { code: code.replace(/\s/g, "") }),
    onSuccess: (reply) => {
      setCodes(reply.codes ?? null);
      setAsking(false);
      setCode("");
      client.setQueryData(["backup-codes"], { ...reply, codes: undefined });
      client.invalidateQueries({ queryKey: ["me"] });
    },
  });
  const close = () => { setAsking(false); setCode(""); make.reset(); };
  const left = status.data?.remaining;

  return (
    <>
      <PageHeader title="Two-step verification"
        subtitle="Your authenticator app, your passkeys, and the one-time backup codes that stand in for the app if you lose your phone." />
      <Stack gap={2.5} maxWidth={640}>
        {codes && (
          <Section title="Your new backup codes">
            <Alert severity="success" sx={{ mb: 2 }}>New codes made. Your earlier codes no longer work.</Alert>
            <BackupCodeList codes={codes} email={staff.email} />
          </Section>
        )}
        <Section title="Backup codes"
          action={<Button variant="contained" onClick={() => setAsking(true)}>Make new codes</Button>}>
          <ErrorAlert error={status.error} />
          {status.data && (
            <>
              <Row label="Codes left" value={`${left} of ${status.data.total}`} />
              <Row label="Made" value={status.data.generated_at ? longDate(status.data.generated_at) : "Never"} />
              {left !== undefined && left <= 2 && (
                <Alert severity="warning" sx={{ mt: 1.5 }}>
                  {left === 0 ? "You have no backup codes." : `Only ${left} backup code${left === 1 ? "" : "s"} left.`} Make new
                  ones so you can still sign in if you lose your phone.
                </Alert>
              )}
            </>
          )}
        </Section>
        <Passkeys />
        <Section title="Authenticator app">
          <Typography variant="body2" color="text.secondary">
            Set up at your first sign-in. If you get a new phone, ask the SecurePay server operator to reset it; you set it up again
            at your next sign-in.
          </Typography>
        </Section>
      </Stack>
      <Dialog open={asking} onClose={close} fullWidth maxWidth="xs" aria-labelledby="new-codes-title">
        <DialogTitle id="new-codes-title">Make new backup codes</DialogTitle>
        <DialogContent>
          <DialogContentText mb={2}>
            You get {status.data?.total ?? 10} new codes, shown once. Every earlier code stops working.
          </DialogContentText>
          <TextField label="Code from your authenticator app" value={code} onChange={(e) => setCode(e.target.value)}
            fullWidth autoFocus inputProps={{ inputMode: "numeric", autoComplete: "one-time-code", maxLength: 7 }} />
          <ErrorAlert error={make.error} />
        </DialogContent>
        <DialogActions>
          <Button onClick={close}>Cancel</Button>
          <Button variant="contained" disabled={!/^\d{6}$/.test(code.replace(/\s/g, "")) || make.isPending}
            onClick={() => make.mutate()}>Make new codes</Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
