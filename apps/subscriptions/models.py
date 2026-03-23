"""
Subscription models for SaaS billing - Phase 3B.
"""
import uuid
from decimal import Decimal

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

    # Feature flags
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

    # Phase 3B: Upgrade priority for upgrade path
    upgrade_priority = models.PositiveSmallIntegerField(
        default=0,
        help_text=_("Higher number = better plan. Used for upgrade validation.")
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

    def can_upgrade_to(self, target_plan):
        """Check if this plan can be upgraded to target plan."""
        return target_plan.upgrade_priority > self.upgrade_priority


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
        return f"{self.plan.name} - {self.interval} ({self.formatted_price})"

    @property
    def price_dollars(self) -> float:
        return self.price_cents / 100

    @property
    def formatted_price(self) -> str:
        """
        Return formatted price with proper currency symbol.

        Example:
            - USD: "$9.99"
            - INR: "₹332.32"
            - JPY: "¥1000"
        """
        from apps.subscriptions.utils.currency_utils import format_currency
        return format_currency(self.price_cents, self.currency)


class PlanDiscount(models.Model):
    """Discount codes and promotions for plans."""

    class DiscountType(models.TextChoices):
        PERCENTAGE = "percentage", _("Percentage")
        FIXED_AMOUNT = "fixed_amount", _("Fixed Amount")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    code = models.CharField(
        max_length=50,
        unique=True,
        help_text=_("Discount code (e.g., SUMMER20)")
    )
    description = models.CharField(
        max_length=255,
        blank=True,
        help_text=_("Description of the discount")
    )

    discount_type = models.CharField(
        max_length=20,
        choices=DiscountType.choices,
        help_text=_("Type of discount")
    )
    discount_value = models.PositiveIntegerField(
        help_text=_("Discount value (percentage or cents)")
    )

    # Optional: limit to specific plans
    applicable_plans = models.ManyToManyField(
        Plan,
        blank=True,
        related_name="discounts",
        help_text=_("Plans this discount applies to (empty = all plans)")
    )

    # Usage limits
    max_uses = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=_("Maximum number of times this discount can be used (null = unlimited)")
    )
    use_count = models.PositiveIntegerField(
        default=0,
        help_text=_("Number of times this discount has been used")
    )

    # Validity period
    valid_from = models.DateTimeField(default=timezone.now)
    valid_until = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When this discount expires (null = never)")
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("plan discount")
        verbose_name_plural = _("plan discounts")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        if self.discount_type == self.DiscountType.PERCENTAGE:
            return f"{self.code} ({self.discount_value}%)"
        else:
            return f"{self.code} (${self.discount_value / 100:.2f})"

    def is_valid(self):
        """Check if discount is currently valid."""
        if not self.is_active:
            return False
        if self.max_uses is not None and self.use_count >= self.max_uses:
            return False
        if self.valid_until and timezone.now() > self.valid_until:
            return False
        return True

    def apply_discount(self, price_cents):
        """Apply discount to a price and return discounted price."""
        if self.discount_type == self.DiscountType.PERCENTAGE:
            discount = int(price_cents * (self.discount_value / 100))
            return max(0, price_cents - discount)
        else:  # FIXED_AMOUNT
            return max(0, price_cents - self.discount_value)

    def increment_use(self):
        """Increment the use counter."""
        self.use_count += 1
        self.save(update_fields=["use_count"])


