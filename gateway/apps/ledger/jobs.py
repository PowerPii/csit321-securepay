"""Scheduled ledger jobs, run by the worker (apps/events/management/commands/run_worker.py)."""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from apps.ledger import posting
from apps.ledger.models import JournalEntry

logger = logging.getLogger(__name__)


def reserve_release_at(capture_entry: JournalEntry):
    return capture_entry.occurred_at + timedelta(
        days=settings.GATEWAY_RULES["RESERVE_HOLD_DAYS"])


def due_captures(now=None):
    """Capture entries whose hold has passed and whose reserve has not been released yet."""
    now = now or timezone.now()
    cutoff = now - timedelta(days=settings.GATEWAY_RULES["RESERVE_HOLD_DAYS"])
    released = JournalEntry.objects.filter(kind=JournalEntry.Kind.RESERVE_RELEASE,
                                           source_id=OuterRef("public_id"))
    return (JournalEntry.objects.filter(kind=JournalEntry.Kind.CAPTURE,
                                        occurred_at__lte=cutoff,
                                        lines__memo="reserve")
            .exclude(Exists(released)).order_by("occurred_at", "id"))


def release_reserves(limit: int | None = None, now=None) -> int:
    """Release the reserve of every capture older than RESERVE_HOLD_DAYS. Safe to repeat:
    the one-entry-per-source rule means a capture's reserve is released at most once."""
    done = 0
    for entry_id in list(due_captures(now).values_list("pk", flat=True)
                         [:limit or settings.WORKER_BATCH_SIZE]):
        with transaction.atomic():
            entry = JournalEntry.objects.select_related("merchant", "payment").get(pk=entry_id)
            if posting.post_reserve_release(entry) is not None:
                done += 1
    if done:
        logger.info("Released the reserve of %d capture(s)", done)
    return done
