"""Emailed receipts: an authorised payment (FR-CHK-15, UC-04) and a completed refund.

Amount, merchant, date, card brand and last four digits, and the payment reference; for a
guest payment (every payment in the MVP) a link to the guest status page that fills in the
reference. Queued in the same transaction as the authorisation, sent by the worker.

A refund receipt (demo sprint 14) is queued in the transaction that marks the refund
completed, so the customer hears about money coming back only once the simulator has
confirmed it, and only once even if a late answer arrives.
"""
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.conf import settings

from apps.events.emails import queue
from apps.events.models import OutboundEmail
from apps.fx.currencies import format_amount

BRANDS = {"visa": "Visa", "mastercard": "Mastercard"}


def money(amount: int, currency: str = "SGD") -> str:
    return format_amount(amount, currency)


def status_link(reference: str) -> str:
    return f"{settings.GUEST_STATUS_URL}?{urlencode({'ref': reference})}"


def body_for(payment) -> str:
    when = payment.authorised_at.astimezone(ZoneInfo(settings.DASHBOARD_TIME_ZONE))
    card = (f"{BRANDS.get(payment.card_brand, payment.card_brand.title() or 'Card')} "
            f"ending {payment.card_last4}" if payment.card_last4 else "Card")
    lines = [
        f"Thank you. Your payment to {payment.merchant.name} was approved.",
        "",
        f"Amount:     {money(payment.amount, payment.currency)}",
        f"Paid to:    {payment.merchant.name}",
        f"Date:       {when:%d %b %Y, %H:%M} (Singapore time)",
        f"Card:       {card}",
        f"Reference:  {payment.reference}",
    ]
    if payment.line_items:
        lines += ["", "Items:"]
        lines += [f"  {item['name']} x {item['quantity']}  "
                  f"{money(item['unit_amount'] * item['quantity'], payment.currency)}"
                  for item in payment.line_items]
        lines += [f"  Total  {money(payment.amount, payment.currency)}"]
    lines += [
        "",
        "Keep the reference for any question about this payment.",
        "",
        ("Check this payment later (you will need the email address you paid with, and we "
         "will email you a one-time code):"),
        status_link(payment.reference),
        "",
        ("SecurePay processes payments for the shop. We never ask for your card details or "
         "a code by phone or message."),
    ]
    return "\n".join(lines) + "\n"


def queue_receipt(payment) -> OutboundEmail | None:
    """Call inside the authorisation's transaction. No email address, no receipt."""
    if not payment.customer_email:
        return None
    subject = (f"Receipt: {money(payment.amount, payment.currency)} to {payment.merchant.name} "
               f"({payment.reference})")
    return queue(OutboundEmail.Kind.RECEIPT, payment, payment.customer_email, subject,
                 body_for(payment))


def refund_body_for(refund) -> str:
    payment = refund.payment
    when = refund.completed_at.astimezone(ZoneInfo(settings.DASHBOARD_TIME_ZONE))
    card = (f"{BRANDS.get(payment.card_brand, payment.card_brand.title() or 'Card')} "
            f"ending {payment.card_last4}" if payment.card_last4 else "The card you paid with")
    full = refund.amount == payment.amount
    lines = [
        f"{payment.merchant.name} refunded {money(refund.amount, payment.currency)} to your card.",
        "",
        f"Refund:      {money(refund.amount, payment.currency)}{' (full refund)' if full else ''}",
        f"From:        {payment.merchant.name}",
        f"Date:        {when:%d %b %Y, %H:%M} (Singapore time)",
        f"Card:        {card}",
        f"Refund ref:  {refund.reference}",
        f"Payment:     {payment.reference} ({money(payment.amount, payment.currency)})",
        "",
        ("Your bank usually shows a refund within 5 to 10 working days. This is a simulated "
         "payment, so no real money moves."),
        "",
        ("Check the payment later (you will need the email address you paid with, and we "
         "will email you a one-time code):"),
        status_link(payment.reference),
        "",
        ("Questions about the order itself go to the shop. SecurePay never asks for your "
         "card details or a code by phone or message."),
    ]
    return "\n".join(lines) + "\n"


def queue_refund_receipt(refund) -> OutboundEmail | None:
    """Call inside the transaction that completes the refund."""
    payment = refund.payment
    if not payment.customer_email:
        return None
    subject = (f"Refund: {money(refund.amount, payment.currency)} from {payment.merchant.name} "
               f"({payment.reference})")
    return queue(OutboundEmail.Kind.REFUND_RECEIPT, payment, payment.customer_email, subject,
                 refund_body_for(refund))


# --- downloadable PDF receipt (demo sprint 17, Could item) ---------------------------------

# Brand colours, the same as frontend/brand/tokens.json (Slate rebrand).
NAVY, INK, BRASS, BRASS_LIGHT, MUTED, MIST = (
    "#13243F", "#0E1B30", "#B08A3E", "#D2AE63", "#4A5568", "#ECEFF4")


