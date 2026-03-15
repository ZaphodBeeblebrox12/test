"""
Notifications context processors.
"""
from apps.notifications.models import Notification


def unread_notification_count(request):
    """
    Add unread notification count to template context.
    Available in all templates as {{ unread_count }}.
    """
    if request.user.is_authenticated:
        count = Notification.objects.filter(
            user=request.user,
            is_read=False
        ).count()
    else:
        count = 0
    return {"unread_count": count}
