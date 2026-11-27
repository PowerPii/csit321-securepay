// One SPA with role-based routes for merchant operators, administrators and analysts.
// The backend stays the authority on permissions; routes here only hide what a role can't use.
import { CssBaseline, ThemeProvider } from "@mui/material";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { ApiError } from "./api/client";
import { RequireStaff, RoleGate } from "./auth";
import Layout from "./components/Layout";
import {
  Accounts, ApiKeys, AuditLog, Blocklist, FraudRules, HistorySearchPage, LimitsPage, CaseDetail, ForgotPassword, FraudAlerts, FraudInsights, FraudReports, Merchants, Overview, PaymentDetail, PaymentsList, Register, ResetPassword,
  BalancePage, IntegrityPage, ReviewQueue, SecurityAlerts, SignIn, Totp, TwoStep, VerifyEmail, WebhookLog,
} from "./routes";
import { theme } from "./theme";

function makeQueryClient(): QueryClient {
  const client = new QueryClient({
    defaultOptions: {
      queries: {
        // Client errors are final; only server errors are retried.
        retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
      },
    },
  });
  // A 401 anywhere means the session ended (15 idle minutes): check /me, which sends the
  // user back to D1.
  client.getQueryCache().subscribe((event) => {
    const error = event.query.state.error;
    if (event.type === "updated" && error instanceof ApiError && error.status === 401
        && event.query.queryKey[0] !== "me") {
      client.invalidateQueries({ queryKey: ["me"] });
    }
  });
  return client;
}

export default function App() {
  const [queryClient] = useState(makeQueryClient);
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <QueryClientProvider client={queryClient}>
        <BrowserRouter basename={import.meta.env.BASE_URL.replace(/\/$/, "")}>
          <Routes>
            <Route path="/login" element={<SignIn />} />
            <Route path="/login/totp" element={<Totp />} />
            <Route path="/forgot-password" element={<ForgotPassword />} />
            <Route path="/reset-password" element={<ResetPassword />} />
            <Route path="/register" element={<Register />} />
            <Route path="/register/verify" element={<VerifyEmail />} />
            <Route element={<RequireStaff><Layout /></RequireStaff>}>
              <Route path="/overview" element={<Overview />} />
              <Route path="/two-step" element={<TwoStep />} />
              <Route path="/payments" element={<PaymentsList />} />
              <Route path="/payments/:paymentId" element={<PaymentDetail />} />
              <Route path="/payments/:paymentId/refund" element={<PaymentDetail refund />} />
              <Route path="/reviews" element={<RoleGate page="reviews"><ReviewQueue /></RoleGate>} />
              <Route path="/reviews/:caseId" element={<RoleGate page="reviews"><CaseDetail /></RoleGate>} />
              <Route path="/alerts" element={<RoleGate page="alerts"><FraudAlerts /></RoleGate>} />
              <Route path="/insights" element={<RoleGate page="insights"><FraudInsights /></RoleGate>} />
              <Route path="/reports" element={<RoleGate page="reports"><FraudReports /></RoleGate>} />
              <Route path="/search" element={<RoleGate page="search"><HistorySearchPage /></RoleGate>} />
              <Route path="/blocklist" element={<RoleGate page="blocklist"><Blocklist /></RoleGate>} />
              <Route path="/rules" element={<RoleGate page="rules"><FraudRules /></RoleGate>} />
              <Route path="/merchants" element={<RoleGate page="merchants"><Merchants /></RoleGate>} />
              <Route path="/api-keys" element={<RoleGate page="keys"><ApiKeys /></RoleGate>} />
              <Route path="/webhooks" element={<RoleGate page="webhooks"><WebhookLog /></RoleGate>} />
              <Route path="/balance" element={<RoleGate page="balance"><BalancePage /></RoleGate>} />
              <Route path="/audit" element={<RoleGate page="audit"><AuditLog /></RoleGate>} />
              <Route path="/accounts" element={<RoleGate page="accounts"><Accounts /></RoleGate>} />
              <Route path="/limits" element={<RoleGate page="limits"><LimitsPage /></RoleGate>} />
              <Route path="/security" element={<RoleGate page="security"><SecurityAlerts /></RoleGate>} />
              <Route path="/integrity" element={<RoleGate page="integrity"><IntegrityPage /></RoleGate>} />
            </Route>
            <Route path="*" element={<Navigate to="/overview" replace />} />
          </Routes>
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
