"""Post ledger entries for captures and refunds made before the ledger existed (v1 data).

    python manage.py ledger_backfill            # post what is missing
    python manage.py ledger_backfill --dry-run  # only count it

Safe to run again: a payment or refund that already has its entry is skipped. Entries it
adds are marked `backfilled` and dated when the money moved, not when they were posted.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.ledger.models import JournalEntry
from apps.ledger.posting import post_capture, post_refund
from apps.payments.models import Payment
from apps.refunds.models import Refund


class Command(BaseCommand):
    help = "Post missing ledger entries for captures and completed refunds."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, dry_run=False, **options):
        payments = (Payment.objects.filter(captured_amount__gt=0)
                    .exclude(journal_entries__kind=JournalEntry.Kind.CAPTURE)
                    .order_by("captured_at", "id"))
        refunds = (Refund.objects.filter(status=Refund.Status.COMPLETED)
                   .exclude(journal_entries__kind=JournalEntry.Kind.REFUND)
                   .order_by("completed_at", "id"))
        if dry_run:
            self.stdout.write(f"Would post {payments.count()} capture(s) and "
                              f"{refunds.count()} refund(s).")
            return
        captures = refunds_done = 0
        for pk in payments.values_list("pk", flat=True):
            with transaction.atomic():
                payment = (Payment.objects.select_for_update(of=("self",))
                           .select_related("merchant").get(pk=pk))
                post_capture(payment, backfilled=True)
                captures += 1
        for pk in refunds.values_list("pk", flat=True):
            with transaction.atomic():
                refund = Refund.objects.select_related("payment__merchant").get(pk=pk)
                Payment.objects.select_for_update(of=("self",)).get(pk=refund.payment_id)
                post_refund(refund, backfilled=True)
                refunds_done += 1
        self.stdout.write(f"Posted {captures} capture(s) and {refunds_done} refund(s).")
