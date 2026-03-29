"""
Growth models for gift invites, pending claims, referral tracking, and rewards.
"""
import uuid
import secrets
import hashlib
import string
from decimal import Decimal

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator

# ============================================================================
# GIFT MODELS (Existing - preserved)
# ============================================================================

class GiftInvite(models.Model):
    """Token-based gift invitation."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        CLAIMED = "claimed", _("Claimed")
        EXPIRED = "expired", _("Expired")
        REVOKED = "revoked", _("Revoked")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    gift_subscription = models.OneToOneField(
        "subscriptions.GiftSubscription",
        on_delete=models.CASCADE,
        related_name="gift_invite",
        help_text=_("The gift subscription this invite is for")
    )
    recipient_email = models.EmailField(help_text=_("Email address of the intended recipient"))
    recipient_email_hash = models.CharField(max_length=64, db_index=True)
    claim_token = models.CharField(max_length=64, unique=True, db_index=True)
    claim_token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    claimed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="gift_invites_claimed"
    )
    claimed_at = models.DateTimeField(null=True, blank=True)
    email_sent_at = models.DateTimeField(null=True, blank=True)
    email_resend_count = models.PositiveSmallIntegerField(default=0)
    last_email_sent_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(help_text=_("When this invite expires"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("gift invite")
        verbose_name_plural = _("gift invites")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient_email_hash", "status"]),
            models.Index(fields=["claim_token_hash"]),
            models.Index(fields=["status", "expires_at"]),
        ]

    def __str__(self) -> str:
        return f"GiftInvite for {self.recipient_email} ({self.status})"

    def save(self, *args, **kwargs):
        if self.recipient_email:
            self.recipient_email = self.recipient_email.lower().strip()
            self.recipient_email_hash = hashlib.sha256(
                self.recipient_email.encode()
            ).hexdigest()
        super().save(*args, **kwargs)

    @property
    def is_expired(self) -> bool:
        return timezone.now() > self.expires_at

    @property
    def is_claimable(self) -> bool:
        return (
            self.status == self.Status.PENDING
            and not self.is_expired
            and self.claimed_by is None
        )

    @property
    def can_resend_email(self) -> bool:
        if self.email_resend_count >= 5:
            return False
        if self.last_email_sent_at:
            return timezone.now() > (self.last_email_sent_at + timezone.timedelta(hours=1))
        return True

    @classmethod
    def generate_token(cls) -> str:
        return secrets.token_urlsafe(32)

    @classmethod
    def hash_token(cls, token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()


class PendingGiftClaim(models.Model):
    """Stores pending gift claim attempts for anonymous users."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        PROCESSED = "processed", _("Processed")
        FAILED = "failed", _("Failed")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    claim_token_hash = models.CharField(max_length=64, db_index=True)
    session_key = models.CharField(max_length=40, db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    processed_at = models.DateTimeField(null=True, blank=True)
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="pending_claims_processed"
    )
    error_message = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("pending gift claim")
        verbose_name_plural = _("pending gift claims")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["session_key", "status"]),
            models.Index(fields=["claim_token_hash", "status"]),
        ]

    def __str__(self) -> str:
        return f"PendingClaim {self.claim_token_hash[:8]}... ({self.status})"

    @property
    def is_stale(self) -> bool:
        return timezone.now() > (self.created_at + timezone.timedelta(days=7))


# ============================================================================
# REFERRAL TRACKING MODELS
# ============================================================================

class ReferralCode(models.Model):
    """Unique referral code for each user. Auto-created on user signup via signal."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="referral_code", primary_key=True,
    )
    code = models.CharField(max_length=16, unique=True, db_index=True, help_text="Unique referral code")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "referral code"
        verbose_name_plural = "referral codes"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.code} ({self.user.username})"

    @classmethod
    def generate_unique_code(cls) -> str:
        while True:
            letters = ''.join(secrets.choice(string.ascii_uppercase) for _ in range(4))
            numbers = ''.join(secrets.choice(string.digits) for _ in range(4))
            code = f"{letters}{numbers}"
            if not cls.objects.filter(code=code).exists():
                return code

    @classmethod
    def get_or_create_for_user(cls, user) -> "ReferralCode":
        obj, created = cls.objects.get_or_create(
            user=user, defaults={"code": cls.generate_unique_code()}
        )
        return obj


class Referral(models.Model):
    """Tracks referral relationships between users."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"

    referrer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="referrals_made", help_text="User who shared their referral code"
    )
    referred_user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="referred_by", help_text="User who signed up with the code"
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "referral"
        verbose_name_plural = "referrals"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                check=~models.Q(referrer=models.F("referred_user")),
                name="prevent_self_referral"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.referrer.username} -> {self.referred_user.username} ({self.status})"

    def mark_completed(self) -> None:
        if self.status != self.Status.COMPLETED:
            self.status = self.Status.COMPLETED
            self.completed_at = timezone.now()
            self.save(update_fields=["status", "completed_at"])

    @property
    def is_completed(self) -> bool:
        return self.status == self.Status.COMPLETED


# ============================================================================
# REFERRAL REWARD MODELS (Phase 4 - Viral Mode)
# ============================================================================

