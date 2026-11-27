// Dashboard shell after the wireframes: grey sidebar with role-based navigation and the
// signed-in user at the bottom, a top bar with payment search and the merchant scope.
// Below the md breakpoint the sidebar becomes a drawer behind a menu button.
import {
  Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle, Drawer, FormControlLabel, IconButton, InputBase, List, ListItemButton, ListItemText, Paper, Stack, Switch, Typography,
  useMediaQuery, useTheme,
} from "@mui/material";
import { Fragment, useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { CAN, useSignOut, useStaff } from "../auth";
import { minSec, useIdleSignOut } from "../idle";
import { ModeProvider, useMode } from "../mode";
import { SrOnly, visuallyHidden } from "./ui";
import { Wordmark } from "./Wordmark";

const SIDEBAR = 232;

// Grouped so a long menu scans in three short lists (rebrand, 19 Nov 2026).
const NAV: { to: string; label: string; page: keyof typeof CAN; group: "Payments" | "Fraud" | "Admin" }[] = [
  { to: "/overview", label: "Overview", page: "payments", group: "Payments" },
  { to: "/payments", label: "Payments", page: "payments", group: "Payments" },
  { to: "/balance", label: "Balance", page: "balance", group: "Payments" },
  { to: "/search", label: "History search", page: "search", group: "Payments" },
  { to: "/merchants", label: "Merchants", page: "merchants", group: "Payments" },
  { to: "/api-keys", label: "API keys", page: "keys", group: "Payments" },
  { to: "/webhooks", label: "Webhooks", page: "webhooks", group: "Payments" },
  { to: "/reviews", label: "Fraud review", page: "reviews", group: "Fraud" },
  { to: "/alerts", label: "Fraud alerts", page: "alerts", group: "Fraud" },
  { to: "/insights", label: "Fraud insights", page: "insights", group: "Fraud" },
  { to: "/reports", label: "Fraud reports", page: "reports", group: "Fraud" },
  { to: "/blocklist", label: "Block list", page: "blocklist", group: "Fraud" },
  { to: "/rules", label: "Fraud rules", page: "rules", group: "Fraud" },
  { to: "/accounts", label: "Accounts", page: "accounts", group: "Admin" },
  { to: "/limits", label: "Limits", page: "limits", group: "Admin" },
  { to: "/audit", label: "Audit log", page: "audit", group: "Admin" },
  { to: "/security", label: "Security alerts", page: "security", group: "Admin" },
  { to: "/integrity", label: "Integrity and keys", page: "integrity", group: "Admin" },
];

/** NFR-PER-03: a new fraud alert shows on the dashboard within 10 seconds (polled every 5). */
function useUnreadAlerts(enabled: boolean): number {
  const unread = useQuery({
    queryKey: ["alerts-unread"],
    queryFn: () => api<{ unread: number; latest_at: string | null }>("/alerts/unread"),
    enabled,
    refetchInterval: 5_000,
    refetchIntervalInBackground: true,
  });
  return unread.data?.unread ?? 0;
}

/** FR-ADM-08 / NFR-PER-03: open security alerts for administrators, polled every 5 s. */
function useOpenSecurityAlerts(enabled: boolean): number {
  const open = useQuery({
    queryKey: ["security-open"],
    queryFn: () => api<{ open: number; latest_at: string | null }>("/security/alerts/open-count"),
    enabled,
    refetchInterval: 5_000,
    refetchIntervalInBackground: true,
  });
  return open.data?.open ?? 0;
}

function Count({ n }: { n: number }) {
  return (
    <Box component="span" aria-hidden="true"
      sx={{ ml: 1, px: 0.9, minWidth: 22, height: 22, borderRadius: 11, bgcolor: "#C2333B", color: "#fff",
        fontSize: 12, fontWeight: 700, display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
      {n > 99 ? "99+" : n}
    </Box>
  );
}

/** v2: which mode's payments, balance and payouts the pages show (top bar, so it is there on
 *  phones too). Both modes are simulated. */
function ModeSwitch() {
  const { mode, setMode } = useMode();
  const live = mode === "live";
  return (
    <FormControlLabel
      sx={{ m: 0, gap: 0.5, flexShrink: 0, whiteSpace: "nowrap" }}
      title="Live and test keep separate payments, balances and keys. Both are simulated: no real money moves."
      control={<Switch size="small" checked={live} inputProps={{ role: "switch", "aria-label": "Live mode" }} onChange={(e) => setMode(e.target.checked ? "live" : "test")} />}
      label={<Typography variant="body2" fontWeight={600} aria-hidden="true"
        sx={{ display: { xs: "none", sm: "block" } }}>{live ? "Live mode" : "Test mode"}</Typography>}
    />
  );
}

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const staff = useStaff();
  const location = useLocation();
  const signOut = useSignOut();
  const unread = useUnreadAlerts(CAN.alerts.includes(staff.role));
  const security = useOpenSecurityAlerts(CAN.security.includes(staff.role));
  return (
    <Stack component="aside" aria-label="Sidebar" sx={{ width: SIDEBAR, height: "100%", bgcolor: "#ECEFF4", p: 2.5, boxSizing: "border-box" }}>
      <Wordmark />
      <Typography variant="body2" color="text.secondary" mt={0.5} mb={3}>Dashboard</Typography>
      <List component="nav" aria-label="Main" disablePadding sx={{ flexGrow: 1 }}>
        {NAV.filter((n) => CAN[n.page].includes(staff.role)).map((n, i, shown) => {
          const active = location.pathname.startsWith(n.to);
          const groupStart = i === 0 || shown[i - 1].group !== n.group;
          return (
            <Fragment key={n.to}>
            {groupStart && (
              <Typography component="p" variant="caption"
                sx={{ display: "block", color: "text.secondary", fontWeight: 600, fontSize: 12.5, px: 2, mt: i === 0 ? 0 : 2, mb: 0.5 }}>
                {n.group}
              </Typography>
            )}
            <ListItemButton
              component={Link}
              to={n.to}
              onClick={onNavigate}
              selected={active}
              sx={{ borderRadius: 1.5, mb: 0.25, py: 0.6, "&.Mui-selected": { bgcolor: "#DCE2EA", fontWeight: 600 } }}
            >
              <ListItemText primary={n.label} primaryTypographyProps={{ fontWeight: active ? 600 : 400 }} />
              {n.page === "alerts" && unread > 0 && <><Count n={unread} /><SrOnly>, {unread} unread</SrOnly></>}
              {n.page === "security" && security > 0 && <><Count n={security} /><SrOnly>, {security} open</SrOnly></>}
            </ListItemButton>
            </Fragment>
          );
        })}
      </List>
      <Paper sx={{ p: 1.5, bgcolor: "transparent" }}>
        <Typography fontWeight={600} variant="body2" noWrap title={staff.email}>{staff.name}</Typography>
        <Typography variant="body2" color="text.secondary">{staff.role_label}</Typography>
        <Stack alignItems="flex-start" mt={0.5}>
          <Button size="small" component={Link} to="/two-step" onClick={onNavigate} sx={{ px: 0, minWidth: 0 }}>
            Two-step verification
          </Button>
          <Button size="small" onClick={() => signOut()} sx={{ px: 0, minWidth: 0 }}>Sign out</Button>
        </Stack>
      </Paper>
    </Stack>
  );
}

function SearchBox() {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    navigate(`/payments?q=${encodeURIComponent(q.trim())}`);
  };
  return (
    <Paper component="form" onSubmit={submit} role="search" sx={{ px: 1.5, py: 0.5, width: { xs: "100%", sm: 360 } }}>
      <InputBase
        fullWidth
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search payment reference, order or email"
        inputProps={{ "aria-label": "Search payments" }}
        sx={{ fontSize: 14 }}
      />
    </Paper>
  );
}

/** Top-bar link to new fraud alerts, so they show on every screen width (the sidebar is a
 *  drawer on phones). */
function AlertsLink() {
  const staff = useStaff();
  const unread = useUnreadAlerts(CAN.alerts.includes(staff.role));
  if (!unread) return null;
  return (
    <Button component={Link} to="/alerts" size="small" variant="contained"
      aria-label={`${unread} unread fraud alert${unread === 1 ? "" : "s"}`} sx={{ whiteSpace: "nowrap", flexShrink: 0 }}>
      {unread > 99 ? "99+" : unread} new alert{unread === 1 ? "" : "s"}
    </Button>
  );
}

/** Top-bar link to open security alerts (administrators). */
function SecurityLink() {
  const staff = useStaff();
  const open = useOpenSecurityAlerts(CAN.security.includes(staff.role));
  if (!open) return null;
  return (
    <Button component={Link} to="/security" size="small" variant="outlined"
      aria-label={`${open} open security alert${open === 1 ? "" : "s"}`} sx={{ whiteSpace: "nowrap", flexShrink: 0 }}>
      {open > 99 ? "99+" : open} security alert{open === 1 ? "" : "s"}
    </Button>
  );
}

/** Says new alerts out loud for screen-reader users: the top-bar buttons appear without focus
 *  moving to them, so a polite live region that is always on the page announces the counts. */
function AlertAnnouncer() {
  const staff = useStaff();
  const unread = useUnreadAlerts(CAN.alerts.includes(staff.role));
  const open = useOpenSecurityAlerts(CAN.security.includes(staff.role));
  const parts = [
    unread ? `${unread} unread fraud alert${unread === 1 ? "" : "s"}` : "",
    open ? `${open} open security alert${open === 1 ? "" : "s"}` : "",
  ].filter(Boolean);
  return <Box role="status" sx={visuallyHidden}>{parts.join(". ")}</Box>;
}

/** FR-ACC-10: warn at 13 idle minutes, sign out at 15 (the server's session limit). */
function IdleWarning() {
  const staff = useStaff();
  const signOut = useSignOut();
  const keepAlive = useCallback(() => { api("/me").catch(() => undefined); }, []);
  const onEnd = useCallback(() => { signOut("idle"); }, [signOut]);
  const { warning, secondsLeft, stay } = useIdleSignOut({
    enabled: true,
    warnMinutes: staff.idle_warning_minutes ?? 13,
    endMinutes: staff.idle_minutes ?? 15,
    keepAlive,
    onEnd,
  });
  return (
    <Dialog open={warning} aria-labelledby="idle-title" aria-describedby="idle-text">
      <DialogTitle id="idle-title">Are you still there?</DialogTitle>
      <DialogContent>
        <DialogContentText id="idle-text">
          You will be signed out in {minSec(secondsLeft)} because of inactivity.
        </DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button onClick={() => signOut()}>Sign out</Button>
        <Button variant="contained" onClick={stay} autoFocus>Stay signed in</Button>
      </DialogActions>
    </Dialog>
  );
}

export default function Layout() {
  return <ModeProvider><Shell /></ModeProvider>;
}

function Shell() {
  const staff = useStaff();
  const location = useLocation();
  const theme = useTheme();
  const wide = useMediaQuery(theme.breakpoints.up("md"));
  const [open, setOpen] = useState(false);
  const scope = staff.merchant ? `Merchant: ${staff.merchant.name}` : "All merchants (read-only)";
  // After moving to another page, put keyboard and screen-reader focus at the start of the new
  // page's content, as a full page load would (not on the first page shown).
  const mainRef = useRef<HTMLElement>(null);
  const firstPath = useRef(location.pathname);
  useEffect(() => {
    if (location.pathname === firstPath.current) return;
    firstPath.current = "";
    mainRef.current?.focus({ preventScroll: false });
  }, [location.pathname]);
  return (
    <Box sx={{ display: "flex", minHeight: "100vh", bgcolor: "#fff" }}>
      <Box component="a" href="#main"
        onClick={(e: { preventDefault: () => void }) => { e.preventDefault(); mainRef.current?.focus(); }}
        sx={{ ...visuallyHidden, "&:focus": { clip: "auto", width: "auto", height: "auto", margin: 0, zIndex: 2000,
          top: 8, left: 8, px: 2, py: 1, bgcolor: "#13243F", color: "#fff", borderRadius: 1, fontWeight: 600 } }}>
        Skip to content
      </Box>
      {wide ? (
        <Box sx={{ width: SIDEBAR, flexShrink: 0, bgcolor: "#ECEFF4" }}>
          <Box sx={{ position: "sticky", top: 0, height: "100vh" }}>
            <Sidebar />
          </Box>
        </Box>
      ) : (
        <Drawer open={open} onClose={() => setOpen(false)}>
          <Sidebar onNavigate={() => setOpen(false)} />
        </Drawer>
      )}
      <Box sx={{ flexGrow: 1, minWidth: 0 }}>
        <Stack
          component="header"
          direction="row"
          alignItems="center"
          gap={2}
          sx={{ px: { xs: 2, md: 3.5 }, py: 1.5, borderBottom: 1, borderColor: "divider" }}
        >
          {!wide && (
            <IconButton aria-label="Open menu" onClick={() => setOpen(true)} edge="start">
              <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M3 6h18M3 12h18M3 18h18" stroke="#13243F" strokeWidth="2" strokeLinecap="round" />
              </svg>
            </IconButton>
          )}
          <SearchBox />
          <AlertsLink />
          <SecurityLink />
          <Typography variant="body2" sx={{ ml: { xs: 0, sm: "auto" }, display: { xs: "none", sm: "block" } }}>{scope}</Typography>
          <ModeSwitch />
        </Stack>
        <Box component="main" id="main" tabIndex={-1} ref={mainRef}
          sx={{ px: { xs: 2, md: 3.5 }, py: 3, maxWidth: 1240, "&:focus": { outline: "none" } }}>
          {staff.merchant?.status === "pending" && (
            <Alert severity="info" sx={{ mb: 2 }}>
              {staff.merchant.name} is waiting for administrator approval. No API key is issued until then.
            </Alert>
          )}
          {(staff.backup_codes_left ?? 10) <= 2 && !location.pathname.startsWith("/two-step") && (
            <Alert severity="warning" sx={{ mb: 2 }}
              action={<Button color="inherit" size="small" component={Link} to="/two-step">Make codes</Button>}>
              {staff.backup_codes_left === 0
                ? "You have no backup codes for two-step verification."
                : `Only ${staff.backup_codes_left} backup code${staff.backup_codes_left === 1 ? "" : "s"} left.`}{" "}
              Make new ones so you can still sign in if you lose your phone.
            </Alert>
          )}
          <Outlet />
        </Box>
        <IdleWarning />
        <AlertAnnouncer />
      </Box>
    </Box>
  );
}
