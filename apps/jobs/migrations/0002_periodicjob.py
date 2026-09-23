"""PeriodicJob (admin-managed schedule) + seed the channel-membership sync."""
from django.db import migrations, models


def seed_periodic_jobs(apps, schema_editor):
    PeriodicJob = apps.get_model("jobs", "PeriodicJob")
    PeriodicJob.objects.get_or_create(
        name="sync_channel_memberships",
        defaults={"interval_minutes": 60, "enabled": True},
    )


class Migration(migrations.Migration):

    dependencies = [("jobs", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="PeriodicJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                     serialize=False, verbose_name="ID")),
                ("name", models.CharField(help_text=
                     "Handler key, e.g. sync_channel_memberships", max_length=100,
                     unique=True)),
                ("enabled", models.BooleanField(default=True)),
                ("interval_minutes", models.PositiveIntegerField(default=60)),
                ("last_run_at", models.DateTimeField(blank=True, null=True)),
                ("last_status", models.CharField(blank=True, choices=[
                     ("ok", "OK"), ("error", "Error")], default="", max_length=10)),
                ("last_error", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"verbose_name": "Periodic job",
                     "verbose_name_plural": "Periodic jobs"},
        ),
        migrations.RunPython(seed_periodic_jobs, migrations.RunPython.noop),
    ]
