"""PeriodicJob: API-call budget per run + persisted sweep cursor (trickle sync)."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("jobs", "0002_periodicjob")]

    operations = [
        migrations.AddField(
            model_name="periodicjob",
            name="max_calls_per_run",
            field=models.PositiveIntegerField(default=10,
                help_text="Max getChatMember API calls consumed per run (rate budget)."),
        ),
        migrations.AddField(
            model_name="periodicjob",
            name="state",
            field=models.JSONField(blank=True, default=dict,
                help_text="Scheduler cursor, e.g. {\"after_user_id\": 123}."),
        ),
    ]
