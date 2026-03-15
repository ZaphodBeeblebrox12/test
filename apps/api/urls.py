"""
API URL configuration.
"""
from django.urls import path, include

from apps.api.views import (
    APIKeyListCreateView,
    APIKeyRevokeView,
    user_notifications_api,
    api_info,
)

urlpatterns = [
    path("", api_info, name="api_info"),
    path("auth/keys/", APIKeyListCreateView.as_view(), name="api_keys_list"),
    path("auth/keys/<uuid:pk>/revoke/", APIKeyRevokeView.as_view(), name="api_key_revoke"),
    path("user/notifications/", user_notifications_api, name="api_user_notifications"),
    path("notifications/", include("apps.notifications.urls")),
]
