"""Billing additions: PaymentIntent refund/chargeback state + Refund table.

The original payment record (amount, status) is never rewritten by refunds
or disputes — these fields record what happened AFTER the successful charge.
Refund rows are keyed by the provider's refund id so webhook retries and
partial refunds stay idempotent.
"""
import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0007_paymentintent_base_amount_cents"),
    ]

    operations = [
        migrations.AddField(
            model_name="paymentintent",
            name="provider_payment_id",
            field=models.CharField(blank=True, default="",
                help_text="Provider payment/charge id (pi_.. / pay_..) used to link refund/dispute webhooks to this intent.",
                max_length=255),
        ),
        migrations.AddField(
            model_name="paymentintent",
            name="refunded_cents",
            field=models.PositiveIntegerField(default=0,
                help_text="Cumulative refunded amount in minor units (cents/paise)."),
        ),
        migrations.AddField(
            model_name="paymentintent",
            name="refunded_at",
            field=models.DateTimeField(blank=True, null=True,
                help_text="Timestamp of the most recent refund."),
        ),
        migrations.AddField(
            model_name="paymentintent",
            name="chargeback",
            field=models.BooleanField(default=False,
                help_text="A dispute/chargeback has been opened on this payment."),
        ),
        migrations.AddField(
            model_name="paymentintent",
            name="chargeback_confirmed",
            field=models.BooleanField(default=False,
                help_text="Dispute confirmed against us (funds withdrawn or dispute lost)."),
        ),
        migrations.AddField(
            model_name="paymentintent",
            name="chargeback_reference",
            field=models.CharField(blank=True, default="",
                help_text="Provider dispute id (dp_..).", max_length=255),
        ),
        migrations.AddField(
            model_name="paymentintent",
            name="chargeback_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="Refund",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False,
                    primary_key=True, serialize=False)),
                ("provider", models.CharField(choices=[("stripe", "Stripe"),
                    ("razorpay", "Razorpay")], max_length=20)),
                ("provider_refund_id", models.CharField(help_text=
                    "Provider refund id (rf_.. / rfd_..); 'manual:<uuid>' for admin-recorded refunds.",
                    max_length=255)),
                ("amount_cents", models.PositiveIntegerField(help_text=
                    "Refunded amount in minor units (cents/paise).")),
                ("currency", models.CharField(default="USD", max_length=3)),
                ("source", models.CharField(choices=[("webhook", "Webhook"),
                    ("manual", "Manual (admin)")], default="webhook", max_length=20)),
                ("refunded_at", models.DateTimeField(help_text=
                    "When the provider processed the refund.")),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("payment_intent", models.ForeignKey(help_text=
                    "The payment this refund applies to.",
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="refunds", to="payments.paymentintent")),
            ],
            options={"ordering": ["-refunded_at"], "verbose_name": "refund",
                     "verbose_name_plural": "refunds"},
        ),
        migrations.AddConstraint(
            model_name="refund",
            constraint=models.UniqueConstraint(fields=("provider", "provider_refund_id"),
                                               name="uniq_provider_refund"),
        ),
    ]