class ReferralSettings(models.Model):
    """Admin-configurable settings for referral rewards (singleton)."""

    default_reward_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("20.00"),
        validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("100.00"))],
        help_text="Default percentage of referred purchase amount to reward referrer (e.g., 20.00 = 20%)"
    )
    minimum_purchase_amount_cents = models.PositiveIntegerField(
        default=0, help_text="Minimum purchase amount (in cents) required to trigger a reward"
    )
    rewards_enabled = models.BooleanField(
        default=True, help_text="Enable or disable referral rewards system-wide"
    )
    # NEW: Configurable reward delay (default 72 hours = 3 days)
    reward_delay_hours = models.PositiveIntegerField(
        default=72, help_text="Hours to delay before unlocking referral rewards (default: 72 = 3 days)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "referral settings"
        verbose_name_plural = "referral settings"

    def __str__(self) -> str:
        return f"Referral Settings (Reward: {self.default_reward_percentage}%, Enabled: {self.rewards_enabled})"

    @classmethod
    def get_settings(cls) -> "ReferralSettings":
        obj, created = cls.objects.get_or_create(
            pk=1,
            defaults={
                "default_reward_percentage": Decimal("20.00"),
                "minimum_purchase_amount_cents": 0,
                "rewards_enabled": True,
                "reward_delay_hours": 72,
            }
        )
        return obj


class ReferralReward(models.Model):
    """Ledger entry for referral rewards earned by a referrer."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"           # Waiting for unlock delay
        CREDITED = "credited", "Credited"        # Available in wallet
        USED = "used", "Used"                    # Fully consumed
        EXPIRED = "expired", "Expired"           # Refunded or blocked

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    referral = models.OneToOneField(
        Referral, on_delete=models.CASCADE, related_name="reward",
        help_text="The referral that generated this reward"
    )
    referrer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="referral_rewards", help_text="User who earned this reward"
    )
    amount_cents = models.PositiveIntegerField(help_text="Reward amount in cents")
    currency = models.CharField(max_length=3, default="USD", help_text="Currency of the reward amount")
    referred_purchase_amount_cents = models.PositiveIntegerField(
        help_text="Original purchase amount that triggered this reward (in cents)"
    )
    reward_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, help_text="Percentage used to calculate this reward"
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    used_amount_cents = models.PositiveIntegerField(
        default=0, help_text="Amount already used/consumed (in cents)"
    )
    used_at = models.DateTimeField(null=True, blank=True)
    # NEW: Delayed unlock fields
    unlocked_at = models.DateTimeField(
        null=True, blank=True, help_text="When this reward becomes available (after delay)"
    )
    # NEW: Track if reward was blocked (circular, refunded, etc.)
    block_reason = models.CharField(
        max_length=50, blank=True, help_text="Reason if reward was blocked (circular, refunded, etc.)"
    )
    # NEW: Explicit link to triggering subscription for refund checking
    triggering_subscription = models.ForeignKey(
        "subscriptions.Subscription",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="referral_rewards",
        help_text="The subscription that triggered this reward (for refund checking)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "referral reward"
        verbose_name_plural = "referral rewards"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["referrer", "status"]),
            models.Index(fields=["status", "unlocked_at"]),  # For unlock task
        ]

    def __str__(self) -> str:
        return f"Reward {self.amount_cents/100:.2f} {self.currency} for {self.referrer.username} ({self.status})"

    @property
    def amount_display(self) -> str:
        return f"{self.amount_cents / 100:.2f}"

    @property
    def available_amount_cents(self) -> int:
        if self.status in [self.Status.EXPIRED]:
            return 0
        return max(0, self.amount_cents - self.used_amount_cents)

    @property
    def is_fully_used(self) -> bool:
        return self.available_amount_cents == 0

    @property
    def is_expired(self) -> bool:
        return self.status == self.Status.EXPIRED

    @property
    def is_unlocked(self) -> bool:
        """Check if reward delay has passed and can be credited."""
        if self.status != self.Status.PENDING:
            return self.status == self.Status.CREDITED
        if not self.unlocked_at:
            return False
        return timezone.now() >= self.unlocked_at

    def mark_credited(self) -> None:
        """Mark reward as credited (available in wallet)."""
        if self.status == self.Status.PENDING:
            self.status = self.Status.CREDITED
            self.save(update_fields=["status", "updated_at"])

    def mark_expired(self, reason: str = "") -> None:
        """Mark reward as expired (refunded, circular, etc.)."""
        self.status = self.Status.EXPIRED
        if reason:
            self.block_reason = reason
        self.save(update_fields=["status", "block_reason", "updated_at"])

    def mark_used(self, amount_cents: int) -> None:
        self.used_amount_cents += amount_cents
        if self.used_amount_cents >= self.amount_cents:
            self.status = self.Status.USED
            self.used_at = timezone.now()
        self.save(update_fields=["used_amount_cents", "status", "used_at", "updated_at"])


class ReferralRewardLedger(models.Model):
    """Transaction ledger for referral reward usage (audit trail)."""

    class TransactionType(models.TextChoices):
        CREDIT = "credit", "Credit"
        DEBIT = "debit", "Debit"
        EXPIRED = "expired", "Expired"
        REVERSAL = "reversal", "Reversal"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reward = models.ForeignKey(
        ReferralReward, on_delete=models.CASCADE, related_name="ledger_entries",
        help_text="The reward being transacted"
    )
    transaction_type = models.CharField(max_length=10, choices=TransactionType.choices)
    amount_cents = models.IntegerField(
        help_text="Transaction amount in cents (positive for credit, negative for debit)"
    )
    balance_after_cents = models.IntegerField(help_text="Reward balance after this transaction (in cents)")
    description = models.CharField(max_length=255, blank=True, help_text="Description of transaction")
    subscription = models.ForeignKey(
        "subscriptions.Subscription", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="referral_reward_entries",
        help_text="Subscription this reward was applied to (if applicable)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True, help_text="Additional metadata")

    class Meta:
        verbose_name = "referral reward ledger entry"
        verbose_name_plural = "referral reward ledger entries"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.transaction_type} {abs(self.amount_cents)/100:.2f} - {self.description[:50]}"
