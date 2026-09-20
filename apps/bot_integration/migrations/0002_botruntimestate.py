# Generated for the Provision v1 integration layer.

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("bot_integration", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="BotRuntimeState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("instance_id", models.CharField(max_length=64, unique=True)),
                ("base_url", models.URLField(max_length=255)),
                ("contract", models.CharField(default="provision", max_length=32)),
                ("contract_version", models.PositiveSmallIntegerField(default=1)),
                ("operations", models.JSONField(blank=True, default=list)),
                ("status", models.CharField(choices=[("online", "Online"), ("offline", "Offline"), ("incompatible", "Incompatible")], default="online", max_length=16)),
                ("bot_telegram_id", models.BigIntegerField(blank=True, null=True)),
                ("bot_username", models.CharField(blank=True, max_length=255)),
                ("last_seen", models.DateTimeField(default=django.utils.timezone.now)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-last_seen"]},
        ),
    ]
