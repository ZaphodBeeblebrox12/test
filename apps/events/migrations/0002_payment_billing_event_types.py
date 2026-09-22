"""Add billing event types (payment.refunded / payment.disputed /
payment.chargeback / payment.dispute_won). Choices-only AlterField: no
database schema change, keeps state consistent with the model.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0001_initial"),
    ]

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
            ], max_length=50),
        ),
    ]
