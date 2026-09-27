import uuid
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0008_user_nickname"),
    ]

    operations = [
        migrations.CreateModel(
            name="SecurityEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ("event_type", models.CharField(choices=[
                    ("login_ok", "Login succeeded"),
                    ("login_failed", "Login failed"),
                    ("password_changed", "Password changed"),
                    ("email_verified", "Email verified"),
                    ("mfa_enabled", "MFA enabled"),
                    ("ato_flag", "Account-takeover pattern flagged")],
                    max_length=30)),
                ("ip_address", models.GenericIPAddressField()),
                ("user_agent", models.TextField(blank=True, default="")),
                ("occurred_at", models.DateTimeField(
                    default=django.utils.timezone.now)),
                ("detail", models.JSONField(blank=True, default=dict,
                    help_text="e.g. failure reason. No passwords/tokens.")),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="security_events",
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "indexes": [models.Index(fields=["user", "occurred_at"],
                                         name="accounts_se_user_ts"),
                            models.Index(fields=["event_type", "occurred_at"],
                                         name="accounts_se_type_ts")],
            },
        ),
    ]
