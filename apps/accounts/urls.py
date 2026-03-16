"""
Account API URL configuration.
"""
from django.urls import path

from apps.accounts.views import (
    telegram_connect,
    UserMeAPIView,
    UserProfileAPIView,
    UserActivityAPIView,
)
from apps.accounts.discord_views import discord_connect_api

urlpatterns = [
    # Telegram endpoints - using name that matches template
    path("telegram/", telegram_connect, name="api_telegram_connect"),

    # Discord endpoints
    path("discord/connect/", discord_connect_api, name="api_discord_connect"),

    # User API endpoints
    path("user/me/", UserMeAPIView.as_view(), name="user_me"),
    path("user/profile/", UserProfileAPIView.as_view(), name="user_profile"),
    path("user/activity/", UserActivityAPIView.as_view(), name="user_activity"),
]
