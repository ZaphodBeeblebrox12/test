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

    class Meta:
        verbose_name = _("payment intent")
        verbose_name_plural = _("payment intents")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.user.username} - {self.plan.name} ({self.status})"

    @property
    def amount_dollars(self) -> float:
        return self.amount / 100
