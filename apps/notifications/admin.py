"""
Notifications admin.
"""
from django.contrib import admin

from apps.notifications.models import Notification, EmailLog


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    """Notification admin."""

    list_display = ["title", "user", "notification_type", "is_read", "created_at"]
    list_filter = ["notification_type", "is_read", "created_at"]
    search_fields = ["title", "message", "user__username"]
    readonly_fields = ["id", "created_at"]


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    """Email log admin."""

    list_display = ["email", "template", "subject", "status", "created_at"]
    list_filter = ["status", "template", "created_at"]
    search_fields = ["email", "subject"]
    readonly_fields = ["id", "created_at"]
