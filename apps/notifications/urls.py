"""
Notifications URLs with badge counter endpoints.
"""
from django.urls import path

from apps.notifications.views import (
    NotificationListAPIView,
    mark_notification_read,
    mark_all_notifications_read,
    unread_notification_count,
)

urlpatterns = [
    # List notifications with filtering
    path("", NotificationListAPIView.as_view(), name="api_notifications_list"),

    # Badge counter endpoint (lightweight)
    path("unread-count/", unread_notification_count, name="api_notifications_unread_count"),

    # Mark single notification as read (FIXED: added <uuid:pk> parameter)
    path("<uuid:pk>/read/", mark_notification_read, name="api_notification_read"),

    # Mark all notifications as read
    path("mark-all-read/", mark_all_notifications_read, name="api_notifications_mark_all_read"),
]
