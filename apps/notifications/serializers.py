"""
Notifications serializers.
"""
from rest_framework import serializers

from apps.notifications.models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    """Serializer for Notification model."""

    class Meta:
        model = Notification
        fields = [
            "id", "notification_type", "title", "message",
            "link", "image", "is_read", "read_at", "created_at"
        ]
        read_only_fields = ["id", "created_at"]
