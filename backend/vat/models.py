from typing import ClassVar

from django.db import models


class VatThresholdAlert(models.Model):
    """One VAT threshold alert sent (vat/monitor.py): each threshold alerts once per
    calendar year. Amounts and counts only, never who the posters are."""

    year = models.PositiveSmallIntegerField()
    # `vat.monitor.Threshold` key.
    threshold = models.CharField(max_length=40)
    # What the year's total stood at when the alert went out.
    total_cents = models.PositiveBigIntegerField()
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "vat_threshold_alerts"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["year", "threshold"], name="vat_alert_once_a_year"),
        ]

    def __str__(self) -> str:
        return f"VAT alert {self.threshold} {self.year}"
