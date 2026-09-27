import uuid
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("payments", "0009_upgrade_intent"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Dispute",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ("provider", models.CharField(choices=[
                    ("stripe", "Stripe"), ("razorpay", "Razorpay")],
                    max_length=20)),
                ("provider_dispute_id", models.CharField(max_length=255)),
                ("network", models.CharField(blank=True, default="", max_length=20)),
                ("reason_raw", models.CharField(blank=True, default="",
                    help_text="Provider reason, verbatim.", max_length=50)),
                ("reason_category", models.CharField(choices=[
                    ("fraud", "Fraud - card absent"),
                    ("unrecognized", "Unrecognized transaction"),
                    ("not_received", "Service not received"),
                    ("not_as_described", "Not as described"),
                    ("recurring_cancelled", "Recurring/cancelled"),
                    ("credit_not_processed", "Credit not processed"),
                    ("duplicate", "Duplicate transaction"),
                    ("customer_initiated", "Customer initiated"),
                    ("other", "Other")], default="other", max_length=30)),
                ("amount_cents", models.PositiveIntegerField(default=0)),
                ("currency", models.CharField(default="USD", max_length=3)),
                ("status", models.CharField(choices=[
                    ("opened", "Opened"),
                    ("evidence_prep", "Evidence preparation"),
                    ("ready_for_review", "Ready for review"),
                    ("submitted", "Evidence submitted"),
                    ("won", "Won"), ("lost", "Lost"),
                    ("withdrawn", "Withdrawn by customer/issuer"),
                    ("accepted", "Accepted (merchant conceded)")],
                    default="opened", max_length=25)),
                ("opened_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("evidence_due_at", models.DateTimeField(blank=True, null=True,
                    help_text="Provider response deadline.")),
                ("funds_withdrawn_at", models.DateTimeField(blank=True, null=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("claim_text", models.TextField(blank=True, default="")),
                ("access_actioned", models.BooleanField(default=False,
                    help_text="Confirmed-loss access revocation ran exactly once.")),
                ("payment_intent", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="disputes", to="payments.paymentintent")),
                ("assigned_to", models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="assigned_disputes",
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "constraints": [models.UniqueConstraint(fields=("provider",
                    "provider_dispute_id"), name="disputes_provider_case_unique")],
                "indexes": [models.Index(fields=["status"], name="disputes_d_status"),
                            models.Index(fields=["evidence_due_at"],
                                         name="disputes_d_due"),
                            models.Index(fields=["payment_intent"],
                                         name="disputes_d_intent")],
            },
        ),
        migrations.CreateModel(
            name="DisputeEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ("event_type", models.CharField(choices=[
                    ("created", "Created"), ("status_changed", "Status changed"),
                    ("note", "Note"), ("evidence_generated", "Evidence generated"),
                    ("evidence_submitted", "Evidence submitted"),
                    ("access_action", "Access action"),
                    ("customer_contacted", "Customer contacted")],
                    max_length=30)),
                ("from_status", models.CharField(blank=True, default="",
                                                 max_length=25)),
                ("to_status", models.CharField(blank=True, default="",
                                               max_length=25)),
                ("note", models.TextField(blank=True, default="")),
                ("occurred_at", models.DateTimeField(
                    default=django.utils.timezone.now)),
                ("prev_hash", models.CharField(blank=True, default="",
                                               max_length=64)),
                ("content_hash", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True,
                    help_text="Null = system/webhook.",
                    on_delete=django.db.models.deletion.SET_NULL,
                    to=settings.AUTH_USER_MODEL)),
                ("dispute", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="events", to="disputes.dispute")),
            ],
            options={
                "ordering": ["occurred_at", "created_at"],
                "get_latest_by": "occurred_at",
                "indexes": [models.Index(fields=["dispute", "occurred_at"],
                                         name="disputes_de_dispute_ts")],
            },
        ),
        migrations.CreateModel(
            name="DisputeEvidencePackage",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ("generator_version", models.CharField(default="1", max_length=20)),
                ("reason_category", models.CharField(default="other", max_length=30)),
                ("sections", models.JSONField(default=dict)),
                ("timeline", models.JSONField(default=list)),
                ("narrative_draft", models.TextField(blank=True, default="")),
                ("narrative_final", models.TextField(blank=True, default="")),
                ("readiness", models.JSONField(default=dict)),
                ("content_sha256", models.CharField(max_length=64)),
                ("generated_at", models.DateTimeField(
                    default=django.utils.timezone.now)),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("submitted_via", models.CharField(blank=True, default="",
                                                   max_length=20)),
                ("outcome", models.CharField(choices=[
                    ("pending", "Pending"), ("won", "Won"), ("lost", "Lost"),
                    ("withdrawn", "Withdrawn"), ("accepted", "Accepted")],
                    default="pending", max_length=20)),
                ("dispute", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="packages", to="disputes.dispute")),
                ("generated_by", models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="generated_packages",
                    to=settings.AUTH_USER_MODEL)),
                ("submitted_by", models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="submitted_packages",
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-generated_at"],
                "indexes": [models.Index(fields=["dispute", "generated_at"],
                                         name="disputes_dep_dispute_ts")],
            },
        ),
    ]
