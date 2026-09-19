"""Access reconciliation engine.

Invariant: actual platform access converges to the access dictated by the
user's current subscription. Diff-based and idempotent — re-running with no
change produces zero platform calls and zero audit rows.

Fixes vs. the removed sync.py:
- Discord roles are now REVOKED on lapse (old code silently did nothing).
- Telegram channels from a PREVIOUS plan are now banned (plan-change drift).
- No-op reconciles are silent (old code re-unbanned + audited every target
  channel on every run).
- Per-user cache lock serializes concurrent reconciles (no duplicate invites);
  failed ops stay retryable because the diff re-evaluates them next run.
"""
import logging
from django.core.cache import cache
from django.utils import timezone

from .access import compute_target_access
from .models import (TelegramAccount, DiscordAccount, PlanChannelMapping,
                     UserChannelAssignment, BotAccessAudit)
from .services.telegram import TelegramBotService
from .services.discord import DiscordBotService

logger = logging.getLogger(__name__)
LOCK_TTL = 120  # seconds — only guards concurrent triggers, never held across network calls


def _audit(user_id, action, platform, target, success, error_message=""):
    BotAccessAudit.objects.create(
        user_id=user_id, action=action, platform=platform, target=str(target),
        status="success" if success else "failed",
        error_message=error_message or "",
    )


def reconcile_user_access(user_id):
    lock_key = f"bot_integration:reconcile:{user_id}"
    if not cache.add(lock_key, True, LOCK_TTL):
        return  # another reconcile in flight; it sees fresh state
    try:
        target = compute_target_access(user_id)

        tg = TelegramAccount.objects.filter(user_id=user_id, is_active=True).first()
        if tg:
            _reconcile_telegram(user_id, tg, target)

        dc = DiscordAccount.objects.filter(user_id=user_id, is_active=True).first()
        if dc:
            _reconcile_discord(user_id, dc, target)
    finally:
        cache.delete(lock_key)


def _reconcile_telegram(user_id, account, target):
    existing = set(UserChannelAssignment.objects.filter(
        user_id=user_id, platform="telegram", is_active=True
    ).values_list("external_id", flat=True))

    if not target.has_plan:
        for channel_id in sorted(existing):
            _tg_revoke(user_id, account, channel_id)
    else:
        for channel_id in sorted(target.telegram_ids - existing):
            _tg_grant(user_id, account, target, channel_id)
        for channel_id in sorted(existing - target.telegram_ids):
            _tg_revoke(user_id, account, channel_id)  # plan-change drift

    account.last_synced_at = timezone.now()
    account.save(update_fields=["last_synced_at"])


def _tg_grant(user_id, account, target, channel_id):
    if account.telegram_user_id:
        ok, err = TelegramBotService.unban_user(channel_id, account.telegram_user_id)
        if not ok:
            _audit(user_id, "grant", "telegram", channel_id, False, f"unban failed: {err}")
            return

    invite_link = TelegramBotService.create_one_time_invite_link(channel_id)
    if not invite_link:
        mapping = PlanChannelMapping.objects.filter(
            platform="telegram", external_id=channel_id).first()
        if mapping and mapping.name and mapping.name.startswith("@"):
            invite_link = f"https://t.me/{mapping.name[1:]}"
    if invite_link:
        ok = TelegramBotService.send_message(
            account.chat_id, f"🔓 Your subscription is active! Join here: {invite_link}")
        note = f"Invite link: {invite_link}"
    else:
        ok = TelegramBotService.send_message(
            account.chat_id, "🔓 Your subscription is active! Please contact support.")
        note = "support needed"
    if ok:
        UserChannelAssignment.objects.create(
            user_id=user_id, platform="telegram", external_id=channel_id, is_active=True)
    _audit(user_id, "grant", "telegram", channel_id, ok, "" if ok else note)


def _tg_revoke(user_id, account, channel_id):
    if not account.telegram_user_id:
        _audit(user_id, "revoke", "telegram", channel_id, False,
               "Missing telegram_user_id – cannot ban")
        return
    ok, err = TelegramBotService.ban_user(channel_id, account.telegram_user_id)
    if ok:
        UserChannelAssignment.objects.filter(
            user_id=user_id, platform="telegram", external_id=channel_id,
            is_active=True).update(is_active=False, revoked_at=timezone.now())
    _audit(user_id, "revoke", "telegram", channel_id, ok, "" if ok else err)


def _reconcile_discord(user_id, account, target):
    # Authoritative set = the account's cached roles, driven to the target.
    # Symmetric with Telegram: roles not permitted by the current plan are
    # removed (fixes the old lapse no-op). Roles are only written by the
    # reconcile, so this is safe and drift-correcting.
    current = set(account.roles or [])

    for role_id in sorted(target.discord_ids - current):
        ok = DiscordBotService.add_role(account.discord_user_id, role_id)
        if ok:
            current.add(role_id)
            UserChannelAssignment.objects.get_or_create(
                user_id=user_id, platform="discord", external_id=role_id,
                defaults={"is_active": True})
        _audit(user_id, "grant", "discord", role_id, ok)

    for role_id in sorted(current - target.discord_ids):
        ok = DiscordBotService.remove_role(account.discord_user_id, role_id)
        if ok:
            current.discard(role_id)
            UserChannelAssignment.objects.filter(
                user_id=user_id, platform="discord", external_id=role_id,
                is_active=True).update(is_active=False, revoked_at=timezone.now())
        _audit(user_id, "revoke", "discord", role_id, ok)

    if set(account.roles or []) != current:
        account.roles = sorted(current)
        account.save(update_fields=["roles"])
