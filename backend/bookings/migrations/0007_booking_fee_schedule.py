from django.db import migrations, models


class Migration(migrations.Migration):
    """The fee schedule a booking was charged under, kept for its cancellations (BEAV-191).
    Every booking so far was charged under the first schedule (12%, at least EUR 2.50), so
    existing rows are backfilled with its version and rate. The database defaults stay for
    one release, so an instance still on the old code can insert bookings during a rolling
    deploy."""

    dependencies = [
        ("bookings", "0006_withdrawal_statement"),
    ]

    operations = [
        migrations.AddField(
            model_name="booking",
            name="fee_schedule_version",
            field=models.CharField(db_default="1", max_length=32),
        ),
        migrations.AddField(
            model_name="booking",
            name="fee_percent",
            field=models.PositiveSmallIntegerField(db_default=12),
        ),
    ]
