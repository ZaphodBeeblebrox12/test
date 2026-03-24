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
    is_active = models.BooleanField(
        default=True,
        help_text=_("Whether this plan is available for new subscriptions")
    )
    display_order = models.PositiveSmallIntegerField(
        default=0,
        help_text=_("Order for display in plan lists")
    )
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
    plan = models.ForeignKey(
        Plan,
        on_delete=models.CASCADE,
        related_name="prices",
        help_text=_("The plan this price applies to")
    )
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
    is_active = models.BooleanField(
        default=True,
        help_text=_("Whether this price is currently offered")
    )
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
        return self.price_cents / 100


class GeoPlanPrice(models.Model):
    """Geo-specific pricing for plans - OVERRIDES ONLY."""

    class Interval(models.TextChoices):
        MONTHLY = "monthly", _("Monthly")
        YEARLY = "yearly", _("Yearly")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(
        Plan,
        on_delete=models.CASCADE,
        related_name="geo_prices",
        help_text=_("The plan this geo price applies to")
    )
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
    country = models.CharField(
        max_length=2,
        blank=True,
        null=True,
        help_text=_("ISO country code for country-specific override (e.g., 'IN')")
    )
    region = models.CharField(
        max_length=10,
        blank=True,
        null=True,
        help_text=_("Region code for regional override (e.g., 'APAC', 'EU')")
    )
    is_active = models.BooleanField(
        default=True,
        help_text=_("Whether this geo price is currently offered")
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("geo plan price")
        verbose_name_plural = _("geo plan prices")
        unique_together = ["plan", "interval", "country", "region"]
        ordering = ["plan", "interval", "country", "region"]
        constraints = [
            models.CheckConstraint(
                check=~models.Q(country__isnull=True, region__isnull=True),
                name="geo_price_must_have_country_or_region",
                violation_error_message="GeoPlanPrice must have either country or region specified. Use PlanPrice for global pricing.",
            ),
        ]

    def clean(self):
        """Validate that geo price has either country or region."""
        if not self.country and not self.region:
            raise ValidationError({
                "country": "GeoPlanPrice must specify either country or region. Use PlanPrice for global pricing.",
                "region": "GeoPlanPrice must specify either country or region. Use PlanPrice for global pricing.",
            })
        super().clean()

    def __str__(self) -> str:
        price_dollars = self.price_cents / 100
        if self.country:
            return f"{self.plan.name} - {self.interval} [{self.country}] ({self.currency} {price_dollars:.2f})"
        elif self.region:
            return f"{self.plan.name} - {self.interval} [{self.region}] ({self.currency} {price_dollars:.2f})"
        return f"{self.plan.name} - {self.interval} (INVALID - no geo)"

    @property
    def price_dollars(self) -> float:
        return self.price_cents / 100

    @property
    def is_country_specific(self) -> bool:
        return self.country is not None

    @property
    def is_regional_price(self) -> bool:
        return self.country is None and self.region is not None


class Subscription(models.Model):
    """User subscription to a plan."""

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        CANCELED = "canceled", _("Canceled")
        EXPIRED = "expired", _("Expired")
        PENDING = "pending", _("Pending")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
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
    pricing_country = models.CharField(
        max_length=2,
        blank=True,
        null=True,
        help_text=_("Country code used for pricing at subscription time")
    )
    pricing_region = models.CharField(
        max_length=10,
        blank=True,
        null=True,
        help_text=_("Region code used for pricing at subscription time")
    )
    is_gift = models.BooleanField(
        default=False,
        help_text=_("Whether this subscription was granted as a gift")
    )
    gift_from = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gifts_given",
        help_text=_("User who gifted this subscription")
    )
    gift_message = models.TextField(
        blank=True,
        help_text=_("Optional message from gift giver")
    )
    is_admin_grant = models.BooleanField(
        default=False,
        help_text=_("Whether this subscription was granted by admin")
    )
    granted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="admin_grants",
        help_text=_("Admin who granted this subscription")
    )
    grant_reason = models.TextField(
        blank=True,
        help_text=_("Reason for admin grant")
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("subscription")
        verbose_name_plural = _("subscriptions")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.user.username} - {self.plan.name} ({self.status})"

    def clean(self):
        if self.is_active and self.status not in [self.Status.ACTIVE]:
            raise ValidationError(
                _("Only active status subscriptions can be marked is_active=True")
            )

    def save(self, *args, **kwargs):
        if self.is_active and self.status == self.Status.ACTIVE:
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
        TRIAL_STARTED = "trial_started", _("Trial Started")
        TRIAL_EXPIRED = "trial_expired", _("Trial Expired")
        ADMIN_GRANTED = "admin_granted", _("Admin Granted")
        GIFT_RECEIVED = "gift_received", _("Gift Received")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
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
    event_type = models.CharField(
        max_length=20,
        choices=EventType.choices,
        help_text=_("Type of subscription event")
    )
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
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Additional event metadata")
    )
    notes = models.TextField(
        blank=True,
        help_text=_("Human-readable notes about this event")
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("subscription history")
        verbose_name_plural = _("subscription histories")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.subscription.user.username} - {self.event_type} at {self.created_at}"


