/**
 * Public types, taken from the generated OpenAPI types (src/generated/openapi.ts, built from
 * contracts/generated/merchant-api.v1.json). Regenerate with `npm run generate`.
 */
import type { components } from "./generated/openapi.js";

type Schemas = components["schemas"];

export type Payment = Schemas["Payment"];
export type PaymentStatus = Schemas["PaymentStatus"];
export type OutcomeReason = Schemas["OutcomeReason"];
export type LineItem = Schemas["LineItem"];
export type Refund = Schemas["Refund"];
export type RefundStatus = Refund["status"];
export type ApiErrorBody = Schemas["Error"];
export type ApiErrorType = ApiErrorBody["error"]["type"];

export type CreatePaymentParams = Schemas["CreatePaymentRequest"];
export type CapturePaymentParams = Schemas["CapturePaymentRequest"];
export type CreateRefundParams = Schemas["CreateRefundRequest"];
export type Balance = Schemas["Balance"];
export type Payout = Schemas["Payout"];
export type CreatePayoutParams = Schemas["CreatePayoutRequest"];

/** `GET /v1/payouts`: the newest payouts first. */
export interface PayoutList {
  object: "list";
  has_more: boolean;
  data: Payout[];
}

/** `GET /v1/payments/{id}/refunds`. */
export interface RefundList {
  object: "list";
  payment_id: string;
  refundable_amount: number;
  data: Refund[];
}

type RawEvent = Schemas["Event"];
export type EventType = RawEvent["type"];
export type PaymentEventType = Extract<EventType, `payment.${string}`>;
export type RefundEventType = Extract<EventType, `refund.${string}`>;

type EventBase = Omit<RawEvent, "type" | "data">;
/** A `payment.*` webhook: `data.object` is the payment. */
export type PaymentEvent = EventBase & { type: PaymentEventType; data: { object: Payment } };
/** A `refund.*` webhook: `data.object` is the refund. */
export type RefundEvent = EventBase & { type: RefundEventType; data: { object: Refund } };
/** A verified webhook. Narrow on `type` to get the right `data.object`. */
export type SecurePayEvent = PaymentEvent | RefundEvent;