class Subscription(models.Model):
    """User subscription to a plan."""

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        CANCELED = "canceled", _("Canceled")
        EXPIRED = "expired", _("Expired")
        PENDING = "pending", _("Pending")
        TRIAL = "trial", _("Trial")

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

    # Phase 3B: Track applied discount
    applied_discount = models.ForeignKey(
        PlanDiscount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subscriptions",
        help_text=_("Discount applied to this subscription")
    )

    # Phase 3B: Track if this was an admin grant
    is_admin_grant = models.BooleanField(
        default=False,
        help_text=_("Whether this subscription was granted by an admin")
    )
    granted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="granted_subscriptions",
        help_text=_("Admin who granted this subscription")
    )
    granted_reason = models.TextField(
        blank=True,
        help_text=_("Reason for admin grant")
    )

    # Phase 3B: Trial tracking
    is_trial = models.BooleanField(
        default=False,
        help_text=_("Whether this is a trial subscription")
    )
    trial_days = models.PositiveSmallIntegerField(
        default=0,
        help_text=_("Number of trial days")
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

    # Payment tracking
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

    # Phase 3B: Prorated credit tracking for upgrades
    prorated_credit_cents = models.PositiveIntegerField(
        default=0,
        help_text=_("Credit from previous subscription applied to this one (cents)")
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("subscription")
        verbose_name_plural = _("subscriptions")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.user.username} - {self.plan.name} ({self.status})"

    def clean(self):
        """Validate subscription state."""
        if self.is_active and self.status not in [self.Status.ACTIVE, self.Status.TRIAL]:
            raise ValidationError(
                _("Only active/trial status subscriptions can be marked is_active=True")
            )

    def save(self, *args, **kwargs):
        """Override save to enforce one active subscription per user."""
        if self.is_active and self.status in [self.Status.ACTIVE, self.Status.TRIAL]:
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

    def cancel(self, reason=""):
        """Cancel this subscription."""
        self.status = self.Status.CANCELED
        self.is_active = False
        self.canceled_at = timezone.now()
        self.save(update_fields=["status", "is_active", "canceled_at"])

        SubscriptionHistory.objects.create(
            subscription=self,
            user=self.user,
            event_type=SubscriptionHistory.EventType.CANCELED,
            previous_status=self.Status.ACTIVE,
            new_status=self.Status.CANCELED,
            notes=reason
        )

    def calculate_prorated_credit(self):
        """Calculate prorated credit for upgrade."""
        if not self.expires_at or not self.plan_price:
            return 0

        now = timezone.now()
        if now >= self.expires_at:
            return 0

        total_duration = (self.expires_at - self.started_at).total_seconds()
        remaining_duration = (self.expires_at - now).total_seconds()

        if total_duration <= 0:
            return 0

        remaining_ratio = remaining_duration / total_duration
        credit = int(self.plan_price.price_cents * remaining_ratio)

        return credit

    def can_upgrade(self, target_plan):
        """Check if this subscription can be upgraded to target plan."""
        if self.user.is_banned:
            return False
        if not self.is_active:
            return False
        return self.plan.can_upgrade_to(target_plan)


class GiftSubscription(models.Model):
    """Gift subscriptions that can be redeemed by users."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        REDEEMED = "redeemed", _("Redeemed")
        EXPIRED = "expired", _("Expired")
        CANCELED = "canceled", _("Canceled")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Gift details
    code = models.CharField(
        max_length=50,
        unique=True,
        help_text=_("Unique gift code")
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        related_name="gift_subscriptions",
        help_text=_("Plan being gifted")
    )
    duration_days = models.PositiveIntegerField(
        default=30,
        help_text=_("Duration of the gifted subscription")
    )

    # Sender info
    sender = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="sent_gifts",
        help_text=_("User who sent the gift")
    )
    recipient_email = models.EmailField(
        blank=True,
        help_text=_("Email of intended recipient (optional)")
    )
    message = models.TextField(
        blank=True,
        help_text=_("Personal message from sender")
    )

    # Redemption
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING
    )
    redeemed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="redeemed_gifts",
        help_text=_("User who redeemed this gift")
    )
    redeemed_at = models.DateTimeField(null=True, blank=True)
    created_subscription = models.ForeignKey(
        Subscription,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gift_source",
        help_text=_("Subscription created from this gift")
    )

    # Expiration
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When this gift code expires")
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("gift subscription")
        verbose_name_plural = _("gift subscriptions")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Gift: {self.plan.name} from {self.sender.username}"

    def is_valid(self):
        """Check if gift is still valid for redemption."""
        if self.status != self.Status.PENDING:
            return False
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        return True

    def redeem(self, user):
        """Redeem this gift for a user."""
        if not self.is_valid():
            raise ValidationError("Gift is not valid or has expired")

        if user.is_banned:
            raise ValidationError("Banned users cannot redeem gifts")

        # Create subscription
        subscription = Subscription.objects.create(
            user=user,
            plan=self.plan,
            status=Subscription.Status.ACTIVE,
            is_active=True,
            started_at=timezone.now(),
            expires_at=timezone.now() + timezone.timedelta(days=self.duration_days)
        )

        self.status = self.Status.REDEEMED
        self.redeemed_by = user
        self.redeemed_at = timezone.now()
        self.created_subscription = subscription
        self.save()

        # Log event
        SubscriptionHistory.objects.create(
            subscription=subscription,
            user=user,
            event_type=SubscriptionHistory.EventType.GIFT_REDEEMED,
            new_plan_id=self.plan.id,
            new_status=subscription.status,
            metadata={"gift_id": str(self.id), "sender_id": str(self.sender.id)}
        )

        return subscription


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
        GIFT_REDEEMED = "gift_redeemed", _("Gift Redeemed")

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

    previous_plan_id = models.UUIDField(null=True, blank=True)
    new_plan_id = models.UUIDField(null=True, blank=True)
    previous_status = models.CharField(max_length=10, blank=True)
    new_status = models.CharField(max_length=10, blank=True)

    metadata = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("subscription history")
        verbose_name_plural = _("subscription histories")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.user.username} - {self.event_type} at {self.created_at}"


class SubscriptionEvent(models.Model):
    """Event system for subscription-related events."""

    class EventType(models.TextChoices):
        SUBSCRIPTION_CREATED = "subscription_created", _("Subscription Created")
        SUBSCRIPTION_UPGRADED = "subscription_upgraded", _("Subscription Upgraded")
        TRIAL_STARTED = "trial_started", _("Trial Started")
        TRIAL_EXPIRED = "trial_expired", _("Trial Expired")
        ADMIN_GRANTED_PLAN = "admin_granted_plan", _("Admin Granted Plan")
        SUBSCRIPTION_CANCELED = "subscription_canceled", _("Subscription Canceled")
        SUBSCRIPTION_EXPIRED = "subscription_expired", _("Subscription Expired")
        GIFT_CREATED = "gift_created", _("Gift Created")
        GIFT_REDEEMED = "gift_redeemed", _("Gift Redeemed")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    event_type = models.CharField(
        max_length=30,
        choices=EventType.choices,
        help_text=_("Type of event")
    )

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="subscription_events",
        help_text=_("User this event relates to")
    )
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="events",
        help_text=_("Subscription this event relates to (optional)")
    )

    # Event data
    data = models.JSONField(
        default=dict,
        help_text=_("Event payload data")
    )

    # Processing state
    processed = models.BooleanField(
        default=False,
        help_text=_("Whether this event has been processed")
    )
    processed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("subscription event")
        verbose_name_plural = _("subscription events")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["event_type", "processed"]),
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} - {self.user.username} at {self.created_at}"

    def mark_processed(self):
        """Mark this event as processed."""
        self.processed = True
        self.processed_at = timezone.now()
        self.save(update_fields=["processed", "processed_at"])

    @classmethod
    def log_event(cls, event_type, user, subscription=None, data=None):
        """Create a new event."""
        return cls.objects.create(
            event_type=event_type,
            user=user,
            subscription=subscription,
            data=data or {}
        )
