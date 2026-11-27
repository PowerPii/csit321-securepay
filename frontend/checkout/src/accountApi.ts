// Customer account API (gateway/apps/customers), screens C10 to C12. Same origin, JSON only.
// The session lives in the HttpOnly `sp_customer` cookie; the page never sees it.
import { ApiError, jsonClient } from "./http";

export interface Customer {
  id: string;
  full_name: string;
  email: string;
  idle_minutes: number;
  idle_warning_minutes: number;
}

export interface PersonalData {
  full_name: string;
  email: string;
  created_at: string;
  last_sign_in_at: string | null;
  password_changed_at: string | null;
  details_changed_at: string | null;
  privacy_notice_version: string | null;
  privacy_consent_at: string | null;
  payments: number;
  active_sessions: number;
}

export interface HistoryRow {
  reference: string;
  merchant_name: string;
  amount: number;
  currency: string;
  status: string;
  status_label: string;
  created_at: string;
}

export interface PaymentDetail extends HistoryRow {
  order_reference: string;
  card: { brand: string; last4: string } | null;
  refunded_amount?: number;
  timeline: { at: string; label: string }[];
}

export interface Passkey {
  id: string;
  name: string;
  synced: boolean; // kept in iCloud Keychain, Google Password Manager and the like
  created_at: string;
  last_used_at: string | null;
}

type Options = { options: Record<string, unknown> };

export interface SpendFigures {
  spent: number;
  refunded: number;
  net: number;
  payments: number;
}
export interface MonthSpend extends SpendFigures { month: string } // "2026-11"
export interface MerchantSpend extends SpendFigures {
  merchant_name: string;
  merchant_count?: number; // only on the "Other merchants" row
}
export interface Insights {
  currency: string;
  time_zone: string;
  from: string;
  to: string;
  totals: SpendFigures & { merchants: number; on_hold: number; on_hold_payments: number };
  by_month: MonthSpend[];
  by_merchant: MerchantSpend[];
}

export class AccountApiError extends ApiError {
  constructor(status: number, code: string, message: string, param: string | null, public retryAfter: number | null) {
    super(status, code, message, param);
  }
}

const call = jsonClient("/customer", ({ status, code, message, param, retryAfter }) =>
  new AccountApiError(status, code, message, param, retryAfter));

const post = <T>(path: string, body: unknown = {}) => call<T>(path, { method: "POST", body: JSON.stringify(body) });

export const getMe = () => call<Customer>("me");
export const signIn = (email: string, password: string, remember: boolean) =>
  post<Customer>("sign-in", { email, password, remember });
export const register = (full_name: string, email: string, password: string) =>
  post<Customer>("register", { full_name, email, password, privacy_consent: true });
export const getProfile = () => call<PersonalData>("profile");
export const updateProfile = (current_password: string, full_name: string, email: string) =>
  post<PersonalData>("profile", { current_password, full_name, email });
export const signOut = () => post<void>("sign-out");
export const signOutEverywhere = () => post<void>("sign-out-everywhere");
export const changePassword = (current_password: string, password: string) =>
  post<{ message: string }>("password", { current_password, password });
export const getPayments = () => call<{ data: HistoryRow[]; has_more: boolean }>("payments");
export const getInsights = () => call<Insights>("insights");
export const getPayment = (reference: string) => call<PaymentDetail>(`payments/${encodeURIComponent(reference)}`);
export const requestReset = (email: string) => post<{ message: string }>("password-reset/request", { email });
export const checkReset = (token: string) => post<{ email: string }>("password-reset/check", { token });
export const confirmReset = (token: string, password: string) =>
  post<{ message: string }>("password-reset/confirm", { token, password });
// v2 passkeys (docs/v2/PASSKEYS.md)
export const getPasskeys = () => call<{ data: Passkey[]; limit: number }>("passkeys");
export const passkeyRegisterOptions = () => post<Options>("passkeys/register/options");
export const addPasskey = (credential: Record<string, unknown>, name?: string) =>
  post<Passkey>("passkeys/register", name ? { credential, name } : { credential });
export const removePasskey = (id: string) => post<void>(`passkeys/${encodeURIComponent(id)}/remove`);
export const passkeySignInOptions = () => post<Options>("passkeys/sign-in/options");
export const signInWithPasskey = (credential: Record<string, unknown>, remember: boolean) =>
  post<Customer>("passkeys/sign-in", { credential, remember });
