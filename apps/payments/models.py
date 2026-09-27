"""
Payment models: PaymentIntent (financial snapshot), Refund, WebhookEvent.
"""
import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User
from apps.subscriptions.models import Plan, PlanPrice


class PaymentIntent(models.Model):
    """Simple payment intent for tracking payments."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        SUCCESS = "success", _("Success")
        FAILED = "failed", _("Failed")

    class Provider(models.TextChoices):
        STRIPE = "stripe", _("Stripe")
        RAZORPAY = "razorpay", _("Razorpay")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="payment_intents",
        help_text=_("User making the payment"))
    plan = models.ForeignKey(
        Plan, on_delete=models.CASCADE, related_name="payment_intents",
        help_text=_("Plan being purchased"))
    plan_price = models.ForeignKey(
        PlanPrice, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="payment_intents",
        help_text=_("Price selected for the plan"))
    base_amount_cents = models.PositiveIntegerField(default=0)
    amount = models.PositiveIntegerField(help_text=_("Amount in cents"))
    currency = models.CharField(max_length=3, default="USD",
                                help_text=_("ISO 4217 currency code"))
    provider = models.CharField(max_length=20, choices=Provider.choices,
                                help_text=_("Payment provider"))
    status = models.CharField(max_length=20, choices=Status.choices,
                              default=Status.PENDING,
                              help_text=_("Payment status"))
    country = models.CharField(max_length=2, blank=True,
                               help_text=_("Country code used for pricing"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    geo_plan_price = models.ForeignKey(
        "subscriptions.GeoPlanPrice", null=True, blank=True,
        on_delete=models.PROTECT, related_name="payment_intents",
        help_text="Snapshot of the regional price used for this payment (null when a global PlanPrice applied).")
    applied_coupon_code = models.CharField(max_length=50, blank=True, default="")
    coupon_discount_cents = models.PositiveIntegerField(default=0)
    provider_reference = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Provider checkout/order/session id (Stripe Checkout Session id or Razorpay Order id).")
    applied_referral_discount = models.ForeignKey(
        "growth.Referral", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="payment_intents",
        help_text=_("Referral whose discount was applied to this payment"))
    provider_payment_id = models.CharField(
        max_length=255, blank=True, default="",
        help_text=_("Provider payment/charge id (pi_.. / pay_..) used to link refund/dispute webhooks to this intent."))
    is_upgrade = models.BooleanField(
        default=False,
        help_text=_("Prorated upgrade purchase; on activation writes UpgradeHistory + upgraded history."))
    refunded_cents = models.PositiveIntegerField(
        default=0,
        help_text=_("Cumulative refunded amount in minor units (cents/paise)."))
    refunded_at = models.DateTimeField(null=True, blank=True,
                                       help_text=_("Timestamp of the most recent refund."))
    chargeback = models.BooleanField(
        default=False, help_text=_("A dispute/chargeback has been opened on this payment."))
    chargeback_confirmed = models.BooleanField(
        default=False,
        help_text=_("Dispute confirmed against us (funds withdrawn or dispute lost)."))
    chargeback_reference = models.CharField(max_length=255, blank=True, default="",
                                            help_text=_("Provider dispute id (dp_..)."))
    chargeback_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("payment intent")
        verbose_name_plural = _("payment intents")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.user.username} - {self.plan.name} ({self.status})"

    @property
    def amount_dollars(self) -> float:
        return self.amount / 100

    @property
    def is_partially_refunded(self) -> bool:
        return 0 < self.refunded_cents < self.amount

    @property
    def is_fully_refunded(self) -> bool:
        return self.amount > 0 and self.refunded_cents >= self.amount


class Refund(models.Model):
    """One refunded amount against a PaymentIntent. THE unified ledger —
    trial refunds, standard refunds and upgrade refunds all live here,
    differentiated by `commercial_context` (the same pattern PaymentIntent
    uses with is_upgrade). Never a second trial-only ledger.

    Idempotent per (provider, provider_refund_id): retries of the same
    provider refund store exactly one row. PaymentIntent keeps its original
    SUCCESS state — a refund never rewrites the payment.
    """

    class Source(models.TextChoices):
        WEBHOOK = "webhook", _("Webhook")
        MANUAL = "manual", _("Manual (admin)")

    class CommercialContext(models.TextChoices):
        TRIAL = "trial", _("Trial payment (incl. conversion charge)")
        STANDARD = "standard", _("Standard paid plan")
        UPGRADE = "upgrade", _("Upgrade purchase")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment_intent = models.ForeignKey(
        PaymentIntent, on_delete=models.CASCADE, related_name="refunds",
        help_text=_("The payment this refund applies to."))
    provider = models.CharField(max_length=20,
                                choices=PaymentIntent.Provider.choices)
    provider_refund_id = models.CharField(
        max_length=255,
        help_text=_("Provider refund id (rf_.. / rfd_..); 'manual:<uuid>' for admin-recorded refunds."))
    amount_cents = models.PositiveIntegerField(
        help_text=_("Refunded amount in minor units (cents/paise)."))
    currency = models.CharField(max_length=3, default="USD")
    source = models.CharField(max_length=20, choices=Source.choices,
                              default=Source.WEBHOOK)
    # Commercial context of the refunded payment. Stamped at creation from
    # the payment's CHECKOUT EVIDENCE snapshot (transaction-specific), never
    # from today's plan configuration. Refund rules are evaluated against
    # this context + the snapshot, not the live plan.
    commercial_context = models.CharField(
        max_length=20, choices=CommercialContext.choices,
        default=CommercialContext.STANDARD)
    refunded_at = models.DateTimeField(help_text=_("When the provider processed the refund."))
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("refund")
        verbose_name_plural = _("refunds")
        ordering = ["-refunded_at"]
        constraints = [
            models.UniqueConstraint(fields=["provider", "provider_refund_id"],
                                    name="uniq_provider_refund"),
        ]
        indexes = [models.Index(fields=["commercial_context", "refunded_at"],
                                name="pay_refund_ctx_ts")]

    def __str__(self):
        return (f"{self.provider}:{self.provider_refund_id} "
                f"({self.currency} {self.amount_cents / 100:.2f}, {self.commercial_context})")

    @property
    def amount_dollars(self) -> float:
        return self.amount_cents / 100


class WebhookEvent(models.Model):
    """Durable record of a received provider webhook (P4)."""

    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        PROCESSED = "processed", "Processed"
        FAILED = "failed", "Failed"
        IGNORED = "ignored", "Ignored"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=20,
                                choices=PaymentIntent.Provider.choices)
    provider_event_id = models.CharField(max_length=255)
    event_type = models.CharField(max_length=100)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices,
                              default=Status.RECEIVED)
    error = models.TextField(blank=True, default="")
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "provider_event_id"],
                                    name="uniq_provider_event"),
        ]
        indexes = [models.Index(fields=["status", "provider"],
                                name="pay_wh_status_idx")]
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.provider}:{self.event_type}:{self.provider_event_id}"
