"""
URL configuration for accounts API.
"""
from django.urls import path

from apps.accounts.views import (
    UserMeAPIView,
    UserProfileAPIView,
    UserActivityAPIView,
    telegram_connect,
)

urlpatterns = [
    path("me/", UserMeAPIView.as_view(), name="api_user_me"),
    path("profile/", UserProfileAPIView.as_view(), name="api_user_profile"),
    path("activity/", UserActivityAPIView.as_view(), name="api_user_activity"),
    path("telegram/connect/", telegram_connect, name="api_telegram_connect"),
]
