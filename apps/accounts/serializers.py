"""
Serializers for accounts app.
"""
from rest_framework import serializers

from apps.accounts.models import User, UserPreference


class UserPreferenceSerializer(serializers.ModelSerializer):
    """Serializer for UserPreference model."""

    class Meta:
        model = UserPreference
        fields = ["timezone", "language", "notifications_enabled", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]


class UserSerializer(serializers.ModelSerializer):
    """Serializer for User model (basic info)."""

    telegram_connected = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "username", "email", "first_name", "last_name",
            "bio", "avatar", "role", "is_staff", "is_admin",
            "telegram_verified", "telegram_connected",
            "date_joined", "last_login", "is_banned"
        ]
        read_only_fields = [
            "id", "username", "role", "is_staff", "is_admin",
            "telegram_verified", "date_joined", "last_login", "is_banned"
        ]

    def get_telegram_connected(self, obj):
        return bool(obj.telegram_id and obj.telegram_verified)


class UserProfileSerializer(serializers.ModelSerializer):
    """Serializer for user profile (detailed)."""

    preferences = UserPreferenceSerializer(read_only=True)
    telegram_connected = serializers.SerializerMethodField()
    full_name = serializers.CharField(source="full_name", read_only=True)

    class Meta:
        model = User
        fields = [
            "id", "username", "email", "first_name", "last_name", "full_name",
            "bio", "avatar", "role", "is_staff", "is_admin",
            "telegram_id", "telegram_username", "telegram_verified", "telegram_connected",
            "preferences", "date_joined", "last_login"
        ]
        read_only_fields = [
            "id", "username", "role", "is_staff", "is_admin",
            "telegram_id", "telegram_username", "telegram_verified",
            "date_joined", "last_login"
        ]

    def get_telegram_connected(self, obj):
        return bool(obj.telegram_id and obj.telegram_verified)
