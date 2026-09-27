"""Immutable/append-only evidence models.

CheckoutEvidence    - write-once capture of the agreement moment (1:1 PaymentIntent)
PaymentAuthentication - write-once provider auth result (1:1 PaymentIntent)
CancellationRequest - write-once record of WHO asked to cancel, WHEN, via WHICH channel
MembershipConfirmation - bot-observed community join (distinct from grant/invite)

Design notes:
* Layer 1 (immutable): no update/delete code paths; admin read-only.
* Clear distinctions: ACCESS GRANTED (UserChannelAssignment.assigned_at),
  INVITATION SENT (UserChannelAssignment.last_invite_sent_at),
  MEMBERSHIP CONFIRMED (MembershipConfirmation.confirmed_at),
  ACCESS REVOKED (UserChannelAssignment.revoked_at) - all from the existing
  ledger plus this one confirmation record. No second access ledger.
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class ImmutableModel(models.Model):
    """Base: write-once rows; any later .save() raises."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk and not kwargs.get("force_insert"):
            raise RuntimeError(
                f"{self.__class__.__name__} is immutable and cannot be updated.")
        super().save(*args, **kwargs)


def _session_ref(session_key: str) -> str:
    """Salted hash of the session key - never store the raw key."""
    import hashlib
    from django.conf import settings as dj_settings
    salt = getattr(dj_settings, "RISK_DEVICE_SALT",
                   getattr(dj_settings, "SECRET_KEY", ""))
    return hashlib.sha256(f"{salt}:{session_key}".encode()).hexdigest()


