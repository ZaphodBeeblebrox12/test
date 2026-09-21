"""
Minimal PaymentIntent model for simple payment flow.
"""
import uuid

from django.db import models
from django.utils import timezone
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
        User,
        on_delete=models.CASCADE,
        related_name="payment_intents",
        help_text=_("User making the payment")
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.CASCADE,
        related_name="payment_intents",
        help_text=_("Plan being purchased")
    )
    plan_price = models.ForeignKey(
        PlanPrice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_intents",
        help_text=_("Price selected for the plan")
    )
    # G4: original base price BEFORE referral/coupon discounts (provenance).
    base_amount_cents = models.PositiveIntegerField(default=0)
    amount = models.PositiveIntegerField(
        help_text=_("Amount in cents")
    )
    currency = models.CharField(
        max_length=3,
        default="USD",
        help_text=_("ISO 4217 currency code")
    )
    provider = models.CharField(
        max_length=20,
        choices=Provider.choices,
        help_text=_("Payment provider")
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        help_text=_("Payment status")
    )
    country = models.CharField(
        max_length=2,
        blank=True,
        help_text=_("Country code used for pricing")
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # NEW: track which referral's discount was applied to this payment
    geo_plan_price = models.ForeignKey(
        "subscriptions.GeoPlanPrice",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="payment_intents",
        help_text="Snapshot of the regional price used for this payment (null when a global PlanPrice applied).",
    )
    # G4: coupon provenance (which code, how much off the original price).
    applied_coupon_code = models.CharField(max_length=50, blank=True, default="")
    coupon_discount_cents = models.PositiveIntegerField(default=0)
    provider_reference = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Provider checkout/order/session id (Stripe Checkout Session id or Razorpay Order id).",
    )

    applied_referral_discount = models.ForeignKey(
        "growth.Referral",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_intents",
        help_text=_("Referral whose discount was applied to this payment")
    )

    class Meta:
        verbose_name = _("payment intent")
        verbose_name_plural = _("payment intents")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.user.username} - {self.plan.name} ({self.status})"

    @property
    def amount_dollars(self) -> float:
        return self.amount / 100


class WebhookEvent(models.Model):
    """Durable record of a received provider webhook (P4).

    Receive (HTTP) is separated from process (durable job).  The
    (provider, provider_event_id) pair is unique so duplicated deliveries are
    stored once and processed at most once.  Raw payload is kept minimal and
    is never used as a trust source for business fields."""

    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        PROCESSED = "processed", "Processed"
        FAILED = "failed", "Failed"
        IGNORED = "ignored", "Ignored"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=20, choices=PaymentIntent.Provider.choices)
    provider_event_id = models.CharField(max_length=255)
    event_type = models.CharField(max_length=100)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RECEIVED)
    error = models.TextField(blank=True, default="")
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_event_id"],
                name="uniq_provider_event",
            )
        ]
        indexes = [models.Index(fields=["status", "provider"], name="pay_wh_status_idx")]
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.provider}:{self.event_type}:{self.provider_event_id}"
