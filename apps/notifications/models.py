"""
Notifications models for community platform.
"""
import uuid

from django.db import models
from django.conf import settings


class Notification(models.Model):
    """User notification model."""

    class NotificationType(models.TextChoices):
        SYSTEM = "system", "System"
        SUBSCRIPTION = "subscription", "Subscription"
        CONTENT = "content", "Content"
        MENTION = "mention", "Mention"
        TELEGRAM = "telegram", "Telegram"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications"
    )
    notification_type = models.CharField(
        max_length=20,
        choices=NotificationType.choices,
        default=NotificationType.SYSTEM
    )
    title = models.CharField(max_length=255)
    message = models.TextField()
    link = models.URLField(blank=True)
    image = models.URLField(blank=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["user", "is_read"]),
        ]

    def __str__(self):
        return f"{self.title} - {self.user}"


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
