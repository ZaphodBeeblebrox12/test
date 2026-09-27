import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="RiskSignal",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ("signal_type", models.CharField(choices=[
                    ("velocity", "Signup-to-payment velocity"),
                    ("multi_account_card", "Shared payment credential"),
                    ("multi_account_device", "Shared device"),
                    ("trial_abuse", "Trial abuse pattern"),
                    ("prior_dispute", "Prior dispute / chargeback"),
                    ("refund_pattern", "Repeat refund pattern")],
                    max_length=30)),
                ("severity", models.PositiveSmallIntegerField(default=3)),
                ("subject_hash", models.CharField(blank=True, db_index=True,
                                                  default="", max_length=64)),
                ("detail", models.JSONField(blank=True, default=dict)),
                ("source", models.CharField(default="rule", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("disposition", models.CharField(blank=True, default="",
                                                 max_length=20)),
                ("reviewed_by", models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    to=settings.AUTH_USER_MODEL)),
                ("user", models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="risk_signals",
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "indexes": [models.Index(fields=["signal_type", "created_at"],
                                         name="risk_rs_type_ts")],
            },
        ),
    ]
