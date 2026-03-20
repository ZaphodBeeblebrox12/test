"""
Notification signal handlers for subscription events.

These handlers connect to Subscription signals and create appropriate notifications.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from datetime import timedelta

from apps.subscriptions.models import Subscription, SubscriptionHistory
from apps.notifications.models import Notification
from apps.notifications.helpers import create_notification


@receiver(post_save, sender=Subscription)
def handle_subscription_status_change(sender, instance, created, **kwargs):
    """
    Handle subscription status changes and create appropriate notifications.

    Trigger points:
    - Subscription enters grace period (status change)
    - Subscription expires (status = EXPIRED)
    - Subscription upgraded (plan change with upgrade metadata)
    - Gift subscription granted (is_gift flag)
    """
    if created:
        # New subscription created
        if hasattr(instance, 'is_gift') and instance.is_gift:
            # Gift subscription notification
            create_notification(
                user=instance.user,
                notification_type=Notification.NotificationType.GIFT_RECEIVED,
                title="Gift Subscription Received! 🎁",
                message=f"You've received a gift subscription to {instance.plan.name}.",
                metadata={
                    'plan_id': str(instance.plan.id),
                    'plan_name': instance.plan.name,
                    'subscription_id': str(instance.id),
                }
            )
        return

    # Check for grace period entry (status change to PENDING with is_active)
    if instance.status == Subscription.Status.PENDING and instance.is_active:
        # Subscription in grace period
        create_notification(
            user=instance.user,
            notification_type=Notification.NotificationType.GRACE_PERIOD,
            title="Payment Required - Grace Period Active",
            message=f"Your {instance.plan.name} subscription is in grace period. Please update your payment method to avoid interruption.",
            metadata={
                'plan_id': str(instance.plan.id),
                'plan_name': instance.plan.name,
                'subscription_id': str(instance.id),
                'grace_period_end': instance.expires_at.isoformat() if instance.expires_at else None,
            }
        )

    # Check for subscription expired
    if instance.status == Subscription.Status.EXPIRED:
        # Subscription has expired - create urgent notification
        create_notification(
            user=instance.user,
            notification_type=Notification.NotificationType.SUBSCRIPTION_EXPIRED,
            title="Subscription Expired",
            message=f"Your {instance.plan.name} subscription has expired. Renew now to restore access.",
            metadata={
                'plan_id': str(instance.plan.id),
                'plan_name': instance.plan.name,
                'subscription_id': str(instance.id),
                'expired_at': instance.expires_at.isoformat() if instance.expires_at else timezone.now().isoformat(),
            }
        )

    # Check for downgrade scheduled (if you have scheduled downgrade logic)
    # This would be triggered by a scheduled task or status flag


@receiver(post_save, sender=SubscriptionHistory)
def handle_subscription_history_event(sender, instance, created, **kwargs):
    """
    Handle subscription history events and create notifications.

    Trigger points:
    - Plan upgraded
    - Plan downgraded
    """
    if not created:
        return

    if instance.event_type == SubscriptionHistory.EventType.UPGRADED:
        create_notification(
            user=instance.user,
            notification_type=Notification.NotificationType.PLAN_UPGRADED,
            title="Plan Upgraded! 🎉",
            message=f"Your subscription has been upgraded. Enjoy your new features!",
            metadata={
                'subscription_id': str(instance.subscription.id),
                'previous_plan_id': str(instance.previous_plan_id) if instance.previous_plan_id else None,
                'new_plan_id': str(instance.new_plan_id) if instance.new_plan_id else None,
                'event_type': instance.event_type,
            }
        )


def check_expiration_warnings():
    """
    Check for subscriptions expiring soon and create warning notifications.

    This should be called by a periodic task (celery beat or cron).

    Trigger points:
    - 3 days before expiration
    - 1 day before expiration
    """
    now = timezone.now()

    # 3 days warning
    three_days_from_now = now + timedelta(days=3)
    three_day_window_start = three_days_from_now - timedelta(hours=1)

    subscriptions_3_days = Subscription.objects.filter(
        status=Subscription.Status.ACTIVE,
        expires_at__gte=three_day_window_start,
        expires_at__lte=three_days_from_now,
        is_active=True
    )

    for sub in subscriptions_3_days:
        # Check if notification already exists (idempotent via create_notification)
        create_notification(
            user=sub.user,
            notification_type=Notification.NotificationType.EXPIRATION_WARNING,
            title="Subscription Expiring Soon",
            message=f"Your {sub.plan.name} subscription expires in 3 days. Renew now to avoid interruption.",
            metadata={
                'plan_id': str(sub.plan.id),
                'plan_name': sub.plan.name,
                'subscription_id': str(sub.id),
                'days_remaining': 3,
                'expires_at': sub.expires_at.isoformat() if sub.expires_at else None,
            }
        )

    # 1 day warning
    one_day_from_now = now + timedelta(days=1)
    one_day_window_start = one_day_from_now - timedelta(hours=1)

    subscriptions_1_day = Subscription.objects.filter(
        status=Subscription.Status.ACTIVE,
        expires_at__gte=one_day_window_start,
        expires_at__lte=one_day_from_now,
        is_active=True
    )

    for sub in subscriptions_1_day:
        create_notification(
            user=sub.user,
            notification_type=Notification.NotificationType.EXPIRATION_WARNING,
            title="Subscription Expires Tomorrow",
            message=f"Your {sub.plan.name} subscription expires tomorrow. Renew now to avoid interruption.",
            metadata={
                'plan_id': str(sub.plan.id),
                'plan_name': sub.plan.name,
                'subscription_id': str(sub.id),
                'days_remaining': 1,
                'expires_at': sub.expires_at.isoformat() if sub.expires_at else None,
            }
        )


def create_platform_connected_notification(user, platform: str):
    """
    Create notification when user connects a platform (Discord/Telegram).

    Args:
        user: The user who connected the platform
        platform: Name of the platform ("Discord" or "Telegram")
    """
    create_notification(
        user=user,
        notification_type=Notification.NotificationType.PLATFORM_CONNECTED,
        title=f"{platform} Connected",
        message=f"Your {platform} account has been successfully connected.",
        metadata={
            'platform': platform.lower(),
        }
    )


def create_downgrade_scheduled_notification(subscription, downgrade_date):
    """
    Create notification when a downgrade is scheduled.

    Args:
        subscription: The subscription being downgraded
        downgrade_date: When the downgrade will take effect
    """
    create_notification(
        user=subscription.user,
        notification_type=Notification.NotificationType.DOWNGRADE_SCHEDULED,
        title="Downgrade Scheduled",
        message=f"Your subscription will be downgraded on {downgrade_date.strftime('%Y-%m-%d')}.",
        metadata={
            'subscription_id': str(subscription.id),
            'downgrade_date': downgrade_date.isoformat(),
            'current_plan': subscription.plan.name,
        }
    )
