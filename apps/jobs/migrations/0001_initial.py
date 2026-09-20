# Generated for the Django-native job system.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ProvisioningOperation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("operation_id", models.UUIDField(default=__import__("uuid").uuid4, editable=False, unique=True)),
                ("operation", models.CharField(choices=[("grant", "Grant"), ("revoke", "Revoke")], max_length=16)),
                ("user_id", models.UUIDField()),
                ("channel_id", models.CharField(max_length=64)),
                ("state", models.CharField(choices=[("pending", "Pending"), ("creating", "Creating"), ("created", "Created"), ("sending", "Sending"), ("sent", "Sent"), ("unknown_external_state", "Unknown External State"), ("completed", "Completed"), ("failed", "Failed")], default="pending", max_length=24)),
                ("invite_link", models.CharField(blank=True, max_length=512, null=True)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("last_error", models.TextField(blank=True)),
                ("telegram_user_id", models.BigIntegerField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"indexes": [models.Index(fields=["state"], name="jobs_provis_state_a24ff2_idx"), models.Index(fields=["user_id", "channel_id"], name="jobs_provis_user_id_162134_idx")]},
        ),
        migrations.CreateModel(
            name="ReconcileState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version", models.BigIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="reconcile_state", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="Job",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(max_length=32)),
                ("payload", models.JSONField(default=dict)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("running", "Running"), ("succeeded", "Succeeded"), ("failed", "Failed")], default="pending", max_length=16)),
                ("requested_version", models.BigIntegerField(default=0)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("max_attempts", models.PositiveSmallIntegerField(default=5)),
                ("next_attempt_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("idempotency_key", models.CharField(blank=True, max_length=160, null=True)),
                ("locked_at", models.DateTimeField(editable=False, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.AddIndex(
            model_name="job",
            index=models.Index(fields=["status", "next_attempt_at"], name="jobs_job_status_72c9f1_idx"),
        ),
        migrations.AddIndex(
            model_name="job",
            index=models.Index(fields=["kind"], name="jobs_job_kind_6f5e94_idx"),
        ),
        migrations.AddConstraint(
            model_name="job",
            constraint=models.UniqueConstraint(condition=models.Q(("status__in", ("pending", "running")), _connector="AND"), fields=("idempotency_key",), name="jobs_open_key_uniq"),
        ),
    ]
