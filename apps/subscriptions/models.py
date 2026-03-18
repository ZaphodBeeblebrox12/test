"""
Subscription models for SaaS billing.
"""
import uuid

from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User


class Plan(models.Model):
    """Subscription plan definition."""

    class Tier(models.TextChoices):
        FREE = "free", _("Free")
        BASIC = "basic", _("Basic")
        PRO = "pro", _("Pro")
        ENTERPRISE = "enterprise", _("Enterprise")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Plan identification
    tier = models.CharField(
        max_length=20,
        choices=Tier.choices,
        unique=True,
        help_text=_("Plan tier level")
    )
    name = models.CharField(
        max_length=100,
        help_text=_("Display name for the plan")
    )
    description = models.TextField(
        blank=True,
        help_text=_("Plan description shown to users")
    )

    # Feature flags (read-only for now)
    max_projects = models.PositiveIntegerField(
        default=0,
        help_text=_("Maximum number of projects allowed")
    )
    max_storage_mb = models.PositiveIntegerField(
        default=0,
        help_text=_("Maximum storage in MB")
    )
    api_calls_per_day = models.PositiveIntegerField(
        default=0,
        help_text=_("API call limit per day")
    )

    # Status
    is_active = models.BooleanField(
        default=True,
        help_text=_("Whether this plan is available for new subscriptions")
    )
    display_order = models.PositiveSmallIntegerField(
        default=0,
        help_text=_("Order for display in plan lists")
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("plan")
        verbose_name_plural = _("plans")
        ordering = ["display_order", "tier"]

    def __str__(self) -> str:
        return self.name


class PlanPrice(models.Model):
    """Pricing for a plan at different billing intervals."""

    class Interval(models.TextChoices):
        MONTHLY = "monthly", _("Monthly")
        YEARLY = "yearly", _("Yearly")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Relationships
    plan = models.ForeignKey(
        Plan,
        on_delete=models.CASCADE,
        related_name="prices",
        help_text=_("The plan this price applies to")
    )

    # Pricing
    interval = models.CharField(
        max_length=10,
        choices=Interval.choices,
        help_text=_("Billing interval")
    )
    price_cents = models.PositiveIntegerField(
        help_text=_("Price in cents (e.g., 999 for $9.99)")
    )
    currency = models.CharField(
        max_length=3,
        default="USD",
        help_text=_("ISO 4217 currency code")
    )

    # Status
    is_active = models.BooleanField(
        default=True,
        help_text=_("Whether this price is currently offered")
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("plan price")
        verbose_name_plural = _("plan prices")
        unique_together = ["plan", "interval", "currency"]
        ordering = ["plan", "interval"]

    def __str__(self) -> str:
        price_dollars = self.price_cents / 100
        return f"{self.plan.name} - {self.interval} (${price_dollars:.2f})"

    @property
    def price_dollars(self) -> float:
        """Return price in dollars."""
        return self.price_cents / 100


class Subscription(models.Model):
    """User subscription to a plan."""

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        CANCELED = "canceled", _("Canceled")
        EXPIRED = "expired", _("Expired")
        PENDING = "pending", _("Pending")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Relationships
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="subscriptions",
        help_text=_("The user who owns this subscription")
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        related_name="subscriptions",
        help_text=_("The subscribed plan")
    )
    plan_price = models.ForeignKey(
        PlanPrice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subscriptions",
        help_text=_("The price/interval selected")
    )

    # Status
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        help_text=_("Current subscription status")
    )
    is_active = models.BooleanField(
        default=False,
        help_text=_("Whether this subscription grants current access")
    )

    # Dates
    started_at = models.DateTimeField(
        default=timezone.now,
        help_text=_("When the subscription started")
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When the subscription expires/ended")
    )
    canceled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When the subscription was canceled")
    )

    # Payment tracking (minimal for Phase 3A)
    payment_provider = models.CharField(
        max_length=50,
        blank=True,
        help_text=_("Payment provider (e.g., stripe, paypal)")
    )
    provider_subscription_id = models.CharField(
        max_length=255,
        blank=True,
        help_text=_("Subscription ID in payment provider")
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("subscription")
        verbose_name_plural = _("subscriptions")
        ordering = ["-created_at"]
        # Database-level constraint to help enforce one active per user
        # Note: Partial unique indexes require PostgreSQL 11+
        # For SQLite/older PG, we rely on application-level enforcement

    def __str__(self) -> str:
        return f"{self.user.username} - {self.plan.name} ({self.status})"

    def clean(self):
        """Validate subscription state."""
        if self.is_active and self.status not in [self.Status.ACTIVE]:
            raise ValidationError(
                _("Only active status subscriptions can be marked is_active=True")
            )

    def save(self, *args, **kwargs):
        """Override save to enforce one active subscription per user."""
        # If marking this as active, deactivate other active subscriptions
        if self.is_active and self.status == self.Status.ACTIVE:
            # Deactivate other active subscriptions for this user
            Subscription.objects.filter(
                user=self.user,
                is_active=True
            ).exclude(pk=self.pk).update(
                is_active=False,
                status=self.Status.CANCELED,
                canceled_at=timezone.now()
            )

        self.full_clean()
        super().save(*args, **kwargs)


class SubscriptionHistory(models.Model):
    """Audit log of subscription changes."""

    class EventType(models.TextChoices):
        CREATED = "created", _("Created")
        ACTIVATED = "activated", _("Activated")
        RENEWED = "renewed", _("Renewed")
        CANCELED = "canceled", _("Canceled")
        EXPIRED = "expired", _("Expired")
        UPGRADED = "upgraded", _("Upgraded")
        DOWNGRADED = "downgraded", _("Downgraded")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Relationships
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        related_name="history",
        help_text=_("The subscription this event relates to")
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="subscription_history",
        help_text=_("The user who owns the subscription")
    )

    # Event details
    event_type = models.CharField(
        max_length=20,
        choices=EventType.choices,
        help_text=_("Type of subscription event")
    )

    # Snapshot of subscription state at event time
    previous_plan_id = models.UUIDField(
        null=True,
        blank=True,
        help_text=_("Previous plan ID (for upgrades/downgrades)")
    )
    new_plan_id = models.UUIDField(
        null=True,
        blank=True,
        help_text=_("New plan ID (for upgrades/downgrades)")
    )
    previous_status = models.CharField(
        max_length=10,
        blank=True,
        help_text=_("Previous subscription status")
    )
    new_status = models.CharField(
        max_length=10,
        blank=True,
        help_text=_("New subscription status")
    )

    # Additional data
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Additional event metadata")
    )
    notes = models.TextField(
        blank=True,
        help_text=_("Human-readable notes about this event")
    )

    # Timestamp
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("subscription history")
        verbose_name_plural = _("subscription histories")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.subscription.user.username} - {self.event_type} at {self.created_at}"
