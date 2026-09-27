import uuid
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("payments", "0009_upgrade_intent"),
        ("subscriptions", "0017_strip_legacy_plan_unique"),
        ("bot_integration", "0001_initial"),
        ("policies", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CheckoutEvidence",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ("ip_address", models.GenericIPAddressField(
                    help_text="Clear-text IP (required format for Visa CE3.0 "
                              "evidence). Restricted access; see privacy docs.")),
                ("user_agent", models.TextField()),
                ("device_fingerprint", models.CharField(
                    db_index=True, max_length=64,
                    help_text="sha256 (>=20 chars) of canonicalized browser "
                              "signals. Evidence-oriented identifier; reproducible "
                              "per FINGERPRINT_VERSION.")),
                ("fingerprint_version", models.CharField(default="1", max_length=10)),
                ("device_signals", models.JSONField(
                    blank=True, default=dict,
                    help_text="Canonicalized non-sensitive signals used for the "
                              "fingerprint (UA, language, timezone, screen, "
                              "platform, concurrency).")),
                ("risk_device_id", models.CharField(
                    blank=True, db_index=True, default="", max_length=64,
                    help_text="Salted HMAC of the fingerprint - internal continuity "
                              "id, not network-facing.")),
                ("accept_language", models.CharField(blank=True, default="",
                                                    max_length=64)),
                ("session_ref", models.CharField(db_index=True, max_length=64)),
                ("pricing_snapshot", models.JSONField(default=dict,
                    help_text="Point-in-time commercial agreement: plan name/"
                              "description shown, interval, price, currency, "
                              "country, trial info, coupon/referral.")),
                ("accepted_at", models.DateTimeField(
                    help_text="Server timestamp of agreement.")),
                ("client_ts", models.DateTimeField(blank=True, null=True,
                    help_text="Client-reported time (informational).")),
                ("checkout_version", models.CharField(default="1", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("payment_intent", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="checkout_evidence", to="payments.paymentintent")),
                ("terms_version", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="checkout_terms_evidence",
                    to="policies.policyversion")),
                ("refund_policy_version", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="checkout_refund_evidence",
                    to="policies.policyversion")),
                ("risk_disclaimer_version", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="checkout_risk_evidence",
                    to="policies.policyversion")),
            ],
            options={
                "indexes": [models.Index(fields=["device_fingerprint"],
                                         name="evidence_ce_fp"),
                            models.Index(fields=["ip_address"],
                                         name="evidence_ce_ip")],
            },
        ),
        migrations.CreateModel(
            name="PaymentAuthentication",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ("three_ds_result", models.CharField(choices=[
                    ("attempted", "Attempted"), ("authenticated", "Authenticated"),
                    ("liability_shift", "Liability shift"),
                    ("liability_shift_unsuccessful",
                     "Liability shift (unsuccessful)"),
                    ("failed", "Failed"), ("not_requested", "Not requested"),
                    ("unknown", "Unknown")], default="unknown", max_length=30)),
                ("eci", models.CharField(blank=True, default="", max_length=4,
                    help_text="May be unavailable via PSP API; never invent.")),
                ("cavv_present", models.BooleanField(default=False)),
                ("cvc_check", models.CharField(blank=True, default="",
                                              max_length=20)),
                ("avs_line1_check", models.CharField(blank=True, default="",
                                                    max_length=20)),
                ("avs_zip_check", models.CharField(blank=True, default="",
                                                  max_length=20)),
                ("card_brand", models.CharField(blank=True, default="",
                                                max_length=20)),
                ("card_last4", models.CharField(blank=True, default="",
                                                max_length=4)),
                ("card_fingerprint", models.CharField(blank=True, db_index=True,
                    default="", max_length=64,
                    help_text="PSP credential identifier (not PAN) - links same "
                              "card across payments/accounts for CE3.0.")),
                ("card_country", models.CharField(blank=True, default="",
                                                  max_length=2)),
                ("card_issuer", models.CharField(blank=True, default="",
                                                 max_length=120)),
                ("wallet_type", models.CharField(blank=True, default="",
                    max_length=20,
                    help_text="apple_pay/google_pay/'' - wallets have no CVC/AVS "
                              "by design.")),
                ("network_txn_id", models.CharField(blank=True, default="",
                                                   max_length=64)),
                ("source_ref", models.CharField(blank=True, default="",
                    max_length=255,
                    help_text="PSP object id used for backfill.")),
                ("raw", models.JSONField(blank=True, default=dict,
                    help_text="Allow-listed check/result fields only.")),
                ("backfilled_at", models.DateTimeField(
                    default=django.utils.timezone.now)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("payment_intent", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="authentication", to="payments.paymentintent")),
            ],
            options={
                "indexes": [models.Index(fields=["card_fingerprint"],
                                         name="evidence_pa_cfp")],
            },
        ),
        migrations.CreateModel(
            name="CancellationRequest",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ("requested_at", models.DateTimeField(
                    help_text="The timestamp that decides cancelled-recurring "
                              "disputes.")),
                ("channel", models.CharField(choices=[
                    ("web", "Self-serve web"), ("email", "Email request"),
                    ("support_ticket", "Support ticket"),
                    ("admin", "Admin action"), ("trial_end", "Trial end")],
                    max_length=20)),
                ("effective_at", models.DateTimeField(
                    help_text="When access actually ends (period end or immediate).")),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("note", models.TextField(blank=True, default="",
                    help_text="Email/ticket reference or staff note.")),
                ("superseded_by", models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL, to="evidence.cancellationrequest")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="requested_cancellations",
                    to=settings.AUTH_USER_MODEL)),
                ("subscription", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="cancellation_requests",
                    to="subscriptions.subscription")),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="cancellation_requests",
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "indexes": [models.Index(fields=["subscription", "requested_at"],
                                         name="evidence_cr_sub_ts"),
                            models.Index(fields=["user", "requested_at"],
                                         name="evidence_cr_user_ts")],
            },
        ),
        migrations.CreateModel(
            name="MembershipConfirmation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ("platform", models.CharField(max_length=20)),
                ("external_id", models.CharField(max_length=128)),
                ("confirmed_at", models.DateTimeField(
                    help_text="First observed membership.")),
                ("last_seen_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("assignment", models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="confirmations",
                    to="bot_integration.userchannelassignment")),
                ("source_snapshot", models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    to="bot_integration.channelmembershipsnapshot")),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="membership_confirmations",
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "constraints": [models.UniqueConstraint(fields=("user", "platform",
                    "external_id"), name="membership_conf_unique")],
            },
        ),
    ]
