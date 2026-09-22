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
from .models import TelegramAccount
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


# ── Stage 2: transactional/system messages on the same durable edges ──────
# Reuses the _old_status stash above. TRANSACTIONAL, not marketing: no
# consent gate is (or may be) applied to these messages. Sends run via
# transaction.on_commit so they never fire for rolled-back payments.


@receiver(post_save, sender=TelegramAccount)
def transactional_welcome_on_link(sender, instance, created, **kwargs):
    """Welcome DM when a user links their Telegram account. Transactional."""
    if not created:
        return
    from django.db import transaction as _tx
    from .transactional import TransactionalTelegramService
    _tx.on_commit(lambda: TransactionalTelegramService.send_welcome(instance.user))


def _plan_label(subscription) -> str:
    plan = getattr(subscription, "plan", None)
    for attr in ("display_name", "name", "title"):
        value = getattr(plan, attr, None)
        if value:
            return str(value)
    return "your plan"


@receiver(post_save, sender=Subscription)
def transactional_message_on_transition(sender, instance, created, **kwargs):
    """Activation/expiry edge messages. Mirrors the activation semantics of
    provision_on_transition (status == active AND is_active). Routine saves
    and renewals (status stays active) do not message."""
    old_status = getattr(instance, "_old_status", None)
    new_status = instance.status
    now_active = new_status == "active" and instance.is_active
    was_active = old_status == "active"
    if now_active == was_active and not created:
        return
    from django.db import transaction as _tx
    from .transactional import TransactionalTelegramService
    if now_active and not was_active:
        label = _plan_label(instance)
        _tx.on_commit(
            lambda: TransactionalTelegramService.send_subscription_activated(
                instance.user, plan_name=label))
    elif new_status == "expired" and was_active:
        label = _plan_label(instance)
        _tx.on_commit(
            lambda: TransactionalTelegramService.send_subscription_expired(
                instance.user, plan_name=label))
