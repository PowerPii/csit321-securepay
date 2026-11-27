// v2: the signed-in staff member's passkeys, which can stand in for the authenticator code
// at sign-in (the password is still asked first). Adding one needs the current code.
import { Alert, Button, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle, Stack, TextField, Typography } from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, post } from "../api/client";
import { longDate } from "../format";
import { createPasskey, PasskeyCancelled, passkeysSupported } from "../passkeys";
import { ErrorAlert, Section } from "./ui";

interface Passkey { id: string; name: string; synced: boolean; created_at: string; last_used_at: string | null }
interface PasskeyList { data: Passkey[]; limit: number }

export function Passkeys() {
  const client = useQueryClient();
  const list = useQuery({ queryKey: ["passkeys"], queryFn: () => api<PasskeyList>("/me/passkeys") });
  const [asking, setAsking] = useState(false);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [cancelled, setCancelled] = useState(false);
  const refresh = () => client.invalidateQueries({ queryKey: ["passkeys"] });
  const add = useMutation({
    mutationFn: async () => {
      const { options } = await post<{ options: Record<string, unknown> }>("/me/passkeys/register/options");
      const credential = await createPasskey(options);
      return post<Passkey>("/me/passkeys/register", { credential, name: name.trim() || undefined, code: code.replace(/\s/g, "") });
    },
    onSuccess: () => { close(); refresh(); },
    onError: (err) => setCancelled(err instanceof PasskeyCancelled),
  });
  const remove = useMutation({
    mutationFn: (id: string) => post<void>(`/me/passkeys/${encodeURIComponent(id)}/remove`),
    onSuccess: refresh,
  });
  function close() { setAsking(false); setName(""); setCode(""); setCancelled(false); add.reset(); }
  const rows = list.data?.data ?? [];
  const full = list.data ? rows.length >= list.data.limit : false;

  return (
    <>
      <Section title="Passkeys"
        action={passkeysSupported() && !full ? <Button variant="contained" onClick={() => setAsking(true)}>Add a passkey</Button> : undefined}>
        <Typography variant="body2" color="text.secondary" mb={1.5}>
          After your password, use your fingerprint, face or device PIN instead of the code from your app.
          The app and your backup codes keep working.
        </Typography>
        <ErrorAlert error={list.error ?? remove.error} />
        {list.data && rows.length === 0 && <Typography variant="body2">You have no passkeys yet.</Typography>}
        <Stack gap={1}>
          {rows.map((p) => (
            <Stack key={p.id} direction="row" justifyContent="space-between" alignItems="center" gap={1}
              sx={{ border: 1, borderColor: "divider", borderRadius: 1.5, px: 1.5, py: 1 }}>
              <div>
                <Typography variant="body2" fontWeight={600}>{p.name}</Typography>
                <Typography variant="caption" color="text.secondary">
                  Added {longDate(p.created_at)} · {p.last_used_at ? `last used ${longDate(p.last_used_at)}` : "not used yet"}
                  {p.synced ? " · synced" : ""}
                </Typography>
              </div>
              <Button size="small" color="error" disabled={remove.isPending} onClick={() => remove.mutate(p.id)}
                aria-label={`Remove ${p.name}`}>Remove</Button>
            </Stack>
          ))}
        </Stack>
        {!passkeysSupported() && (
          <Typography variant="body2" color="text.secondary" mt={1}>This browser can't make passkeys.</Typography>
        )}
      </Section>
      <Dialog open={asking} onClose={close} fullWidth maxWidth="xs" aria-labelledby="add-passkey-title">
        <DialogTitle id="add-passkey-title">Add a passkey</DialogTitle>
        <DialogContent>
          <DialogContentText mb={2}>
            Enter the current code from your authenticator app, then follow your browser's prompt.
          </DialogContentText>
          <Stack gap={2}>
            <TextField label="Name (optional)" value={name} onChange={(e) => setName(e.target.value)}
              placeholder="Work laptop" inputProps={{ maxLength: 60 }} fullWidth />
            <TextField label="Code from your authenticator app" value={code} onChange={(e) => setCode(e.target.value)}
              fullWidth autoFocus inputProps={{ inputMode: "numeric", autoComplete: "one-time-code", maxLength: 7 }} />
          </Stack>
          {cancelled ? <Alert severity="info" sx={{ mt: 2 }}>No passkey was added.</Alert> : <ErrorAlert error={add.error} />}
        </DialogContent>
        <DialogActions>
          <Button onClick={close}>Cancel</Button>
          <Button variant="contained" disabled={!/^\d{6}$/.test(code.replace(/\s/g, "")) || add.isPending}
            onClick={() => add.mutate()}>Add passkey</Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
