"""Built-in non-reconcile job handlers (sweep / referrals / MaxMind)."""

import logging
from django.utils import timezone

from .models import Job
from .worker import HANDLERS, claim_next, process_job
from .enqueue import enqueue_generic

logger = logging.getLogger(__name__)


def _sweep(payload, job):
    from django.contrib.auth import get_user_model
    from apps.jobs.enqueue import enqueue_reconcile
    User = get_user_model()
    for uid in (User.objects.filter(subscription__status="active",
                                    subscription__is_active=True)
                .values_list("id", flat=True).distinct()):
        enqueue_reconcile(uid, reason="sweep")
    for uid in (User.objects.filter(telegram_account__is_active=True)
                .values_list("id", flat=True).distinct()):
        enqueue_reconcile(uid, reason="sweep")


def _referrals(payload, job):
    from apps.growth.tasks import unlock_pending_referral_rewards as fn
    fn()   # plain function call; the former shared_task body


def _maxmind(payload, job):
    from apps.subscriptions.tasks import update_maxmind_database_task as fn
    fn()


def register_builtin_handlers():
    HANDLERS.setdefault("sweep", _sweep)
    HANDLERS.setdefault("referral_unlock", _referrals)
    HANDLERS.setdefault("update_maxmind", _maxmind)


def schedule_periodic():
    """Enqueue periodic jobs idempotently (open-job suppression)."""
    enqueue_generic("update_maxmind", {}, idempotency_key="sched:update_maxmind")
    enqueue_generic("referral_unlock", {}, idempotency_key="sched:referral_unlock")
