"""
Notification views with badge counter and filtering support.
"""
from django.utils import timezone
from rest_framework import generics, permissions, status, filters
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from django_filters.rest_framework import DjangoFilterBackend

from apps.notifications.models import Notification
from apps.notifications.serializers import NotificationSerializer
from apps.notifications.helpers import (
    get_unread_count,
    mark_all_as_read,
    mark_as_read,
)


class StandardResultsSetPagination(PageNumberPagination):
    """Standard pagination for notification lists."""
    page_size = 20
    page_size_query_param = 'limit'
    max_page_size = 100


class NotificationListAPIView(generics.ListAPIView):
    """
    List user notifications with filtering.

    Query params:
    - is_read: true/false - filter by read status
    - limit: number - pagination limit
    """
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['is_read', 'notification_type']

    def get_queryset(self):
        """Return notifications for current user, newest first."""
        return Notification.objects.filter(user=self.request.user)


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def unread_notification_count(request):
    """
    Get unread notification count for badge counter.

    Returns:
        { "count": 3 }
    """
    count = get_unread_count(request.user)
    return Response({"count": count})


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def mark_notification_read(request, pk):
    """
    Mark a specific notification as read.

    URL param:
    - pk: Notification UUID
    """
    success = mark_as_read(pk, request.user)

    if success:
        return Response({"success": True})

    return Response(
        {"success": False, "message": "Notification not found or already read"},
        status=status.HTTP_404_NOT_FOUND
    )


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def mark_all_notifications_read(request):
    """
    Mark all notifications as read for current user.

    Returns:
        { "success": True, "marked_count": 5 }
    """
    count = mark_all_as_read(request.user)
    return Response({
        "success": True,
        "marked_count": count
    })
