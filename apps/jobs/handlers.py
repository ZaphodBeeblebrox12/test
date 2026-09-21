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


def _automation_fire(payload, job):
    """Durable job: fire due automation runs."""
    from apps.automation.services.automation import fire_due_runs

    return fire_due_runs()


def _campaign_send(payload, job):
    """Durable job: send a batch of a campaign to its claimed recipients."""
    from apps.campaigns.services.campaigns import campaign_send_sweep

    campaign_id = (payload or {}).get("campaign_id")
    return campaign_send_sweep(campaign_id)


def _process_webhook_event(payload, job):
    """Durable job: process a stored WebhookEvent idempotently."""
    from apps.payments.webhook_processor import process_webhook_event

    process_webhook_event(payload, job)


def _subscription_expiry(payload, job):
    """Durable job: expire all active subscriptions past expires_at.

    The worker marks the job succeeded/failed around this handler (same as
    _sweep); the handler only performs the idempotent expiry sweep.
    """
    from apps.subscriptions.services import expire_due_subscriptions

    return {"expired": expire_due_subscriptions()}


def register_builtin_handlers():
    HANDLERS.setdefault("sweep", _sweep)
    HANDLERS.setdefault("subscription_expiry", _subscription_expiry)
    HANDLERS.setdefault("process_webhook_event", _process_webhook_event)
    HANDLERS.setdefault("campaign_send", _campaign_send)
    HANDLERS.setdefault("automation_fire", _automation_fire)
    HANDLERS.setdefault("referral_unlock", _referrals)
    HANDLERS.setdefault("update_maxmind", _maxmind)


def schedule_periodic():
    """Enqueue periodic jobs idempotently (open-job suppression)."""
    enqueue_generic("update_maxmind", {}, idempotency_key="sched:update_maxmind")
    enqueue_generic("referral_unlock", {}, idempotency_key="sched:referral_unlock")
    enqueue_generic("subscription_expiry", {}, idempotency_key="sched:subscription_expiry")
