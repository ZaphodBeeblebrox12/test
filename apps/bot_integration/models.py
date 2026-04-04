import uuid
import secrets
from django.db import models
from django.utils import timezone
from django.conf import settings


class BotConfig(models.Model):
    """Singleton configuration for Telegram and Discord bots."""
    telegram_bot_token = models.CharField(max_length=255, blank=True)
    telegram_bot_username = models.CharField(max_length=255, blank=True)
    telegram_webhook_url = models.URLField(blank=True, help_text="Override auto webhook URL")

    discord_bot_token = models.CharField(max_length=255, blank=True)
    discord_guild_id = models.CharField(max_length=64, blank=True, help_text="Discord server (guild) ID")
    discord_client_id = models.CharField(max_length=255, blank=True)
    discord_client_secret = models.CharField(max_length=255, blank=True)
    discord_redirect_uri = models.URLField(blank=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Bot Configuration"
        verbose_name_plural = "Bot Configurations"

    def __str__(self):
        return f"Bot Config (Telegram: {bool(self.telegram_bot_token)}, Discord: {bool(self.discord_bot_token)})"

    @classmethod
    def get_config(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class TelegramAccount(models.Model):
    """Links a Django user to their Telegram chat_id and user_id."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='telegram_account'
    )
    chat_id = models.BigIntegerField(unique=True)
    telegram_user_id = models.BigIntegerField(
        null=True, blank=True,
        help_text="Telegram numeric user ID (from message.from.id). Used for banning/unbanning."
    )
    linked_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Telegram Account"
        verbose_name_plural = "Telegram Accounts"

    def __str__(self):
        return f"{self.user.username} -> {self.chat_id}"


class DiscordAccount(models.Model):
    """Links a Django user to their Discord user ID and guild roles."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='discord_account'
    )
    discord_user_id = models.CharField(max_length=64, unique=True)
    guild_id = models.CharField(max_length=64, blank=True)
    username = models.CharField(max_length=255, blank=True)
    roles = models.JSONField(default=list, blank=True, help_text="Cached list of role IDs")
    linked_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Discord Account"
        verbose_name_plural = "Discord Accounts"

    def __str__(self):
        return f"{self.user.username} -> {self.discord_user_id}"


class PlanChannelMapping(models.Model):
    """Maps a subscription plan to Telegram channels and Discord roles."""
    PLATFORM_CHOICES = [
        ('telegram', 'Telegram'),
        ('discord', 'Discord'),
    ]

    plan = models.ForeignKey(
        'subscriptions.Plan',
        on_delete=models.CASCADE,
        related_name='bot_channel_mappings'
    )
    platform = models.CharField(max_length=10, choices=PLATFORM_CHOICES)
    external_id = models.CharField(
        max_length=255,
        help_text="Telegram channel ID/username or Discord role ID"
    )
    name = models.CharField(max_length=255, blank=True, help_text="Human-readable name")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('plan', 'platform', 'external_id')]
        verbose_name = "Plan Channel Mapping"
        verbose_name_plural = "Plan Channel Mappings"

    def __str__(self):
        return f"{self.plan.name} → {self.platform}: {self.external_id}"


class UserChannelAssignment(models.Model):
    """
    Idempotency: tracks which channels/roles have already been granted to a user.
    """
    PLATFORM_CHOICES = [
        ('telegram', 'Telegram'),
        ('discord', 'Discord'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='channel_assignments'
    )
    platform = models.CharField(max_length=10, choices=PLATFORM_CHOICES)
    external_id = models.CharField(max_length=255)
    assigned_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [('user', 'platform', 'external_id')]
        verbose_name = "User Channel Assignment"
        verbose_name_plural = "User Channel Assignments"

    def __str__(self):
        return f"{self.user.username} - {self.platform}:{self.external_id} ({'active' if self.is_active else 'revoked'})"


class BotAccessAudit(models.Model):
    """Audit log for all bot actions."""
    ACTION_CHOICES = [
        ('grant', 'Grant'),
        ('revoke', 'Revoke'),
        ('link', 'Link'),
        ('unlink', 'Unlink'),
        ('sync', 'Sync'),
    ]
    PLATFORM_CHOICES = [
        ('telegram', 'Telegram'),
        ('discord', 'Discord'),
    ]
    STATUS_CHOICES = [
        ('success', 'Success'),
        ('failed', 'Failed'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='bot_audits'
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    platform = models.CharField(max_length=10, choices=PLATFORM_CHOICES)
    target = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['status']),
        ]
        verbose_name = "Bot Access Audit"
        verbose_name_plural = "Bot Access Audits"

    def __str__(self):
        return f"{self.user.username} - {self.action} on {self.platform} ({self.status})"


class TelegramVerificationToken(models.Model):
    """Temporary token for deep‑link verification."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='telegram_verification_tokens'
    )
    token = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    def is_valid(self):
        return timezone.now() < self.expires_at

    @classmethod
    def create_token(cls, user, ttl_minutes=10):
        token = secrets.token_urlsafe(32)
        expires = timezone.now() + timezone.timedelta(minutes=ttl_minutes)
        return cls.objects.create(user=user, token=token, expires_at=expires)

    class Meta:
        verbose_name = "Telegram Verification Token"
        verbose_name_plural = "Telegram Verification Tokens"