class CheckoutEvidence(ImmutableModel):
    """The agreement moment: what was shown, what was accepted, from where."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment_intent = models.OneToOneField(
        "payments.PaymentIntent", on_delete=models.CASCADE,
        related_name="checkout_evidence")
    ip_address = models.GenericIPAddressField(
        help_text="Clear-text IP (required format for Visa CE3.0 evidence). "
                  "Restricted access; see privacy docs.")
    user_agent = models.TextField()
    device_fingerprint = models.CharField(
        max_length=64, db_index=True,
        help_text="sha256 (>=20 chars) of canonicalized browser signals. "
                  "Evidence-oriented identifier; reproducible per FINGERPRINT_VERSION.")
    fingerprint_version = models.CharField(max_length=10, default="1")
    device_signals = models.JSONField(
        default=dict, blank=True,
        help_text="Canonicalized non-sensitive signals used for the fingerprint "
                  "(UA, language, timezone, screen, platform, concurrency).")
    risk_device_id = models.CharField(
        max_length=64, db_index=True, blank=True, default="",
        help_text="Salted HMAC of the fingerprint - internal continuity id, "
                  "not network-facing.")
    accept_language = models.CharField(max_length=64, blank=True, default="")
    session_ref = models.CharField(max_length=64, db_index=True)
    pricing_snapshot = models.JSONField(default=dict, help_text=(
        "Point-in-time commercial agreement: plan name/description shown, "
        "interval, price, currency, country, trial info, coupon/referral."))
    terms_version = models.ForeignKey(
        "policies.PolicyVersion", on_delete=models.PROTECT,
        related_name="checkout_terms_evidence")
    refund_policy_version = models.ForeignKey(
        "policies.PolicyVersion", on_delete=models.PROTECT,
        related_name="checkout_refund_evidence")
    risk_disclaimer_version = models.ForeignKey(
        "policies.PolicyVersion", on_delete=models.PROTECT,
        related_name="checkout_risk_evidence")
    accepted_at = models.DateTimeField(help_text="Server timestamp of agreement.")
    client_ts = models.DateTimeField(null=True, blank=True,
                                     help_text="Client-reported time (informational).")
    checkout_version = models.CharField(max_length=20, default="1")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["device_fingerprint"]),
                   models.Index(fields=["ip_address"])]

    def __str__(self):
        return f"CheckoutEvidence({self.payment_intent_id})"

    @staticmethod
    def session_ref_for(session_key: str) -> str:
        return _session_ref(session_key or "")


class PaymentAuthentication(ImmutableModel):
    """Provider-reported authentication result; backfilled once post-payment."""

    class ThreeDSResult(models.TextChoices):
        ATTEMPTED = "attempted", "Attempted"
        AUTHENTICATED = "authenticated", "Authenticated"
        LIABILITY_SHIFT = "liability_shift", "Liability shift"
        LIABILITY_SHIFT_UNSUCCESSFUL = "liability_shift_unsuccessful",             "Liability shift (unsuccessful)"
        FAILED = "failed", "Failed"
        NOT_REQUESTED = "not_requested", "Not requested"
        UNKNOWN = "unknown", "Unknown"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment_intent = models.OneToOneField(
        "payments.PaymentIntent", on_delete=models.CASCADE,
        related_name="authentication")
    three_ds_result = models.CharField(
        max_length=30, choices=ThreeDSResult.choices,
        default=ThreeDSResult.UNKNOWN)
    eci = models.CharField(max_length=4, blank=True, default="",
                           help_text="May be unavailable via PSP API; never invent.")
    cavv_present = models.BooleanField(default=False)
    cvc_check = models.CharField(max_length=20, blank=True, default="")
    avs_line1_check = models.CharField(max_length=20, blank=True, default="")
    avs_zip_check = models.CharField(max_length=20, blank=True, default="")
    card_brand = models.CharField(max_length=20, blank=True, default="")
    card_last4 = models.CharField(max_length=4, blank=True, default="")
    card_fingerprint = models.CharField(
        max_length=64, blank=True, default="", db_index=True,
        help_text="PSP credential identifier (not PAN) - links same card across "
                  "payments/accounts for CE3.0 'same payment credential'.")
    card_country = models.CharField(max_length=2, blank=True, default="")
    card_issuer = models.CharField(max_length=120, blank=True, default="")
    wallet_type = models.CharField(max_length=20, blank=True, default="",
                                   help_text="apple_pay/google_pay/'' - wallets "
                                             "have no CVC/AVS by design.")
    network_txn_id = models.CharField(max_length=64, blank=True, default="")
    source_ref = models.CharField(max_length=255, blank=True, default="",
                                  help_text="PSP object id used for backfill.")
    raw = models.JSONField(default=dict, blank=True,
                           help_text="Allow-listed check/result fields only.")
    backfilled_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["card_fingerprint"])]

    def __str__(self):
        return f"Auth({self.payment_intent_id}, 3ds={self.three_ds_result})"


class CancellationRequest(ImmutableModel):
    """WHO asked to cancel, WHEN, via WHICH channel - decides 13.2/13.7 cases."""

    class Channel(models.TextChoices):
        WEB = "web", "Self-serve web"
        EMAIL = "email", "Email request"
        SUPPORT_TICKET = "support_ticket", "Support ticket"
        ADMIN = "admin", "Admin action"
        TRIAL_END = "trial_end", "Trial end"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             on_delete=models.CASCADE,
                             related_name="cancellation_requests")
    subscription = models.ForeignKey(
        "subscriptions.Subscription", on_delete=models.CASCADE,
        related_name="cancellation_requests")
    requested_at = models.DateTimeField(
        help_text="The timestamp that decides cancelled-recurring disputes.")
    channel = models.CharField(max_length=20, choices=Channel.choices)
    effective_at = models.DateTimeField(
        help_text="When access actually ends (period end or immediate).")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL,
                              related_name="requested_cancellations")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    note = models.TextField(blank=True, default="",
                            help_text="Email/ticket reference or staff note.")
    superseded_by = models.ForeignKey("self", null=True, blank=True,
                                      on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["subscription", "requested_at"]),
                   models.Index(fields=["user", "requested_at"])]

    def __str__(self):
        return f"CancelReq({self.user}, {self.requested_at:%Y-%m-%d})"


class MembershipConfirmation(models.Model):
    """Bot-observed community membership - completes grant -> join -> revoke trail.

    Written ONLY from observed ChannelMembershipSnapshot data (never inferred).
    One current record per (user, platform, external_id).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             on_delete=models.CASCADE,
                             related_name="membership_confirmations")
    platform = models.CharField(max_length=20)          # telegram / discord
    external_id = models.CharField(max_length=128)      # chat/channel id
    assignment = models.ForeignKey(
        "bot_integration.UserChannelAssignment", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="confirmations")
    confirmed_at = models.DateTimeField(help_text="First observed membership.")
    last_seen_at = models.DateTimeField(null=True, blank=True)
    source_snapshot = models.ForeignKey(
        "bot_integration.ChannelMembershipSnapshot", null=True, blank=True,
        on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "platform", "external_id"],
                name="membership_conf_unique"),
        ]

    def __str__(self):
        return f"Confirmed({self.user}, {self.platform}:{self.external_id})"


class PlanRefundTerms(models.Model):
    """LIVE refund configuration for a plan (including TRIAL plans).

    Editable by staff; NEVER evidence by itself. At each checkout the
    applicable terms are FROZEN into CheckoutEvidence.pricing_snapshot and
    the refund/cancellation policy VERSIONS are FK'd onto the evidence row -
    so every refund decision is evaluated against the terms that applied
    to THAT transaction, not today's configuration.

    window semantics: days from payment (trial window may differ; null =
    fall back to the standard window; 0 = not refundable).
    """
    plan = models.OneToOneField("subscriptions.Plan", on_delete=models.CASCADE,
                                related_name="refund_terms")
    refund_window_days = models.PositiveIntegerField(
        default=0, help_text="Standard refund window in days from payment. "
                             "0 = not refundable.")
    trial_refund_window_days = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Trial-specific window; null = use refund_window_days.")
    cancellation_deadline_hours = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="For trials: cancel before conversion within N hours of "
                  "trial end; null = no special deadline.")
    refund_terms_text = models.TextField(
        blank=True, default="",
        help_text="Plain-language terms shown at checkout and snapshotted.")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Refund terms: {self.plan.name}"

    def window_for(self, is_trial: bool):
        if is_trial and self.trial_refund_window_days is not None:
            return self.trial_refund_window_days
        return self.refund_window_days
