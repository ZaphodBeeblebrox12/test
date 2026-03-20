"""
Notification helper functions for creating and managing notifications.
"""
from django.utils import timezone
from django.db import transaction
from typing import Optional, Dict, Any

from apps.notifications.models import Notification


def create_notification(
    user,
    notification_type: str,
    title: str,
    message: str,
    link: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    **kwargs
) -> Optional[Notification]:
    """
    Create a notification for a user with idempotency check.

    Prevents duplicate notifications of the same type for the same user
    when an unread notification of that type already exists.

    Args:
        user: The user to notify
        notification_type: Type of notification (from Notification.NotificationType)
        title: Notification title (max 100 chars)
        message: Notification message body
        link: Optional URL link
        metadata: Optional JSON-serializable dict with extra context
        **kwargs: Additional fields to set on the notification

    Returns:
        Notification instance if created, None if duplicate exists
    """
    # Idempotency check: Don't create if unread notification of same type exists
    existing = Notification.objects.filter(
        user=user,
        notification_type=notification_type,
        is_read=False
    ).first()

    if existing:
        # Duplicate prevention: Don't create another unread notification of same type
        return None

    notification = Notification.objects.create(
        user=user,
        notification_type=notification_type,
        title=title[:100],  # Ensure within max_length
        message=message,
        link=link,
        metadata=metadata or {},
        **kwargs
    )

    return notification


def get_unread_count(user) -> int:
    """
    Get the count of unread notifications for a user.

    This is optimized to use a lightweight COUNT query.

    Args:
        user: The user to get count for

    Returns:
        Number of unread notifications
    """
    return Notification.objects.filter(user=user, is_read=False).count()


def mark_all_as_read(user) -> int:
    """
    Mark all notifications as read for a user.

    Args:
        user: The user whose notifications to mark as read

    Returns:
        Number of notifications marked as read
    """
    from django.utils import timezone

    count = Notification.objects.filter(
        user=user,
        is_read=False
    ).update(
        is_read=True,
        read_at=timezone.now()
    )

    return count


def mark_as_read(notification_id: str, user) -> bool:
    """
    Mark a specific notification as read.

    Args:
        notification_id: UUID of the notification
        user: The user who owns the notification

    Returns:
        True if marked as read, False if not found or already read
    """
    try:
        notification = Notification.objects.get(id=notification_id, user=user)
        if not notification.is_read:
            notification.mark_as_read()
            return True
        return False
    except Notification.DoesNotExist:
        return False


def get_recent_notifications(user, limit: int = 10, include_read: bool = True):
    """
    Get recent notifications for a user.

    Args:
        user: The user to get notifications for
        limit: Maximum number of notifications to return
        include_read: Whether to include read notifications

    Returns:
        QuerySet of notifications
    """
    queryset = Notification.objects.filter(user=user)

    if not include_read:
        queryset = queryset.filter(is_read=False)

    return queryset[:limit]


def cleanup_old_notifications(days: int = 90):
    """
    Soft-delete (archive) notifications older than specified days.

    Note: This is a soft delete only - we don't hard delete notifications.
    For now, this just marks them as "archived" in metadata.

    Args:
        days: Age in days after which to archive notifications
    """
    from datetime import timedelta

    cutoff_date = timezone.now() - timedelta(days=days)

    # Mark old notifications as archived in metadata
    # We don't delete them (soft delete only per requirements)
    Notification.objects.filter(
        created_at__lt=cutoff_date,
        metadata__archived=False
    ).update(
        metadata__archived=True
    )
