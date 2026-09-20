"""Provision access on subscription activation/deactivation transitions only.

The old post_save fired a full reconcile on EVERY save of an active
subscription (including the expiry task's routine touches). This version
stashes the pre-save status and reconciles only on the activation or
deactivation edge, so routine field updates no longer trigger platform calls.
"""
import logging

from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver

from apps.subscriptions.models import Subscription
from apps.jobs.enqueue import enqueue_reconcile

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Subscription)
def _stash_old_status(sender, instance, **kwargs):
    instance._old_status = None
    if instance.pk:
        instance._old_status = Subscription.objects.filter(
            pk=instance.pk).values_list("status", flat=True).first()


@receiver(post_save, sender=Subscription)
def provision_on_transition(sender, instance, created, **kwargs):
    now_active = instance.status == "active" and instance.is_active
    was_active = getattr(instance, "_old_status", None) == "active"
    if (now_active and not was_active) or (was_active and not now_active):
        # Durability without querying uncommitted state: the job insert runs
        # after this save's transaction commits (same-transaction outbox is
        # guaranteed at the service layer; here we avoid uncommitted-state
        # races inside the signal).
        from django.db import transaction as _tx
        _tx.on_commit(lambda: enqueue_reconcile(
            instance.user_id, reason="subscription_transition"))
