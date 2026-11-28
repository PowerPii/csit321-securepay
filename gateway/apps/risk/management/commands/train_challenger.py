"""Train a learning-loop challenger from labelled screening history (docs/v2/LEARNING-LOOP.md).

    python manage.py train_challenger

The same as "Train a challenger" on the dashboard's Fraud models page. The challenger only
scores in the shadow; an administrator promotes it on that page.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.audit.models import AuditEvent
from apps.audit.models import record as audit
from apps.common.errors import ApiError
from apps.risk import learning


class Command(BaseCommand):
    help = "Train a learning-loop challenger model (it scores in the shadow until promoted)."

    def handle(self, *args, **options):
        try:
            row = learning.train_challenger("command")
        except ApiError as exc:
            raise CommandError(exc.message) from None
        audit("risk_model.trained", actor_type=AuditEvent.ActorType.SYSTEM, actor_id="command",
              target_type="risk_model", target_id=row.version,
              details={"rows": row.training["rows"], "attacks": row.training["attacks"],
                       "genuine": row.training["genuine"]})
        h = row.training["holdout"]
        self.stdout.write(f"Challenger {row.version}: {row.training['rows']} rows "
                          f"({row.training['attacks']} attacks, {row.training['genuine']} genuine)")
        for who in ("champion", "challenger"):
            s = h[who]
            self.stdout.write(f"  holdout {who:10s} caught {s['attacks_stopped']}/{h['attacks']}"
                              f"  genuine stopped {s['genuine_stopped']}/{h['genuine']}")
        for name, s in h["by_scenario"].items():
            self.stdout.write(f"  {name:14s} champion {s['champion_stopped']}/{s['attacks']}"
                              f"  challenger {s['challenger_stopped']}/{s['attacks']}")
