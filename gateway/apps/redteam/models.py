"""Red-team runs and the ground truth of what they sent (docs/v2/RED-TEAM.md).

A run is one attack session by the red-team runner (redteam/ at the repository root), in test
mode only. Every payment it makes is recorded here with what it really was: an attack of a
named scenario, or ordinary background traffic. Screening never reads these rows: they are
the labels the learning loop trains on, and the score the live view keeps.
"""
from django.db import models

from apps.common.ids import new_id


class RedTeamRun(models.Model):
    public_id = models.CharField(max_length=32, unique=True, editable=False)  # rt_...
    merchant = models.ForeignKey("merchants.Merchant", on_delete=models.PROTECT,
                                 related_name="+")
    api_key = models.ForeignKey("merchants.ApiKey", on_delete=models.PROTECT, related_name="+")
    scenario = models.CharField(max_length=40)
    note = models.CharField(max_length=200, blank=True)
    token_hash = models.CharField(max_length=64, unique=True)  # HMAC of the token, shown once
    started_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.public_id} {self.scenario}"

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = new_id("rt")
        super().save(*args, **kwargs)


class RedTeamPayment(models.Model):
    class Kind(models.TextChoices):
        ATTACK = "attack"
        LEGIT = "legit"  # background shoppers, so false positives show too

    run = models.ForeignKey(RedTeamRun, on_delete=models.CASCADE, related_name="payments")
    payment = models.OneToOneField("payments.Payment", on_delete=models.CASCADE,
                                   related_name="red_team")
    kind = models.CharField(max_length=8, choices=Kind.choices)
    scenario = models.CharField(max_length=40)
    bot = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["run", "created_at"])]
