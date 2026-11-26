// The signed-in staff member. The backend decides what each role may do; this only hides
// pages a role cannot use and sends signed-out visitors to D1.
import { CircularProgress, Stack } from "@mui/material";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { api, ApiError, type Role, type Staff } from "./api/client";

const StaffContext = createContext<Staff | null>(null);

export function useStaff(): Staff {
  const staff = useContext(StaffContext);
  if (!staff) throw new Error("useStaff outside RequireStaff");
  return staff;
}

export function useMe() {
  return useQuery<Staff, ApiError>({
    queryKey: ["me"],
    queryFn: () => api<Staff>("/me"),
    retry: false,
    staleTime: 60_000,
  });
}

export function useSignOut() {
  const client = useQueryClient();
  return async (reason?: "idle") => {
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    client.clear();
    window.location.assign(`${import.meta.env.BASE_URL}login${reason ? `?ended=${reason}` : ""}`);
  };
}

export function RequireStaff({ children }: { children: ReactNode }) {
  const me = useMe();
  const location = useLocation();
  if (me.isPending) {
    return (
      <Stack alignItems="center" justifyContent="center" height="100vh">
        <CircularProgress />
      </Stack>
    );
  }
  if (me.isError || !me.data) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  return <StaffContext.Provider value={me.data}>{children}</StaffContext.Provider>;
}

/** Pages per role (URS 3.3 access control matrix). */
export const CAN: Record<string, Role[]> = {
  payments: ["merchant_operator", "fraud_analyst", "administrator"],
  reviews: ["fraud_analyst", "administrator"],
  alerts: ["fraud_analyst", "administrator"],
  reports: ["fraud_analyst", "administrator"],
  insights: ["fraud_analyst", "administrator"],
  search: ["fraud_analyst", "administrator"],
  blocklist: ["fraud_analyst", "administrator"],
  rules: ["fraud_analyst", "administrator"],
  merchants: ["administrator"],
  audit: ["administrator"],
  security: ["administrator"],
  accounts: ["administrator"],
  limits: ["administrator"],
  integrity: ["administrator"],
  keys: ["merchant_operator"],
  webhooks: ["merchant_operator", "administrator"],
};

/** Pages hidden while the server runs in demo core-only mode (gateway DEMO_CORE_ONLY). Each
 *  role keeps its core pages: operator Overview + Payments; analyst + Fraud review and Block
 *  list; administrator + Merchants, Accounts and Audit log. Nothing is removed. */
export const DEMO_HIDDEN: ReadonlySet<keyof typeof CAN> = new Set([
  "keys", "webhooks", // operator
  "search", "alerts", "insights", "reports", "rules", // analyst
  "limits", "security", "integrity", // administrator
]);

/** Whether this staff member sees `page`: their role allows it, and demo mode doesn't hide it. */
export function canSee(staff: Pick<Staff, "role" | "demo_core_only">, page: keyof typeof CAN): boolean {
  return CAN[page].includes(staff.role) && !(staff.demo_core_only && DEMO_HIDDEN.has(page));
}

export function RoleGate({ page, children }: { page: keyof typeof CAN; children: ReactNode }) {
  const staff = useStaff();
  if (!canSee(staff, page)) return <Navigate to="/payments" replace />;
  return <>{children}</>;
}
