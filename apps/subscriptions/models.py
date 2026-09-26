"""
Subscription models for SaaS billing with Trial Support.
"""
import uuid

from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User

from .sanitize import clean_html


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
        # REMOVED: unique=True  ← This was preventing multiple plans per tier
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
    description_html = models.TextField(
        blank=True,
        default="",
        help_text=_("Optional rich HTML shown on the landing page and dashboard card above the feature bullets. Sanitized on save: safe tags only (p, br, ul, ol, li, b, strong, i, em, u, s, a, blockquote, code, h4-h6); scripts and styling are stripped.")
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
        help_text=_("Order for display in plan lists (higher = shown first)")
    )

    is_hidden = models.BooleanField(
        default=False,
        help_text=_("Hidden plans never appear on the landing page, dashboard, or purchase API. Access is granted by admin/ticket approval only.")
    )
    grant_duration_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=_("For approval-granted (hidden) plans: subscription length in days after approval. Leave empty for no expiry.")
    )
    notice_channel = models.CharField(
        max_length=10,
        choices=[("telegram", _("Telegram")), ("email", _("Email")), ("both", _("Both"))],
        default="telegram",
        help_text=_("Where approval/rejection notices are sent for access-request tickets on this plan")
    )
    ticket_approvers = models.ManyToManyField(
        "accounts.User",
        blank=True,
        related_name="approvable_plans",
        limit_choices_to={"is_staff": True},
        help_text=_("Staff allowed to approve/reject access tickets for this plan. Empty = any staff. Superusers can always approve.")
    )

    # TRIAL FIELDS
    is_trial = models.BooleanField(
        default=False,
        help_text=_("Whether this is a one-time trial plan")
    )
    trial_duration_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=_("Duration of trial in days (required if is_trial=True)")
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("plan")
        verbose_name_plural = _("plans")
        ordering = ["display_order", "tier"]
        # ADDED: Unique constraint on tier + is_trial combination
        # This allows: (basic, False) and (basic, True) but not (basic, False) twice
        unique_together = ["tier", "is_trial"]

    def __str__(self) -> str:
        if self.is_trial:
            return f"{self.name} (Trial - {self.trial_duration_days} days)"
        return self.name

    def clean(self):
        """Validate trial configuration."""
        super().clean()
        self.description_html = clean_html(self.description_html)
        if self.is_trial:
            if not self.trial_duration_days:
                raise ValidationError({
                    "trial_duration_days": "Trial duration is required when is_trial=True"
                })
            if self.trial_duration_days < 1:
                raise ValidationError({
                    "trial_duration_days": "Trial duration must be at least 1 day"
                })


class PlanPrice(models.Model):
    """Pricing for a plan at different billing intervals."""

    class Interval(models.TextChoices):
        MONTHLY = "monthly", _("Monthly")
        QUARTERLY = "quarterly", _("Quarterly") 
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


    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def clean(self):
        from django.core.exceptions import ValidationError
        qs = PlanPrice.objects.filter(plan=self.plan, interval=self.interval, is_active=True)
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        if self.is_active and qs.exists():
            raise ValidationError(
                "An active base price already exists for this plan + interval. "
                "Only one active global base price is allowed; geo pricing handles market-specific pricing.")

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


