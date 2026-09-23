"""Seed the daily win-back PeriodicJob (admin-managed schedule)."""
from django.db import migrations


def seed(apps, schema_editor):
    PeriodicJob = apps.get_model("jobs", "PeriodicJob")
    PeriodicJob.objects.get_or_create(
        name="win_back_expired",
        defaults={"interval_minutes": 1440, "enabled": True,
                  "max_calls_per_run": 10},
    )


class Migration(migrations.Migration):

    dependencies = [("jobs", "0003_periodicjob_trickle")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
