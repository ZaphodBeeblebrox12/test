"""
API views.
"""
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.models import APIKey
from apps.notifications.models import Notification
from apps.notifications.serializers import NotificationSerializer


class APIKeyListCreateView(generics.ListCreateAPIView):
    """List or create API keys."""
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return APIKey.objects.filter(user=self.request.user, is_active=True)

    def perform_create(self, serializer):
        # Generate key
        raw_key = APIKey.generate_key()
        key_hash = APIKey.hash_key(raw_key)
        key_prefix = raw_key[:8]

        api_key = serializer.save(
            user=self.request.user,
            key_hash=key_hash,
            key_prefix=key_prefix
        )

        # Store raw key temporarily for response
        api_key._raw_key = raw_key

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        # Include raw key in response (only shown once)
        if hasattr(response.instance, '_raw_key'):
            response.data['key'] = response.instance._raw_key
        return response


class APIKeyRevokeView(generics.DestroyAPIView):
    """Revoke an API key."""
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return APIKey.objects.filter(user=self.request.user)

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save()


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def user_notifications_api(request):
    """Get user notifications."""
    notifications = Notification.objects.filter(
        user=request.user
    ).order_by("-created_at")[:50]

    serializer = NotificationSerializer(notifications, many=True)
    return Response(serializer.data)


@api_view(["GET"])
def api_info(request):
    """API information."""
    return Response({
        "name": "Community Platform API",
        "version": "2.0.0",
        "status": "operational"
    })
