"""
Notification serializers for API responses.
"""
from rest_framework import serializers
from apps.notifications.models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    """Serializer for Notification model."""

    # Human-readable type label
    type_label = serializers.CharField(source="get_notification_type_display", read_only=True)

    # Relative timestamp (e.g., "2 hours ago")
    relative_time = serializers.SerializerMethodField()

    # Urgency indicator color (red for urgent, blue for info)
    indicator_color = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            "id",
            "notification_type",
            "type_label",
            "title",
            "message",
            "link",
            "image",
            "is_read",
            "is_urgent",
            "metadata",
            "created_at",
            "read_at",
            "relative_time",
            "indicator_color",
        ]
        read_only_fields = [
            "id",
            "notification_type",
            "title",
            "message",
            "metadata",
            "created_at",
            "is_urgent",
        ]

    def get_relative_time(self, obj):
        """Return human-readable relative timestamp."""
        from django.utils.timesince import timesince
        from django.utils import timezone

        if obj.created_at:
            return f"{timesince(obj.created_at)} ago"
        return ""

    def get_indicator_color(self, obj):
        """Return indicator color based on urgency."""
        if obj.is_urgent:
            return "red"
        return "blue"


class UnreadCountSerializer(serializers.Serializer):
    """Serializer for unread count response."""
    count = serializers.IntegerField()


class MarkReadResponseSerializer(serializers.Serializer):
    """Serializer for mark-as-read response."""
    success = serializers.BooleanField()
    message = serializers.CharField(required=False)
