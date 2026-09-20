"""
Runtime registration state for discovered Telegram bot bridges.

CONFIGURATION lives in BotConfig (token, webhook, identity).
RUNTIME STATE lives here — everything Django learned from the bot's own
self-registration.  No secrets are ever stored.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone


class BotRuntimeState(models.Model):
    """Latest known runtime state of a self-registered bot bridge instance."""

    STATUS_ONLINE = "online"
    STATUS_OFFLINE = "offline"
    STATUS_INCOMPATIBLE = "incompatible"
    STATUS_CHOICES = [
        (STATUS_ONLINE, "Online"),
        (STATUS_OFFLINE, "Offline"),
        (STATUS_INCOMPATIBLE, "Incompatible"),
    ]

    instance_id = models.CharField(max_length=64, unique=True)
    base_url = models.URLField(max_length=255)
    contract = models.CharField(max_length=32, default="provision")
    contract_version = models.PositiveSmallIntegerField(default=1)
    operations = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ONLINE)
    bot_telegram_id = models.BigIntegerField(null=True, blank=True)
    bot_username = models.CharField(max_length=255, blank=True)
    last_seen = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_seen"]

    def __str__(self):
        return f"{self.instance_id} ({self.base_url})"

    # ---- compatibility -------------------------------------------------
    REQUIRED_OPERATIONS = frozenset({"grant", "revoke"})

    def is_compatible(self) -> bool:
        if self.contract != "provision" or self.contract_version != 1:
            return False
        try:
            return self.REQUIRED_OPERATIONS.issubset(set(self.operations or []))
        except TypeError:
            return False

    def is_fresh(self, seconds: int) -> bool:
        cutoff = timezone.now() - timezone.timedelta(seconds=seconds)
        return self.last_seen >= cutoff

    @classmethod
    def latest_compatible(cls, freshness_seconds: int):
        """Most recently seen compatible+fresh registration, or None."""
        from django.conf import settings
        fresh = cls.objects.filter(
            last_seen__gte=timezone.now()
            - timezone.timedelta(seconds=freshness_seconds)
        )
        for state in fresh:
            if state.is_compatible():
                return state
        # Mark genuinely incompatible registrations so Admin is truthful
        for state in cls.objects.filter(status=cls.STATUS_ONLINE):
            if not state.is_compatible():
                cls.objects.filter(pk=state.pk).update(status=cls.STATUS_INCOMPATIBLE)
        return None

    @classmethod
    def mark_offline_stale(cls, freshness_seconds: int):
        cutoff = timezone.now() - timezone.timedelta(seconds=freshness_seconds)
        cls.objects.filter(status=cls.STATUS_ONLINE, last_seen__lt=cutoff) \
                   .update(status=cls.STATUS_OFFLINE)

    @classmethod
    def touch(cls, instance_id: str, **fields):
        state, _ = cls.objects.update_or_create(instance_id=instance_id, defaults=fields)
        return state
