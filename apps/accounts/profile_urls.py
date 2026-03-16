"""
Profile URLs for user dashboard and profile management.
"""
from django.urls import path

from apps.accounts.views import (
    DashboardView,
    ProfileView,
    ActivityLogView,
    NotificationsView,
)

urlpatterns = [
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("profile/", ProfileView.as_view(), name="profile"),
    path("activity/", ActivityLogView.as_view(), name="activity"),
    path("notifications/", NotificationsView.as_view(), name="notifications"),
]
