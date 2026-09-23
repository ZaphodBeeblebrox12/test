"""Add winback.offered event type (choices-only, no schema change)."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("events", "0002_payment_billing_event_types")]

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
            ], max_length=50),
        ),
    ]
