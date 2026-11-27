// D1 Staff sign-in and D2 Two-step verification (with first-time authenticator setup, the
// one-time backup codes shown after it, and signing in with a backup code instead).
import {
  Alert, Box, Button, Checkbox, FormControlLabel, Link as MuiLink, Paper, Stack, TextField, Typography,
} from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link as RouterLink, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError, post, type Staff } from "../api/client";
import { BackupCodeList } from "../components/BackupCodes";
import { ShieldIcon } from "../components/ui";
import { Wordmark } from "../components/Wordmark";
import { PasskeyCancelled, passkeysSupported, signWithPasskey } from "../passkeys";

interface Setup { secret: string; otpauth_uri: string; qr_svg: string }
interface LoginReply { next: "totp"; email: string; setup: Setup | null; passkey?: boolean }

export function Card({ children, maxWidth = 420 }: { children: React.ReactNode; maxWidth?: number }) {
  return (
    <Stack component="main" alignItems="center" justifyContent="center" sx={{ minHeight: "100vh", bgcolor: "background.default", p: 2 }}>
      <Paper sx={{ p: { xs: 3, sm: 4 }, width: "100%", maxWidth, borderRadius: 2 }}>{children}</Paper>
    </Stack>
  );
}

export function message(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Try again.";
}

export function SignIn() {
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // Sets the CSRF cookie before the first POST (the 401 is expected).
  useEffect(() => {
    api("/me").then(() => navigate("/overview", { replace: true })).catch(() => undefined);
  }, [navigate]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const reply = await post<LoginReply>("/auth/login", { email, password });
      navigate("/login/totp", { state: { email: reply.email, setup: reply.setup, passkey: reply.passkey, from: location.state?.from } });
    } catch (err) {
      setError(message(err));
      setPassword("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <Wordmark />
      <Typography variant="h5" component="h1" mt={2}>Sign in to the dashboard</Typography>
      <Typography color="text.secondary" variant="body2" mt={1} mb={3}>
        For merchant operators, administrators and fraud analysts.
      </Typography>
      {new URLSearchParams(location.search).get("ended") === "idle" && (
        <Alert severity="info" sx={{ mb: 2 }} role="status">
          You were signed out after 15 minutes without activity. Sign in again to continue.
        </Alert>
      )}
      <Box component="form" onSubmit={submit} noValidate>
        <Stack gap={2}>
          <TextField label="Email" type="email" autoComplete="username" value={email} required
            onChange={(e) => setEmail(e.target.value)} autoFocus size="medium" />
          <TextField label="Password" type="password" autoComplete="current-password" value={password} required
            onChange={(e) => setPassword(e.target.value)} size="medium" />
          <Button type="submit" variant="contained" size="large" disabled={busy || !email || !password}>
            Sign in
          </Button>
          {error && <Alert severity="error" role="alert">{error}</Alert>}
          <MuiLink component={RouterLink} to="/forgot-password" variant="body2">Forgot your password?</MuiLink>
          <Typography variant="caption" color="text.secondary">
            After 5 failed attempts the account is locked for 15 minutes.
          </Typography>
          <Typography variant="body2" color="text.secondary" textAlign="center">
            New merchant? <MuiLink component={RouterLink} to="/register">Register your business</MuiLink>
          </Typography>
        </Stack>
      </Box>
    </Card>
  );
}

/** Six boxes that behave like one input (paste fills all of them). */
export function CodeInput({ value, onChange, autoFocus = true }: { value: string; onChange: (v: string) => void; autoFocus?: boolean }) {
  const refs = useRef<(HTMLInputElement | null)[]>([]);
  const set = (i: number, text: string) => {
    const digits = text.replace(/\D/g, "");
    if (digits.length > 1) {
      onChange(digits.slice(0, 6));
      refs.current[Math.min(digits.length, 5)]?.focus();
      return;
    }
    const chars = value.padEnd(6, " ").split("");
    chars[i] = digits || " ";
    onChange(chars.join("").trimEnd());
    if (digits && i < 5) refs.current[i + 1]?.focus();
  };
  return (
    <Stack direction="row" gap={1} justifyContent="center" my={2}>
      {Array.from({ length: 6 }, (_, i) => (
        <Box
          key={i}
          component="input"
          ref={(el: HTMLInputElement | null) => { refs.current[i] = el; }}
          inputMode="numeric"
          autoComplete={i === 0 ? "one-time-code" : "off"}
          aria-label={`Digit ${i + 1}`}
          value={value[i]?.trim() ?? ""}
          autoFocus={autoFocus && i === 0}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => set(i, e.target.value)}
          onKeyDown={(e: React.KeyboardEvent<HTMLInputElement>) => {
            if (e.key === "Backspace" && !value[i]?.trim() && i > 0) refs.current[i - 1]?.focus();
          }}
          sx={{
            width: 46, height: 54, textAlign: "center", fontSize: 22, border: "1px solid #8a8a8a",
            borderRadius: 1.5, outlineColor: "#13243F",
          }}
        />
      ))}
    </Stack>
  );
}