class UpgradeHistory(models.Model):
    """Record of subscription upgrades with proration details."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="upgrade_history",
        help_text=_("User who upgraded")
    )
    from_subscription = models.ForeignKey(
        Subscription,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="upgrades_from",
        help_text=_("Original subscription")
    )
    to_subscription = models.ForeignKey(
        Subscription,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="upgrades_to",
        help_text=_("New subscription after upgrade")
    )
    from_plan = models.ForeignKey(
        Plan,
        on_delete=models.SET_NULL,
        null=True,
        related_name="upgraded_from",
        help_text=_("Previous plan")
    )
    to_plan = models.ForeignKey(
        Plan,
        on_delete=models.SET_NULL,
        null=True,
        related_name="upgraded_to",
        help_text=_("New plan")
    )
    from_price_cents = models.PositiveIntegerField(help_text=_("Previous price in cents"))
    to_price_cents = models.PositiveIntegerField(help_text=_("New price in cents"))
    prorated_credit_cents = models.PositiveIntegerField(
        default=0,
        help_text=_("Credit applied from previous subscription")
    )
    amount_due_cents = models.PositiveIntegerField(help_text=_("Amount charged for upgrade"))
    pricing_country = models.CharField(
        max_length=2,
        blank=True,
        null=True,
        help_text=_("Country code used for pricing")
    )
    pricing_region = models.CharField(
        max_length=10,
        blank=True,
        null=True,
        help_text=_("Region code used for pricing")
    )
    is_successful = models.BooleanField(
        default=True,
        help_text=_("Whether the upgrade completed successfully")
    )
    error_message = models.TextField(
        blank=True,
        help_text=_("Error message if upgrade failed")
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("upgrade history")
        verbose_name_plural = _("upgrade histories")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.user.username}: {self.from_plan} -> {self.to_plan}"


class GiftSubscription(models.Model):
    """Gift subscription template for giving to other users."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        CLAIMED = "claimed", _("Claimed")
        EXPIRED = "expired", _("Expired")
        CANCELED = "canceled", _("Canceled")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(
        Plan,
        on_delete=models.CASCADE,
        related_name="gift_subscriptions",
        help_text=_("Plan being gifted")
    )
    plan_price = models.ForeignKey(
        PlanPrice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gift_subscriptions",
        help_text=_("Price selected for gift")
    )
    from_user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="gifts_created",
        help_text=_("User giving the gift")
    )
    to_user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="gifts_received",
        help_text=_("User receiving the gift (null until claimed)")
    )
    message = models.TextField(
        blank=True,
        help_text=_("Optional message for recipient")
    )
    gift_code = models.CharField(
        max_length=32,
        unique=True,
        help_text=_("Unique code for claiming the gift")
    )
    duration_days = models.PositiveIntegerField(
        default=30,
        help_text=_("Number of days the gift subscription lasts")
    )
    expires_at = models.DateTimeField(help_text=_("When the gift code expires"))
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING
    )
    claimed_at = models.DateTimeField(null=True, blank=True)
    resulting_subscription = models.ForeignKey(
        Subscription,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gift_origin",
        help_text=_("Subscription created from this gift")
    )
    pricing_country = models.CharField(
        max_length=2,
        blank=True,
        null=True,
        help_text=_("Country code used for pricing at creation")
    )
    pricing_region = models.CharField(
        max_length=10,
        blank=True,
        null=True,
        help_text=_("Region code used for pricing at creation")
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("gift subscription")
        verbose_name_plural = _("gift subscriptions")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Gift from {self.from_user.username} - {self.plan.name}"
