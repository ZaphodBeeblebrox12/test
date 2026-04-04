from celery import shared_task
import logging
from django.db.models import Q
from django.utils import timezone
from .sync import sync_user_channels
from .models import TelegramAccount

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def sync_user_channels_task(self, user_id):
    """Celery task to sync a single user's bot channels."""
    try:
        sync_user_channels(user_id)
    except Exception as exc:
        logger.error(f"Sync failed for user {user_id}: {exc}")
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@shared_task
def periodic_sync_all_users():
    """Periodic task to reconcile users that haven't been synced recently."""
    cutoff = timezone.now() - timezone.timedelta(hours=24)
    stale_users = TelegramAccount.objects.filter(
        is_active=True
    ).filter(
        Q(last_synced_at__isnull=True) | Q(last_synced_at__lt=cutoff)
    ).values_list('user_id', flat=True)
    
    for user_id in stale_users:
        sync_user_channels_task.delay(user_id)