from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.subscriptions.models import Subscription
from .tasks import sync_user_channels_task


@receiver(post_save, sender=Subscription)
def subscription_post_save_handler(sender, instance, **kwargs):
    """Trigger sync when a subscription becomes active."""
    if instance.is_active and instance.status == Subscription.Status.ACTIVE:
        sync_user_channels_task.delay(instance.user_id)