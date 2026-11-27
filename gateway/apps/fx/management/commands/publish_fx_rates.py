"""Publish the day's FX rate sheet (docs/v2/FX.md).

    python manage.py publish_fx_rates                 # the committed reference rates
    python manage.py publish_fx_rates --file r.json   # {"USD": "1.31", "THB": "0.0386", ...}
    python manage.py publish_fx_rates --set THB=0.0391 # the current sheet with one change

New payments lock the newest sheet; payments already made keep their rate.
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.audit.models import record as audit
from apps.fx import rates


class Command(BaseCommand):
    help = "Publish a new FX rate sheet (SGD per unit of each currency)."

    def add_arguments(self, parser):
        parser.add_argument("--file", help="JSON object of rates")
        parser.add_argument("--set", action="append", default=[], metavar="CODE=RATE",
                            help="Change one rate of the current sheet (repeatable)")
        parser.add_argument("--source", default="", help="Where the rates came from")

    def handle(self, *args, file, set, source, **options):
        if file:
            new = json.loads(Path(file).read_text())
            source = source or f"file {Path(file).name}"
        elif set:
            new = dict(rates.current().rates)
            source = source or "manual change"
        else:
            new = rates.reference_rates()
            source = source or "reference rates"
        for item in set:
            code, _, value = item.partition("=")
            new[code.strip().upper()] = value.strip()
        try:
            sheet = rates.publish(new, source=source, published_by="publish_fx_rates")
        except rates.RateError as exc:
            raise CommandError(str(exc)) from None
        audit("fx.rates_published", actor_type="system", actor_id="publish_fx_rates",
              target_type="fx_rates", target_id=sheet.public_id,
              details={"rates": sheet.rates, "source": source})
        self.stdout.write(f"Published {sheet.public_id}: " +
                          ", ".join(f"{k} {v}" for k, v in sheet.rates.items()))
