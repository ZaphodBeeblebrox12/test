import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("evidence", "0001_initial"),
        ("subscriptions", "0017_strip_legacy_plan_unique"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlanRefundTerms",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("refund_window_days", models.PositiveIntegerField(
                    default=0,
                    help_text="Standard refund window in days from payment. "
                              "0 = not refundable.")),
                ("trial_refund_window_days", models.PositiveIntegerField(
                    blank=True, null=True,
                    help_text="Trial-specific window; null = use "
                              "refund_window_days.")),
                ("cancellation_deadline_hours", models.PositiveIntegerField(
                    blank=True, null=True,
                    help_text="For trials: cancel before conversion within N "
                              "hours of trial end; null = no special deadline.")),
                ("refund_terms_text", models.TextField(
                    blank=True, default="",
                    help_text="Plain-language terms shown at checkout and "
                              "snapshotted.")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("plan", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="refund_terms", to="subscriptions.plan")),
            ],
        ),
    ]
