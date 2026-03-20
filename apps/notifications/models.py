"""
Notifications models for community platform.
"""
import uuid

from django.db import models
from django.conf import settings


class Notification(models.Model):
    """User notification model with badge counter support."""

    class NotificationType(models.TextChoices):
        # System notifications
        SYSTEM = "system", "System"
        CONTENT = "content", "Content"
        MENTION = "mention", "Mention"
        TELEGRAM = "telegram", "Telegram"

        # Subscription-related notifications (new types per requirements)
        GRACE_PERIOD = "grace_period", "Grace Period"
        EXPIRATION_WARNING = "expiration_warning", "Expiration Warning"
        SUBSCRIPTION_EXPIRED = "subscription_expired", "Subscription Expired"
        PLAN_UPGRADED = "plan_upgraded", "Plan Upgraded"
        GIFT_RECEIVED = "gift_received", "Gift Received"
        PLATFORM_CONNECTED = "platform_connected", "Platform Connected"
        DOWNGRADE_SCHEDULED = "downgrade_scheduled", "Downgrade Scheduled"

    # Urgent notification types that persist until underlying issue resolved
    URGENT_TYPES = [
        NotificationType.GRACE_PERIOD,
        NotificationType.EXPIRATION_WARNING,
        NotificationType.DOWNGRADE_SCHEDULED,
        NotificationType.SUBSCRIPTION_EXPIRED,
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications"
    )
    notification_type = models.CharField(
        max_length=30,
        choices=NotificationType.choices,
        default=NotificationType.SYSTEM
    )
    title = models.CharField(max_length=100)
    message = models.TextField()
    link = models.URLField(blank=True)
    image = models.URLField(blank=True)

    # Read tracking
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    # Urgent flag - determines if notification persists until action taken
    is_urgent = models.BooleanField(
        default=False,
        help_text="Urgent notifications persist until underlying issue is resolved"
    )

    # Additional context data
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["user", "is_read"]),
            models.Index(fields=["notification_type", "is_read"]),
            models.Index(fields=["user", "notification_type", "is_read"]),
        ]

    def __str__(self):
        return f"{self.title} - {self.user}"

    def save(self, *args, **kwargs):
        # Auto-set is_urgent based on notification type
        if self.notification_type in self.URGENT_TYPES:
            self.is_urgent = True
        super().save(*args, **kwargs)

    def mark_as_read(self):
        """Mark notification as read."""
        from django.utils import timezone
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=["is_read", "read_at"])


class EmailLog(models.Model):
    """Sent email tracking."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    email = models.EmailField()
    template = models.CharField(max_length=100)
    subject = models.CharField(max_length=255)
    status = models.CharField(
        max_length=20,
        choices=[
            ("queued", "Queued"),
            ("sent", "Sent"),
            ("failed", "Failed"),
            ("bounced", "Bounced"),
        ],
        default="queued"
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
