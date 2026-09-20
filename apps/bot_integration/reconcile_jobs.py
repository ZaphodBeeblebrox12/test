"""Reconcile bridge: durable ProvisioningOperation per (user, channel, intent).

The existing diff engine (compute_target_access vs UserChannelAssignment)
decides WHICH operations are needed.  Each needed operation gets ONE open
ProvisioningOperation row; retries reuse its id/link; a NEW lifecycle
(grant after revoke completed) creates a NEW row.
"""

import logging
from django.utils import timezone

from apps.bot_integration.models import (
    BotAccessAudit, TelegramAccount, UserChannelAssignment)
from apps.bot_integration.reconcile import (
    _audit, _control_channel_guard, compute_target_access)
from apps.jobs.models import ProvisioningOperation
from apps.jobs.operations import execute_operation

logger = logging.getLogger(__name__)


class ProvisionTransport:
    """Transport adapter over the existing ProvisionClient."""

    def __init__(self, client=None):
        from apps.bot_integration.services.provision_client import ProvisionClient
        self.client = client or ProvisionClient()

    def create_invite_link(self, telegram_user_id, channel_id):
        # The provision API mints the link as part of grant; we surface the
        # outcome via grant's detail.  Simplified: grant performs the whole
        # Telegram side; here we drive the state machine with the client.
        res = self.client.grant(telegram_user_id, channel_id)
        if res.ok:
            return (res.detail or {}).get("invite_link", "")
        return None

    def send_invite_dm(self, telegram_user_id, invite_link):
        # Grant already DMs the link server-side; treat success as sent.
        return True

    def is_member(self, telegram_user_id, channel_id):
        res = self.client.verify_channel(channel_id)
        return None  # membership check not exposed by contract; conservative

    def revoke(self, telegram_user_id, channel_id, idempotency_key=None):
        return self.client.revoke(telegram_user_id, channel_id)


def _get_or_create_operation(user_id, channel_id, operation, telegram_user_id):
    """Reuse the OPEN operation for this intent; else create a new one."""
    open_states = [ProvisioningOperation.ST_PENDING, ProvisioningOperation.ST_CREATING,
                   ProvisioningOperation.ST_CREATED, ProvisioningOperation.ST_SENDING,
                   ProvisioningOperation.ST_SENT, ProvisioningOperation.ST_UNKNOWN]
    op = (ProvisioningOperation.objects
          .filter(user_id=user_id, channel_id=channel_id, operation=operation,
                  state__in=open_states)
          .order_by("id").first())
    if op is not None:
        return op
    return ProvisioningOperation.objects.create(
        user_id=user_id, channel_id=channel_id, operation=operation,
        telegram_user_id=telegram_user_id)


def _finalize_grant(user_id, channel_id):
    UserChannelAssignment.objects.get_or_create(
        user_id=user_id, platform="telegram", external_id=channel_id,
        defaults={"is_active": True})
    UserChannelAssignment.objects.filter(
        user_id=user_id, platform="telegram", external_id=channel_id,
        is_active=False).update(is_active=True)
    _audit(user_id, "grant", "telegram", channel_id, True, "")


def _finalize_revoke(user_id, channel_id):
    UserChannelAssignment.objects.filter(
        user_id=user_id, platform="telegram", external_id=channel_id,
        is_active=True).update(is_active=False, revoked_at=timezone.now())
    _audit(user_id, "revoke", "telegram", channel_id, True, "")


def run_user_reconcile(user_id: int, transport=None) -> None:
    """Job handler: diff -> operations -> state machine -> finalization.

    Idempotent and convergence-friendly: re-runs recompute the diff; open
    operations are reused; terminal outcomes re-apply harmlessly.
    """
    from apps.bot_integration.models import TelegramAccount
    account = TelegramAccount.objects.filter(user_id=user_id, is_active=True).first()
    if not account or not account.telegram_user_id:
        return
    tg_id = account.telegram_user_id

    target = compute_target_access(user_id)
    existing = set(UserChannelAssignment.objects.filter(
        user_id=user_id, platform="telegram", is_active=True,
    ).values_list("external_id", flat=True))

    wanted = set(target.telegram_ids) if target.has_plan else set()
    guard = _control_channel_guard
    transport = transport or ProvisionTransport()

    # Revokes first (drop access), then grants.
    for channel_id in sorted(existing - wanted):
        if guard(channel_id):
            _audit(user_id, "revoke", "telegram", channel_id, False, guard(channel_id))
            continue
        op = _get_or_create_operation(user_id, channel_id,
                                      ProvisioningOperation.OP_REVOKE, tg_id)
        execute_operation(op, transport)
        if op.state == ProvisioningOperation.ST_COMPLETED:
            _finalize_revoke(user_id, channel_id)
        elif op.state == ProvisioningOperation.ST_FAILED:
            _audit(user_id, "revoke", "telegram", channel_id, False, op.last_error[:500])

    for channel_id in sorted(wanted - existing):
        if guard(channel_id):
            _audit(user_id, "grant", "telegram", channel_id, False, guard(channel_id))
            continue
        op = _get_or_create_operation(user_id, channel_id,
                                      ProvisioningOperation.OP_GRANT, tg_id)
        execute_operation(op, transport)
        if op.state == ProvisioningOperation.ST_COMPLETED:
            _finalize_grant(user_id, channel_id)
        elif op.state == ProvisioningOperation.ST_FAILED:
            _audit(user_id, "grant", "telegram", channel_id, False, op.last_error[:500])

    account.last_synced_at = timezone.now()
    account.save(update_fields=["last_synced_at"])
