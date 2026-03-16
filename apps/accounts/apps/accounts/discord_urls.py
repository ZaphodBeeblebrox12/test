"""
Discord authentication URL configuration.
"""
from django.urls import path

from apps.accounts.discord_views import (
    DiscordConnectView,
    DiscordCallbackView,
    DiscordDisconnectView,
)

urlpatterns = [
    path("connect/", DiscordConnectView.as_view(), name="discord_connect"),
    path("callback/", DiscordCallbackView.as_view(), name="discord_callback"),
    path("disconnect/", DiscordDisconnectView.as_view(), name="discord_disconnect"),
]
