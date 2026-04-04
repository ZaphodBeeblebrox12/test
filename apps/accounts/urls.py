"""
Account API URL configuration.
"""
from django.urls import path

from apps.accounts.views import (
    telegram_connect,
    UserMeAPIView,
    UserProfileAPIView,
    UserActivityAPIView,
    ReferralDashboardView,          # <-- NEW IMPORT
)
from apps.accounts.discord_views import discord_connect_api

urlpatterns = [
    # Telegram endpoints
    path("telegram/", telegram_connect, name="api_telegram_connect"),

    # Discord endpoints
    path("discord/connect/", discord_connect_api, name="api_discord_connect"),

    # User API endpoints - FIXED paths and names
    path("me/", UserMeAPIView.as_view(), name="api_user_me"),
    path("profile/", UserProfileAPIView.as_view(), name="api_user_profile"),
    path("activity/", UserActivityAPIView.as_view(), name="api_user_activity"),

    # Referral dashboard
    path("referrals/", ReferralDashboardView.as_view(), name="referrals"),
]