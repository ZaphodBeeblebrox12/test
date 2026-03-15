"""
Audit logging models for community platform.
"""
import uuid

from django.db import models
from django.conf import settings


class AuditLog(models.Model):
    """Audit log for tracking system events."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs"
    )
    action = models.CharField(max_length=100, db_index=True)
    object_type = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Audit Log"
        verbose_name_plural = "Audit Logs"
        indexes = [
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["object_type", "object_id"]),
        ]

    def __str__(self):
        user_str = str(self.user) if self.user else "system"
        return f"{self.action} by {user_str} at {self.created_at}"

    @classmethod
    def log(cls, action, user=None, object_type="", object_id="", metadata=None):
        """Create audit log entry."""
        return cls.objects.create(
            user=user,
            action=action,
            object_type=object_type,
            object_id=str(object_id) if object_id else "",
            metadata=metadata or {}
        )
