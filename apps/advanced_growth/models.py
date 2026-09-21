"""G6 advanced growth: affiliates, experiments, multi-channel, announcements.

Composes G0-G5: Events (triggers/metrics), G2 Campaign (orchestration),
G3 AutomationRule (lifecycle), G4 Promotion (offers), G5 Attribution
(measurement).  Telegram marketing is a CHANNEL here and never touches
entitlement/provisioning (the Provision Contract is unchanged).

Affiliate commission is a Growth ACCOUNTING record -- never alters
PaymentIntent amount or Subscription entitlement.
"""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


# ============================== AFFILIATES ==============================

class Affiliate(models.Model):
    """A creator/affiliate.  Reuses the existing User; adds payout + status."""
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="affiliate")
    code = models.CharField(max_length=50, unique=True)  # unique affiliate code/link
    commission_percent = models.PositiveIntegerField(
        default=10, help_text="Percent of attributed purchase paid as commission.")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    payout_email = models.EmailField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user}: {self.code}"


class AffiliateAttribution(models.Model):
    """Click -> signup -> purchase attribution, with provenance.

    Precedence (explicit): a referred user's FIRST affiliate wins (first-touch
    for affiliate); does not overwrite a G5 SignupAttribution UTM."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    affiliate = models.ForeignKey(Affiliate, on_delete=models.CASCADE, related_name="attributions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="affiliate_attributions")
    utm_source = models.CharField(max_length=100, blank=True, default="affiliate")
    utm_campaign = models.CharField(max_length=100, blank=True, default="")
    clicked_at = models.DateTimeField(null=True, blank=True)
    signed_up_at = models.DateTimeField(null=True, blank=True)
    purchased_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # A user is attributed to ONE affiliate (first-touch wins) per purchase path.
        constraints = [models.UniqueConstraint(fields=["affiliate", "user"],
                                               name="uniq_affiliate_user")]

    def __str__(self):
        return f"{self.affiliate.code}:{self.user_id}"


class AffiliateCommission(models.Model):
    """Commission accounting record.  PENDING->APPROVED->PAYABLE->PAID/REVERSED.
    Idempotent: unique (affiliate, payment_intent) = one commission per sale."""
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        PAYABLE = "payable", "Payable"
        PAID = "paid", "Paid"
        REVERSED = "reversed", "Reversed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    affiliate = models.ForeignKey(Affiliate, on_delete=models.CASCADE, related_name="commissions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="affiliate_commissions")
    payment_intent = models.ForeignKey("payments.PaymentIntent", null=True, blank=True,
                                       on_delete=models.SET_NULL, related_name="affiliate_commissions")
    amount_cents = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("affiliate", "payment_intent")]

    def __str__(self):
        return f"{self.affiliate.code}:{self.amount_cents}"


# ============================== EXPERIMENTS ==============================

class Experiment(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        RUNNING = "running", "Running"
        PAUSED = "paused", "Paused"
        COMPLETED = "completed", "Completed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    key = models.SlugField(unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    # Which template variant to render per assignment is resolved at send time.
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class ExperimentVariant(models.Model):
    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="variants")
    key = models.CharField(max_length=50)
    name = models.CharField(max_length=120)
    allocation_percent = models.PositiveIntegerField(
        default=50, help_text="Percent of eligible traffic assigned to this variant.")
    template_version = models.ForeignKey("emailing.TemplateVersion", null=True, blank=True,
                                         on_delete=models.SET_NULL, related_name="experiment_variants")

    class Meta:
        unique_together = [("experiment", "key")]

    def __str__(self):
        return f"{self.experiment.key}:{self.key}"


class ExperimentAssignment(models.Model):
    """Stable, deterministic assignment: unique (experiment, user) = a user
    never moves between variants.  Recomputed via a hash of experiment+user."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="assignments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="experiment_assignments")
    variant = models.ForeignKey(ExperimentVariant, on_delete=models.PROTECT,
                                related_name="assignments")
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("experiment", "user")]

    def __str__(self):
        return f"{self.experiment.key}:{self.user_id}->{self.variant.key}"


