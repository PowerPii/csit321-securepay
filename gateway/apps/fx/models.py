"""The published FX rate sheet (docs/v2/FX.md).

One row per publication. The newest sheet is the one new payments lock; older sheets stay,
so every payment can show the exact rate it used. Rates are SGD per one whole unit of the
other currency (e.g. THB "0.038500" = S$0.0385 per baht), kept as decimal strings.
"""
from decimal import Decimal

from django.db import models
from django.utils import timezone

from apps.common.ids import new_id


class RateSheet(models.Model):
    public_id = models.CharField(max_length=32, unique=True, editable=False)
    rates = models.JSONField()
    source = models.CharField(max_length=200, blank=True)
    published_by = models.CharField(max_length=64, blank=True)
    published_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-published_at", "-id"]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = new_id("fxr")
        super().save(*args, **kwargs)

    def rate(self, code: str) -> Decimal:
        return Decimal(self.rates[code])

    def __str__(self):
        return f"{self.public_id} {self.published_at:%Y-%m-%d %H:%M}"
