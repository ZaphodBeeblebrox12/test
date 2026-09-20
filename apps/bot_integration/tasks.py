"""Synchronous service functions (former Celery task bodies).

These are invoked by the Django-native job worker (apps.jobs) via handlers.
"""


def reconcile_user_access_task(user_id):
    from .reconcile import reconcile_user_access
    return reconcile_user_access(user_id)


def periodic_access_sweep():
    """Safety-net sweep semantics (telegram+discord users, failed audits,
    orphans) — enqueues reconcile jobs via the jobs app."""
    from datetime import timedelta
    from django.utils import timezone
    from apps.jobs.enqueue import enqueue_reconcile
    from .models import (TelegramAccount, DiscordAccount, UserChannelAssignment,
                         BotAccessAudit)

    tg = set(TelegramAccount.objects.filter(is_active=True)
             .values_list("user_id", flat=True))
    dc = set(DiscordAccount.objects.filter(is_active=True)
             .values_list("user_id", flat=True))
    failed = set(BotAccessAudit.objects.filter(
        status="failed", created_at__gte=timezone.now() - timedelta(hours=24)
    ).values_list("user_id", flat=True))
    orphans = set(UserChannelAssignment.objects.filter(is_active=True).exclude(
        user_id__in=list(tg | dc)).values_list("user_id", flat=True))

    for uid in sorted(tg | dc | failed | orphans):
        enqueue_reconcile(uid, reason="sweep")