def no_receipt():
    from apps.common.errors import ApiError

    return ApiError(409, "no_receipt",
                    "There is no receipt for this payment because it was not charged.")


def receipt_pdf(payment) -> bytes:
    """One A4 page: the same facts as the emailed receipt, plus refunds so far.

    Only for a payment that was charged (authorised or captured); anything else has no
    receipt. The card shows as brand and last four digits only (NFR-CON-02).
    """
    from apps.common import pdf
    from apps.payments.states import PaymentStatus
    from apps.refunds.models import Refund

    if payment.status not in (PaymentStatus.AUTHORISED, PaymentStatus.CAPTURED) \
            or payment.authorised_at is None:
        raise no_receipt()
    tz = ZoneInfo(settings.DASHBOARD_TIME_ZONE)
    when = payment.authorised_at.astimezone(tz)
    card = (f"{BRANDS.get(payment.card_brand, payment.card_brand.title() or 'Card')} "
            f"ending {payment.card_last4}" if payment.card_last4 else "Card")
    page = pdf.Page()
    left, right = 56, pdf.PAGE_W - 56

    page.rect(0, 0, pdf.PAGE_W, 92, fill=NAVY)
    # The securepay. wordmark: lowercase name, then the brass full stop.
    page.text(left, 54, "securepay", size=24, bold=True, colour="#FFFFFF")
    page.text(left + pdf.text_width("securepay", 24, bold=True), 54, ".", size=24, bold=True,
              colour=BRASS_LIGHT)
    page.text(right, 50, "Payment receipt", size=13, colour="#FFFFFF", align="right")
    page.rect(0, 92, pdf.PAGE_W, 2, fill=BRASS)
    page.rect(left, 112, 236, 20, fill=MIST)
    page.text(left + 8, 126, "TEST MODE  -  simulated payment, no real money", size=8.5,
              bold=True, colour=INK)

    y = 170
    page.text(left, y, f"Paid to {payment.merchant.name}", size=17, bold=True)
    y += 32
    page.text(left, y, money(payment.amount, payment.currency), size=26, bold=True, colour=NAVY)
    y += 34
    rows = [("Date", f"{when:%d %b %Y, %H:%M} (Singapore time)"),
            ("Card", card),
            ("Payment reference", payment.reference)]
    if payment.status == PaymentStatus.CAPTURED:
        rows.append(("Charged", money(payment.captured_amount or payment.amount, payment.currency)))
    else:
        rows.append(("Status", "Approved; the shop charges the card when it sends the order"))
    for label, value in rows:
        page.line(left, y, right)
        y += 20
        page.text(left, y, label, size=10, colour=MUTED)
        page.text(left + 150, y, value, size=10.5)
        y += 10
    page.line(left, y, right)

    if payment.line_items:
        y += 34
        page.text(left, y, "Items", size=12, bold=True)
        y += 8
        for item in payment.line_items[:20]:
            y += 20
            page.text(left, y, f"{item['name']}  x {item['quantity']}", size=10.5)
            page.text(right, y, money(item["unit_amount"] * item["quantity"], payment.currency), size=10.5,
                      align="right")
        y += 10
        page.line(left, y, right, colour=INK, width=1)
        y += 20
        page.text(left, y, "Total", size=11, bold=True)
        page.text(right, y, money(payment.amount, payment.currency), size=11, bold=True, align="right")

    refunds = list(Refund.objects.filter(payment=payment, status=Refund.Status.COMPLETED)
                   .order_by("completed_at"))
    if refunds:
        y += 34
        page.text(left, y, "Refunds", size=12, bold=True)
        y += 8
        for item in refunds[:10]:
            y += 20
            at = item.completed_at.astimezone(tz) if item.completed_at else when
            page.text(left, y, f"{at:%d %b %Y}   {item.reference}", size=10.5)
            page.text(right, y, f"-{money(item.amount, payment.currency)}", size=10.5, align="right")

    footer = pdf.PAGE_H - 96
    page.line(left, footer, right)
    page.text(left, footer + 20, "Keep the payment reference for any question about this "
              "payment. Questions about the order go to the shop.", size=8.5, colour=MUTED)
    page.text(left, footer + 34, "SecurePay processes payments for the shop and never asks "
              "for your card details or a code by phone or message.", size=8.5, colour=MUTED)
    page.text(left, footer + 48, "SecurePay is a final-year student project. This receipt is "
              "for a simulated test payment.", size=8.5, colour=MUTED)
    return pdf.render(page, title=f"Receipt {payment.reference}")


def pdf_response(payment):
    """The receipt as a download (the page links to it; the browser saves the file)."""
    from django.http import HttpResponse

    response = HttpResponse(receipt_pdf(payment), content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="securepay-receipt-{payment.reference}.pdf"')
    response["X-Content-Type-Options"] = "nosniff"
    return response
