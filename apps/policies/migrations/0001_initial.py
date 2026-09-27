import uuid
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
            name="PolicyVersion",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ("policy_type", models.CharField(choices=[
                    ("terms", "Terms of Service"),
                    ("refund", "Refund & Cancellation Policy"),
                    ("cancellation", "Cancellation Policy"),
                    ("risk", "Risk Disclosure (Trading Disclaimer)"),
                    ("privacy", "Privacy Policy")], max_length=20)),
                ("version", models.CharField(help_text="e.g. '1.0'", max_length=20)),
                ("title", models.CharField(max_length=200)),
                ("content_html", models.TextField(
                    help_text="Full text served to users at this version. "
                              "Immutable once published.")),
                ("content_sha256", models.CharField(editable=False, max_length=64)),
                ("status", models.CharField(choices=[
                    ("draft", "Draft (not served)"),
                    ("active", "Active (served to new acceptances)"),
                    ("archived", "Archived (historical, still viewable)")],
                    default="draft", max_length=10)),
                ("effective_from", models.DateTimeField()),
                ("published_at", models.DateTimeField(
                    blank=True, null=True,
                    help_text="When this version was first published (made active).")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="created_policy_versions",
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "indexes": [models.Index(fields=["policy_type", "status"],
                                         name="policies_pv_type_status")],
            },
        ),
        migrations.CreateModel(
            name="PolicyAcceptance",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ("context", models.CharField(choices=[
                    ("checkout", "Checkout"), ("cancellation", "Cancellation request"),
                    ("profile", "Profile"), ("trial", "Trial claim"),
                    ("signup", "Signup")], default="checkout", max_length=20)),
                ("accepted_at", models.DateTimeField(
                    help_text="Server timestamp of acceptance.")),
                ("ip_address", models.GenericIPAddressField()),
                ("user_agent", models.TextField(blank=True, default="")),
                ("session_ref", models.CharField(
                    blank=True, db_index=True, default="", max_length=64,
                    help_text="Salted hash of the Django session key at "
                              "acceptance time.")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("policy_version", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="acceptances", to="policies.policyversion")),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="policy_acceptances",
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "indexes": [models.Index(fields=["user", "accepted_at"],
                                         name="policies_pa_user_ts"),
                            models.Index(fields=["session_ref"],
                                         name="policies_pa_sess"),
                            models.Index(fields=["policy_version"],
                                         name="policies_pa_version")],
            },
        ),
        migrations.AddConstraint(
            model_name="policyversion",
            constraint=models.UniqueConstraint(fields=("policy_type", "version"),
                                               name="policies_version_unique"),
        ),
        migrations.AddConstraint(
            model_name="policyversion",
            constraint=models.UniqueConstraint(
                condition=models.Q(status="active"),
                fields=("policy_type",), name="policies_one_active_per_type"),
        ),
    ]
