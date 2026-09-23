"""Add welcome.day1 / welcome.day3 event types (choices-only)."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("events", "0003_winback_event")]

    operations = [
        migrations.AlterField(
            model_name="event",
            name="event_type",
            field=models.CharField(choices=[
                ("purchase.completed", "Purchase completed"),
                ("payment.failed", "Payment failed"),
                ("payment.refunded", "Payment refunded"),
                ("payment.disputed", "Payment disputed"),
                ("payment.chargeback", "Payment charged back"),
                ("payment.dispute_won", "Payment dispute won"),
                ("renewal.completed", "Renewal completed"),
                ("subscription.activated", "Subscription activated"),
                ("subscription.canceled", "Subscription canceled"),
                ("subscription.expired", "Subscription expired"),
                ("trial.started", "Trial started"),
                ("winback.offered", "Win-back offer sent"),
                ("welcome.day1", "Welcome day-1 sent"),
                ("welcome.day3", "Welcome day-3 sent"),
            ], max_length=50),
        ),
    ]
