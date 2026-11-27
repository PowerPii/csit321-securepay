"""
Gateway worker: polls the outbox and job tables.

Run as its own container with `python manage.py run_worker`. Rows are claimed with
SELECT ... FOR UPDATE SKIP LOCKED, sent outside the transaction, and retried with
bounded backoff. The same loop runs reconciliation sweeps, verification expiry and
data purges.
"""
import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from apps.payments import jobs

logger = logging.getLogger(__name__)

# The heartbeat is written at most this often (seconds), not on every one-second pass.
HEARTBEAT_EVERY = 15
_last_beat = 0.0


class Command(BaseCommand):
    help = "Run the outbox / reconciliation / expiry worker loop."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=float, default=1.0, help="Seconds between polls")
        parser.add_argument("--once", action="store_true", help="Run one pass and exit")

    def handle(self, *args, interval, once, **options):
        self.stdout.write("worker started")
        while True:
            run_pass()
            if once:
                return
            time.sleep(interval)


def run_pass() -> None:
    """Run every job once. One job failing never stops the others or the loop."""
    close_old_connections()
    for job in JOBS:
        try:
            job()
        except Exception:
            logger.exception("Worker job %s failed", job.__name__)
    beat()


def beat(force: bool = False) -> None:
    """Record that the loop is alive (apps.events.models.WorkerHeartbeat)."""
    global _last_beat
    now = time.monotonic()
    if not force and now - _last_beat < HEARTBEAT_EVERY:
        return
    from apps.events.models import WorkerHeartbeat

    try:
        WorkerHeartbeat.objects.update_or_create(name="worker",
                                                 defaults={"seen_at": timezone.now()})
        _last_beat = now
    except Exception:
        logger.exception("Worker heartbeat failed")


def deliver_outbox():
    """Send due OutboxEvents as signed webhooks; back off; mark exhausted ones undelivered."""
    from apps.events.delivery import deliver_due

    deliver_due()


def send_emails():
    """Send queued customer emails (receipts, guest status codes); retry and record failures."""
    from apps.events.emails import send_due

    send_due()


def reconcile_unresolved():
    """Query the simulator with the original operation ID at
    GATEWAY_RULES["RECONCILIATION_DELAYS_SECONDS"]; settle or escalate. Never sends a new
    charge. Also picks up operations a crashed process left without an answer (FR-TXN-15)."""
    from apps.payments.reconciliation import reconcile_due

    reconcile_due()


def expire_authorisations():
    """Cancel authorisations older than GATEWAY_RULES["AUTH_EXPIRY_DAYS"]."""
    jobs.expire_authorisations()


def expire_checkout_and_verification():
    """End checkout sessions past their hour: expired, or Blocked when a verification code
    was never entered (FR-CHK-18)."""
    jobs.expire_checkout_sessions()


def purge_card_data():
    """Delete vault card data for attempts that ended without submission."""
    from apps.vault.service import purge_expired

    purge_expired()


def purge_idempotency_records():
    """Free Idempotency-Keys past their 24 hours (contract section 5)."""
    from apps.payments.models import IdempotencyRecord

    IdempotencyRecord.purge_expired()


def seal_audit_entries():
    """Chain new audit entries by hash (NFR-INT-02)."""
    from apps.security.maintenance import seal_audit

    seal_audit()


def run_scheduled_checks():
    """Integrity check (FR-ADM-09), self-checks (FR-ADM-15) and the 12-month clean-up
    (NFR-PRI-01), each when its interval in GATEWAY_RULES has passed."""
    from apps.security.maintenance import run_due

    run_due()


def record_status_sample():
    """One sample for the public status page every five minutes (apps.status)."""
    from apps.status.service import record_sample

    record_sample()


def release_reserves():
    """v2 ledger: move each capture's reserve to available after RESERVE_HOLD_DAYS."""
    from apps.ledger.jobs import release_reserves as release

    release()


JOBS = [
    deliver_outbox,
    send_emails,
    reconcile_unresolved,
    expire_authorisations,
    expire_checkout_and_verification,
    purge_card_data,
    purge_idempotency_records,
    seal_audit_entries,
    run_scheduled_checks,
    record_status_sample,
    release_reserves,
]