export function Totp() {
  const navigate = useNavigate();
  const location = useLocation();
  const client = useQueryClient();
  const state = (location.state ?? {}) as { email?: string; setup?: Setup | null; passkey?: boolean; from?: string };
  const [email, setEmail] = useState(state.email ?? "");
  const [hasPasskey, setHasPasskey] = useState(!!state.passkey);
  const [setup] = useState<Setup | null>(state.setup ?? null);
  const [code, setCode] = useState("");
  const [useBackup, setUseBackup] = useState(false);
  const [backupCode, setBackupCode] = useState("");
  const [newCodes, setNewCodes] = useState<string[] | null>(null);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const destination = state.from && state.from !== "/login" ? state.from : "/overview";

  useEffect(() => {
    if (!state.email) {
      api<{ email: string; passkey?: boolean }>("/auth/totp")
        .then((r) => { setEmail(r.email); setHasPasskey(!!r.passkey); })
        .catch(() => navigate("/login", { replace: true }));
    }
  }, [state.email, navigate]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const staff = useBackup
        ? await post<Staff>("/auth/backup-code", { code: backupCode })
        : await post<Staff>("/auth/totp", { code });
      const { backup_codes: codes, ...me } = staff;
      client.setQueryData(["me"], me);
      if (codes?.length) {
        setNewCodes(codes); // first-time setup: show the backup codes once before going on
      } else {
        navigate(destination, { replace: true });
      }
    } catch (err) {
      setError(message(err));
      setCode("");
      setBackupCode("");
      if (err instanceof ApiError && (err.code === "account_locked" || err.code === "sign_in_required")) {
        navigate("/login", { replace: true, state: {} });
      }
    } finally {
      setBusy(false);
    }
  };

  // v2: a passkey in place of the authenticator code (the password step already passed).
  const withPasskey = async () => {
    setBusy(true);
    setError("");
    try {
      const { options } = await post<{ options: Record<string, unknown> }>("/auth/passkey/options");
      const credential = await signWithPasskey(options);
      const staff = await post<Staff>("/auth/passkey", { credential });
      client.setQueryData(["me"], staff);
      navigate(destination, { replace: true });
    } catch (err) {
      setError(err instanceof PasskeyCancelled ? "No passkey was used. Try again, or enter the code from your app." : message(err));
      if (err instanceof ApiError && (err.code === "account_locked" || err.code === "sign_in_required")) {
        navigate("/login", { replace: true, state: {} });
      }
    } finally {
      setBusy(false);
    }
  };

  const signOut = async () => {
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    navigate("/login", { replace: true });
  };

  if (newCodes) {
    return (
      <Card maxWidth={480}>
        <Stack alignItems="center" textAlign="center" mb={2}>
          <ShieldIcon />
          <Typography variant="h5" component="h1" mt={1.5}>Save your backup codes</Typography>
          <Typography color="text.secondary" variant="body2" mt={1}>
            Your authenticator app is set up. If you lose your phone, sign in with one of these codes instead.
          </Typography>
        </Stack>
        <BackupCodeList codes={newCodes} email={email} />
        <FormControlLabel sx={{ mt: 1.5 }}
          control={<Checkbox checked={saved} onChange={(e) => setSaved(e.target.checked)} />}
          label="I have saved these codes" />
        <Button variant="contained" size="large" fullWidth sx={{ mt: 1 }} disabled={!saved}
          onClick={() => navigate(destination, { replace: true })}>
          Continue to the dashboard
        </Button>
      </Card>
    );
  }

  const switchMode = () => {
    setUseBackup(!useBackup);
    setError("");
    setCode("");
    setBackupCode("");
  };

  return (
    <Card>
      <Stack alignItems="center" textAlign="center">
        <ShieldIcon />
        <Typography variant="h5" component="h1" mt={1.5}>Two-step verification</Typography>
        {useBackup ? (
          <Typography color="text.secondary" variant="body2" mt={1}>
            Enter one of the backup codes you saved when you set up two-step verification. Each code works once.
          </Typography>
        ) : setup ? (
          <>
            <Typography color="text.secondary" variant="body2" mt={1}>
              First sign-in: scan this code with an authenticator app (Google Authenticator, Microsoft
              Authenticator, 1Password...), then enter the 6-digit code it shows.
            </Typography>
            <Box component="img" src={setup.qr_svg} alt="QR code for your authenticator app"
              sx={{ width: 200, height: 200, my: 2 }} />
            <Typography variant="caption" color="text.secondary">Or enter this key by hand:</Typography>
            <Typography variant="body2" sx={{ fontFamily: "monospace", wordBreak: "break-all" }} data-testid="totp-secret">
              {setup.secret}
            </Typography>
          </>
        ) : (
          <Typography color="text.secondary" variant="body2" mt={1}>
            Enter the 6-digit code from your authenticator app.
          </Typography>
        )}
      </Stack>
      <Box component="form" onSubmit={submit}>
        {useBackup ? (
          <TextField label="Backup code" value={backupCode} onChange={(e) => setBackupCode(e.target.value)}
            placeholder="xxxxx-xxxxx" fullWidth autoFocus sx={{ my: 2 }}
            inputProps={{ autoComplete: "off", autoCapitalize: "none", spellCheck: false, maxLength: 20,
              style: { fontFamily: "monospace", fontSize: 18 } }} />
        ) : (
          <CodeInput value={code} onChange={setCode} />
        )}
        <Button type="submit" variant="contained" size="large" fullWidth
          disabled={busy || (useBackup ? backupCode.replace(/[\s-]/g, "").length !== 10 : code.length !== 6)}>
          Verify
        </Button>
        {hasPasskey && !setup && !useBackup && passkeysSupported() && (
          <Button variant="outlined" size="large" fullWidth sx={{ mt: 1.5 }} disabled={busy} onClick={withPasskey}>
            Use your passkey instead
          </Button>
        )}
        {error && <Alert severity="error" role="alert" sx={{ mt: 2 }}>{error}</Alert>}
      </Box>
      <Stack direction="row" justifyContent={setup ? "flex-end" : "space-between"} mt={2} gap={1}>
        {!setup && (
          <MuiLink component="button" type="button" variant="body2" onClick={switchMode}>
            {useBackup ? "Use your authenticator app" : "Lost your phone? Use a backup code"}
          </MuiLink>
        )}
        <MuiLink component="button" variant="body2" color="text.secondary" onClick={signOut}>
          Not you? Sign out
        </MuiLink>
      </Stack>
      {email && (
        <Typography variant="body2" color="text.secondary" textAlign="center" mt={1}>
          Signed in as {email}
        </Typography>
      )}
    </Card>
  );
}
