"""
API models for community platform.
"""
import hashlib
import secrets
import uuid

from django.db import models
from django.conf import settings


class APIKey(models.Model):
    """API authentication keys."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_keys"
    )
    name = models.CharField(max_length=100)
    key_hash = models.CharField(max_length=64, unique=True, db_index=True)
    key_prefix = models.CharField(max_length=8)
    is_active = models.BooleanField(default=True)
    rate_limit = models.IntegerField(default=60)  # requests per minute
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.key_prefix}...)"

    @classmethod
    def generate_key(cls):
        """Generate a new API key."""
        return f"live_{secrets.token_urlsafe(32)}"

    @classmethod
    def hash_key(cls, key):
        """Hash an API key."""
        return hashlib.sha256(key.encode()).hexdigest()

    def verify_key(self, key):
        """Verify a raw key against the stored hash."""
        return self.key_hash == self.hash_key(key)


class APIRequestLog(models.Model):
    """API usage logging."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    api_key = models.ForeignKey(
        APIKey,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    endpoint = models.CharField(max_length=255)
    method = models.CharField(max_length=10)
    status_code = models.IntegerField()
    response_time_ms = models.IntegerField(default=0)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['api_key', '-created_at']),
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['endpoint', '-created_at']),
        ]
