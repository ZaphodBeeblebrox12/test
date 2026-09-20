"""Test-only URL conf for the Provision v1 integration suite."""
from django.urls import include, path

urlpatterns = [
    path("bot/", include("apps.bot_integration.urls")),
    path("bot-admin/", include("apps.bot_integration.integration_admin")),
]
