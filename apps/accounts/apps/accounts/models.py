"""
Accounts models for community platform.
"""
import uuid

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    """Custom User model with Telegram and Discord integration."""

    class Role(models.TextChoices):
        ADMIN = "admin", _("Admin")
        STAFF = "staff", _("Staff")
        USER = "user", _("User")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Standard username field
    username = models.CharField(
        max_length=255,
        unique=True,
        help_text=_("Unique username for login")
    )

    # Telegram fields (optional, for Telegram auth)
    telegram_id = models.BigIntegerField(
        null=True,
        blank=True,
        unique=True,
        help_text=_("Telegram user ID (optional)")
    )
    telegram_username = models.CharField(
        max_length=255,
        blank=True,
        help_text=_("Telegram username without @")
    )
    telegram_verified = models.BooleanField(
        default=False,
        help_text=_("Whether Telegram has been verified via widget")
    )

    # Discord fields (optional, for Discord auth)
    discord_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        unique=True,
        help_text=_("Discord user ID")
    )
    discord_username = models.CharField(
        max_length=255,
        blank=True,
        help_text=_("Discord username")
    )
    discord_avatar = models.CharField(
        max_length=255,
        blank=True,
        help_text=_("Discord avatar hash")
    )
    discord_verified = models.BooleanField(
        default=False,
        help_text=_("Whether Discord has been verified via OAuth")
    )

    # Profile fields
    first_name = models.CharField(max_length=255, blank=True)
    last_name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True, null=True, unique=True)
    bio = models.TextField(blank=True, help_text=_("User biography"))
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)

    # Role and permissions
    role = models.CharField(
        max_length=10,
        choices=Role.choices,
        default=Role.USER,
    )
    is_staff_approved = models.BooleanField(
        default=False,
        help_text=_("Designates whether staff role has been approved by admin")
    )

    # Ban system
    is_banned = models.BooleanField(default=False)
    ban_reason = models.TextField(blank=True)
    banned_at = models.DateTimeField(null=True, blank=True)

    # Django fields
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    last_login = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ["-date_joined"]

    def __str__(self) -> str:
        return self.username

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def is_admin(self) -> bool:
        return self.role == self.Role.ADMIN or self.is_superuser

    def ban(self, reason: str = "") -> None:
        """Ban the user."""
        self.is_banned = True
        self.ban_reason = reason
        self.banned_at = timezone.now()
        self.save(update_fields=["is_banned", "ban_reason", "banned_at"])

    def unban(self) -> None:
        """Unban the user."""
        self.is_banned = False
        self.ban_reason = ""
        self.banned_at = None
        self.save(update_fields=["is_banned", "ban_reason", "banned_at"])

    def approve_staff(self) -> None:
        """Approve staff role."""
        self.is_staff_approved = True
        self.is_staff = True
        self.save(update_fields=["is_staff_approved", "is_staff"])

    def can_subscribe(self) -> bool:
        """Check if user can create subscriptions."""
        return not self.is_banned and self.is_active


class UserPreference(models.Model):
    """User preferences and settings."""

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="preferences"
    )
    timezone = models.CharField(max_length=50, default="UTC")
    language = models.CharField(max_length=10, default="en")
    notifications_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("user preference")
        verbose_name_plural = _("user preferences")

    def __str__(self) -> str:
        return f"Preferences for {self.user}"


class Profile(models.Model):
    """User profile extension (legacy, merged into User)."""

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="profile_legacy"
    )
    timezone = models.CharField(max_length=50, default="UTC")
    language = models.CharField(max_length=10, default="en")
    avatar_url = models.URLField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("profile")
        verbose_name_plural = _("profiles")

    def __str__(self) -> str:
        return f"Profile for {self.user}"


@receiver(post_save, sender=User)
def create_user_preferences(sender, instance, created, **kwargs):
    """Automatically create UserPreference when a new user is created."""
    if created:
        UserPreference.objects.get_or_create(user=instance)
