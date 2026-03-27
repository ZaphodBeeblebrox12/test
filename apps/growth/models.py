"""
Growth models for gift invites and pending claims.

This module contains:
- GiftInvite: Token-based gift invitation (service-created only)
- PendingGiftClaim: Stores anonymous claim attempts for post-signup processing
"""
import uuid
import secrets
import hashlib

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.conf import settings


class GiftInvite(models.Model):
    """
    Token-based gift invitation.

    IMPORTANT: This model must ONLY be created through GiftService.
    Direct admin creation is disabled to ensure data integrity.
    Each GiftInvite is linked to a GiftSubscription and uses a secure
    token for the claim flow.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        CLAIMED = "claimed", _("Claimed")
        EXPIRED = "expired", _("Expired")
        REVOKED = "revoked", _("Revoked")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Link to the existing GiftSubscription model
    gift_subscription = models.OneToOneField(
        "subscriptions.GiftSubscription",
        on_delete=models.CASCADE,
        related_name="gift_invite",
        help_text=_("The gift subscription this invite is for")
    )

    # Recipient information
    recipient_email = models.EmailField(
        help_text=_("Email address of the intended recipient")
    )
    recipient_email_hash = models.CharField(
        max_length=64,
        db_index=True,
        help_text=_("SHA-256 hash of email for lookup without exposing email")
    )

    # Secure token for claiming
    claim_token = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text=_("Secure token for claiming the gift")
    )

    # Token hash for verification (store hash, not raw token)
    claim_token_hash = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text=_("SHA-256 hash of the claim token")
    )

    # Status tracking
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING
    )

    # Claim tracking
    claimed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gift_invites_claimed",
        help_text=_("User who claimed this gift")
    )
    claimed_at = models.DateTimeField(null=True, blank=True)

    # Email tracking
    email_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When the gift email was sent")
    )
    email_resend_count = models.PositiveSmallIntegerField(
        default=0,
        help_text=_("Number of times the email has been resent")
    )
    last_email_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When the gift email was last sent/resent")
    )

    # Expiration
    expires_at = models.DateTimeField(
        help_text=_("When this invite expires")
    )

    # Metadata
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
        """Ensure email hash is set on save."""
        if self.recipient_email:
            self.recipient_email = self.recipient_email.lower().strip()
            self.recipient_email_hash = hashlib.sha256(
                self.recipient_email.encode()
            ).hexdigest()
        super().save(*args, **kwargs)

    @property
    def is_expired(self) -> bool:
        """Check if the invite has expired."""
        return timezone.now() > self.expires_at

    @property
    def is_claimable(self) -> bool:
        """Check if the invite can still be claimed."""
        return (
            self.status == self.Status.PENDING 
            and not self.is_expired
            and self.claimed_by is None
        )

    @property
    def can_resend_email(self) -> bool:
        """Check if email can be resent (rate limiting)."""
        if self.email_resend_count >= 5:
            return False
        if self.last_email_sent_at:
            # Can resend after 1 hour
            return timezone.now() > (
                self.last_email_sent_at + timezone.timedelta(hours=1)
            )
        return True

    @classmethod
    def generate_token(cls) -> str:
        """Generate a secure random token."""
        return secrets.token_urlsafe(32)

    @classmethod
    def hash_token(cls, token: str) -> str:
        """Hash a token for storage/comparison."""
        return hashlib.sha256(token.encode()).hexdigest()


class PendingGiftClaim(models.Model):
    """
    Stores pending gift claim attempts for anonymous users.

    When an anonymous user clicks a gift claim link, we store the
    claim token here and redirect them to signup/login. After
    successful authentication, we process the pending claim.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        PROCESSED = "processed", _("Processed")
        FAILED = "failed", _("Failed")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # The claim token (hashed for security)
    claim_token_hash = models.CharField(
        max_length=64,
        db_index=True,
        help_text=_("Hash of the claim token being claimed")
    )

    # Session tracking
    session_key = models.CharField(
        max_length=40,
        db_index=True,
        help_text=_("Django session key for correlation")
    )

    # Status
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING
    )

    # Processing tracking
    processed_at = models.DateTimeField(null=True, blank=True)
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pending_claims_processed",
        help_text=_("User who eventually claimed this")
    )
    error_message = models.TextField(
        blank=True,
        help_text=_("Error message if processing failed")
    )

    # Metadata
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text=_("IP address of the requester")
    )
    user_agent = models.TextField(
        blank=True,
        help_text=_("User agent of the requester")
    )
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
        """Check if this pending claim is stale (older than 7 days)."""
        return timezone.now() > (
            self.created_at + timezone.timedelta(days=7)
        )
