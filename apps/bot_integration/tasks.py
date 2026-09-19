"""Celery tasks for the access reconciliation engine."""
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from .reconcile import reconcile_user_access
from .models import (TelegramAccount, DiscordAccount, UserChannelAssignment,
                     BotAccessAudit)

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def reconcile_user_access_task(self, user_id):
    try:
        reconcile_user_access(user_id)
    except Exception as exc:
        logger.exception("reconcile failed for user %s", user_id)
        raise self.retry(exc=exc)


@shared_task
def periodic_access_sweep():
    """Safety net. Reconcile everyone with a linked platform account, plus
    anyone with a failed access audit in the last 24h (retry failed ops) and
    anyone holding an active assignment but no active account row (orphans).
    Includes Discord-only users (the old sweep only looked at Telegram)."""
    tg_users = set(TelegramAccount.objects.filter(is_active=True)
                   .values_list("user_id", flat=True))
    dc_users = set(DiscordAccount.objects.filter(is_active=True)
                   .values_list("user_id", flat=True))
    failed_users = set(BotAccessAudit.objects.filter(
        status="failed", created_at__gte=timezone.now() - timedelta(hours=24)
    ).values_list("user_id", flat=True))
    orphans = set(UserChannelAssignment.objects.filter(is_active=True).exclude(
        user_id__in=list(tg_users | dc_users)).values_list("user_id", flat=True))

    for user_id in sorted(tg_users | dc_users | failed_users | orphans):
        reconcile_user_access_task.delay(user_id)
