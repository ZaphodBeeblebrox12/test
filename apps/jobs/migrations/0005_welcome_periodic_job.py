"""Seed the daily welcome-sequence PeriodicJob (admin-managed)."""
from django.db import migrations


def seed(apps, schema_editor):
    PeriodicJob = apps.get_model("jobs", "PeriodicJob")
    PeriodicJob.objects.get_or_create(
        name="welcome_sequence",
        defaults={"interval_minutes": 1440, "enabled": True,
                  "max_calls_per_run": 10},
    )


class Migration(migrations.Migration):

    dependencies = [("jobs", "0004_winback_periodic_job")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