class PlanFeature(models.Model):
    """Landing-page feature bullet for a plan.

    DB-driven replacement for the hardcoded per-tier feature lists in
    LandingPageView._get_features_for_tier. A plan with NO PlanFeature rows
    falls back to the hardcoded tier defaults (see _get_plan_features).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(
        Plan,
        on_delete=models.CASCADE,
        related_name="features",
        help_text=_("The plan this feature bullet belongs to")
    )
    text = models.CharField(
        max_length=255,
        help_text=_("Feature bullet text shown on the pricing card")
    )
    position = models.PositiveSmallIntegerField(
        default=0,
        help_text=_("Display order on the card (lower = shown first)")
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("plan feature")
        verbose_name_plural = _("plan features")
        ordering = ["position", "id"]

    def __str__(self) -> str:
        return f"{self.plan.name}: {self.text}"


class GeoPlanPrice(models.Model):
    """Geo-specific pricing for plans - OVERRIDES ONLY."""

    class Interval(models.TextChoices):
        MONTHLY = "monthly", _("Monthly")
        QUARTERLY = "quarterly", _("Quarterly") 
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
    geo_plan_price = models.ForeignKey(
        "subscriptions.GeoPlanPrice",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subscriptions",
        verbose_name="resolved geo price"
    )
    # G4: promotional provenance (base -> discount -> final actually purchased).
    base_price_cents = models.PositiveIntegerField(default=0)
    discount_cents = models.PositiveIntegerField(default=0)
    coupon_code = models.CharField(max_length=50, blank=True, default="")
    price_cents = models.PositiveIntegerField(null=True, blank=True)
    price_currency = models.CharField(max_length=3, null=True, blank=True)
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
    is_trial = models.BooleanField(
        default=False,
        help_text=_("Whether this subscription is a trial")
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
        from django.core.exceptions import ValidationError
        if self.pk:
            orig = Subscription.objects.filter(pk=self.pk).only("price_cents","price_currency").first()
            if orig and (orig.price_cents != self.price_cents or orig.price_currency != self.price_currency):
                raise ValidationError("price_cents/price_currency are an immutable historical snapshot and cannot be changed after creation.")
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


class UserTrialUsage(models.Model):
    """
    Tracks one-time trial usage per user per plan.
    Ensures users can only claim a trial plan once.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="trial_usages",
        help_text=_("User who used the trial")
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.CASCADE,
        related_name="trial_usages",
        help_text=_("Trial plan that was used")
    )
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trial_usage_record",
        help_text=_("Subscription created from this trial")
    )
    used_at = models.DateTimeField(
        auto_now_add=True,
        help_text=_("When the trial was claimed")
    )
    expires_at = models.DateTimeField(
        help_text=_("When the trial expires")
    )

    class Meta:
        verbose_name = _("user trial usage")
        verbose_name_plural = _("user trial usages")
        unique_together = ["user", "plan"]
        ordering = ["-used_at"]

    def __str__(self) -> str:
        return f"{self.user.username} - {self.plan.name} Trial"

    @property
    def is_expired(self) -> bool:
        """Check if trial has expired."""
        return timezone.now() > self.expires_at


class SubscriptionHistory(models.Model):
    """Audit log of subscription changes."""

    class EventType(models.TextChoices):
        CREATED = "created", _("Created")
        ACTIVATED = "activated", _("Activated")
        RENEWED = "renewed", _("Renewed")
        CANCELED = "canceled", _("Canceled")
        EXPIRED = "expired", _("Expired")
        PENDING = "pending", _("Pending")
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


class SubscriptionReminder(models.Model):
    """Dedupe guard for transactional expiry reminders (Stage 3).

    One row per subscription+kind. A row exists only after at least one
    channel (Telegram / email) delivered — the periodic job deletes an
    unclaimed-row when no channel could deliver so a later run retries.
    TRANSACTIONAL, not marketing: no consent gate is or may be applied.
    """

    class Kind(models.TextChoices):
        PRE_EXPIRY = "pre_expiry", _("Pre-expiry")
        POST_EXPIRY = "post_expiry", _("Post-expiry")

    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="reminders")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    sent_at = models.DateTimeField(auto_now_add=True)
    telegram_message_id = models.BigIntegerField(null=True, blank=True)
    email_sent = models.BooleanField(default=False)
    email_recipient = models.EmailField(blank=True, default="")

    class Meta:
        verbose_name = _("subscription reminder")
        constraints = [
            models.UniqueConstraint(
                fields=["subscription", "kind"], name="uniq_reminder_per_kind"),
        ]

    def __str__(self) -> str:
        return f"{self.subscription_id}:{self.kind}"