class ExperimentConversion(models.Model):
    """A conversion attributed to an assignment (idempotent per assignment+event)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assignment = models.ForeignKey(ExperimentAssignment, on_delete=models.CASCADE,
                                   related_name="conversions")
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="experiment_conversions")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("assignment", "event")]

    def __str__(self):
        return f"{self.assignment_id}:{self.event_id}"


# ============================== MULTI-CHANNEL ==============================

class Channel(models.Model):
    """A delivery channel.  Email uses G1 Delivery; Telegram/in-product have
    their own delivery records.  Provisioning is NOT a channel here."""
    class Kind(models.TextChoices):
        EMAIL = "email", "Email"
        TELEGRAM = "telegram", "Telegram (marketing)"
        IN_PRODUCT = "in_product", "In-product"

    kind = models.CharField(max_length=20, choices=Kind.choices, unique=True)
    name = models.CharField(max_length=60)

    def __str__(self):
        return self.name


class CampaignAction(models.Model):
    """One channel-specific action within a G2 Campaign.  Keeps Campaign as
    the orchestration concept; each action delivers via its own channel."""
    class State(models.TextChoices):
        QUEUED = "queued", "Queued"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey("campaigns.Campaign", on_delete=models.CASCADE, related_name="actions")
    channel = models.ForeignKey(Channel, on_delete=models.PROTECT, related_name="actions")
    template_version = models.ForeignKey("emailing.TemplateVersion", null=True, blank=True,
                                         on_delete=models.SET_NULL,
                                         help_text="For email/announcement actions.")
    announcement = models.ForeignKey("advanced_growth.Announcement", null=True, blank=True,
                                     on_delete=models.SET_NULL, help_text="For in-product actions.")
    state = models.CharField(max_length=20, choices=State.choices, default=State.QUEUED)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.campaign}:{self.channel}"


class TelegramMarketingDelivery(models.Model):
    """Marketing message delivery record.  SEPARATE from entitlement/provisioning:
    grants/revokes/membership stay in bot_integration; this is Growth-only."""
    class State(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        SUPPRESSED = "suppressed", "Suppressed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign_action = models.ForeignKey(CampaignAction, null=True, blank=True,
                                        on_delete=models.SET_NULL, related_name="telegram_deliveries")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="telegram_marketing_deliveries")
    idempotency_key = models.CharField(max_length=255, unique=True, db_index=True)
    message = models.TextField()
    state = models.CharField(max_length=20, choices=State.choices, default=State.QUEUED)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"tg-mkt:{self.user_id}:{self.idempotency_key}"


# ============================== ANNOUNCEMENTS ==============================

class Announcement(models.Model):
    """In-product message with targeting, scheduling, dismissal, frequency."""
    class Kind(models.TextChoices):
        BANNER = "banner", "Banner"
        MODAL = "modal", "Modal"
        NOTIFICATION = "notification", "Notification"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200)
    body = models.TextField()
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.BANNER)
    priority = models.PositiveIntegerField(default=0, help_text="Higher shows first.")
    # Targeting: structured rules over the User (like G2 Audience).
    audience_rules = models.JSONField(default=dict, blank=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True,
                                   help_text="An announcement is NOT shown indefinitely unless ends_at is empty AND no frequency cap.")
    max_shows_per_user = models.PositiveIntegerField(
        default=3, help_text="Frequency cap: stop showing after this many exposures.")
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class AnnouncementExposure(models.Model):
    """One impression; unique (announcement, user, idempotency) caps frequency."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="exposures")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="announcement_exposures")
    idempotency_key = models.CharField(max_length=255, default="")
    dismissed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("announcement", "user", "idempotency_key")]

    def __str__(self):
        return f"{self.announcement_id}:{self.user_id}"
