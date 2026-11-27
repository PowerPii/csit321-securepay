// One route per wireframe screen (Figma page "Staff dashboard"): D1, D2, D3, D4, D5 (dialog
// on D4), D6, D6b, fraud alerts and fraud reports (Could; reuse the D6 layouts), plus the administrator pages (merchant approval, audit log, security alerts), D7 business
// registration, D8 API keys and the password reset pages.
export { SignIn, Totp } from "./SignIn";
export { default as PaymentsList } from "./PaymentsList";
export { default as PaymentDetail } from "./PaymentDetail";
export { ReviewQueue, CaseDetail } from "./Reviews";
export { FraudAlerts, FraudReports } from "./Fraud";
export { default as FraudInsights } from "./FraudInsights";
export { Merchants, AuditLog } from "./Admin";
export { Register, VerifyEmail, ForgotPassword, ResetPassword } from "./Account";
export { default as ApiKeys } from "./ApiKeys";
export { SecurityAlerts } from "./Security";
export { Accounts, LimitsPage } from "./Manage";
export { Blocklist, FraudRules, HistorySearchPage } from "./FraudTools";
export { IntegrityPage } from "./Integrity";
export { default as Overview } from "./Overview";
export { TwoStep } from "./TwoStep";
export { WebhookLog } from "./Webhooks";
export { BalancePage } from "./Balance";
