"""Check the ledger: every entry balances, and every capture and refund has its entry.

    python manage.py ledger_check     # exit code 1 if anything is wrong
"""
from django.core.management.base import BaseCommand, CommandError

from apps.ledger.checks import problems
from apps.ledger.models import JournalEntry


class Command(BaseCommand):
    help = "Check that the ledger balances and matches the payments and refunds."

    def handle(self, *args, **options):
        found = problems()
        for line in found:
            self.stderr.write(line)
        if found:
            raise CommandError(f"ledger check failed: {len(found)} problem(s)")
        self.stdout.write(f"Ledger OK: {JournalEntry.objects.count()} entries, all balanced.")
