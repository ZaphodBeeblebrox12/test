"""
Notifications URLs.
"""
from django.urls import path

from apps.notifications.views import (
    NotificationListAPIView,
    mark_notification_read,
    mark_all_notifications_read,
    unread_notification_count,
    notifications_list_view,
)

app_name = "notifications"

urlpatterns = [
    # Template view for /notifications/ page
    path("notifications/", notifications_list_view, name="list"),

    # API endpoints (mounted under api/notifications/ in config/urls.py)
    path("", NotificationListAPIView.as_view(), name="api_notifications_list"),
    path("unread-count/", unread_notification_count, name="api_notifications_unread_count"),
    path("<uuid:pk>/read/", mark_notification_read, name="api_notification_read"),
    path("read-all/", mark_all_notifications_read, name="api_notifications_read_all"),
]
