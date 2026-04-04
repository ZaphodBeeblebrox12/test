import logging
from django.db import transaction
from django.utils import timezone
from .models import (
    TelegramAccount, DiscordAccount, PlanChannelMapping,
    UserChannelAssignment, BotAccessAudit
)
from .services.telegram import TelegramBotService
from .services.discord import DiscordBotService
from apps.subscriptions.models import Subscription

logger = logging.getLogger(__name__)


def sync_user_channels(user_id):
    """
    Idempotent sync: ensures user has exactly the Telegram invites and Discord roles
    required by their current active subscription plan.
    """
    # Get accounts (may not exist)
    tg_account = TelegramAccount.objects.filter(user_id=user_id, is_active=True).first()
    discord_account = DiscordAccount.objects.filter(user_id=user_id, is_active=True).first()

    if not tg_account and not discord_account:
        return

    # Determine active plan
    try:
        sub = Subscription.objects.filter(
            user_id=user_id, is_active=True, status='active'
        ).select_related('plan').latest('created_at')
        plan = sub.plan
    except Subscription.DoesNotExist:
        plan = None

    # Get target mappings
    mappings = PlanChannelMapping.objects.filter(plan=plan) if plan else []
    target_telegram = {m.external_id for m in mappings if m.platform == 'telegram'}
    target_discord = {m.external_id for m in mappings if m.platform == 'discord'}

    # ---- TELEGRAM REVOCATION (no active plan) ----
    if tg_account and not plan:
        active_assignments = list(UserChannelAssignment.objects.filter(
            user_id=user_id, platform='telegram', is_active=True
        ))
        for assignment in active_assignments:
            if not tg_account.telegram_user_id:
                BotAccessAudit.objects.create(
                    user_id=user_id, action='revoke', platform='telegram',
                    target=assignment.external_id, status='failed',
                    error_message='Missing telegram_user_id – cannot ban'
                )
                continue

            success, err_msg = TelegramBotService.ban_user(
                assignment.external_id, tg_account.telegram_user_id
            )
            if success:
                assignment.is_active = False
                assignment.revoked_at = timezone.now()
                assignment.save(update_fields=['is_active', 'revoked_at'])
            BotAccessAudit.objects.create(
                user_id=user_id, action='revoke', platform='telegram',
                target=assignment.external_id,
                status='success' if success else 'failed',
                error_message=err_msg if not success else ''
            )

        tg_account.last_synced_at = timezone.now()
        tg_account.save(update_fields=['last_synced_at'])
        # No further Telegram processing for this user
        tg_account = None

    # ---- TELEGRAM GRANT (active plan) ----
    if tg_account and plan:
        # Unban first
        for channel_id in target_telegram:
            if tg_account.telegram_user_id:
                success, err_msg = TelegramBotService.unban_user(channel_id, tg_account.telegram_user_id)
                BotAccessAudit.objects.create(
                    user_id=user_id, action='grant', platform='telegram',
                    target=channel_id, status='success' if success else 'failed',
                    error_message=err_msg if not success else ''
                )

        # Send invites only for channels not already active
        existing_active = set(
            UserChannelAssignment.objects.filter(
                user_id=user_id, platform='telegram', is_active=True
            ).values_list('external_id', flat=True)
        )
        missing = target_telegram - existing_active

        for channel_id in missing:
            invite_link = TelegramBotService.create_one_time_invite_link(channel_id)
            if not invite_link:
                mapping = PlanChannelMapping.objects.filter(
                    plan=plan, platform='telegram', external_id=channel_id
                ).first()
                if mapping and mapping.name and mapping.name.startswith('@'):
                    invite_link = f"https://t.me/{mapping.name[1:]}"
                else:
                    invite_link = None

            if invite_link:
                success = TelegramBotService.send_message(
                    tg_account.chat_id,
                    f"🔓 Your subscription is active! Join here: {invite_link}"
                )
            else:
                success = TelegramBotService.send_message(
                    tg_account.chat_id,
                    "🔓 Your subscription is active! Please contact support to receive the channel link."
                )
                invite_link = "support needed"

            if success:
                UserChannelAssignment.objects.create(
                    user_id=user_id, platform='telegram', external_id=channel_id, is_active=True
                )
            BotAccessAudit.objects.create(
                user_id=user_id, action='grant', platform='telegram',
                target=channel_id, status='success' if success else 'failed',
                error_message=f"Invite link: {invite_link}" if success else "Failed to send message"
            )

        tg_account.last_synced_at = timezone.now()
        tg_account.save(update_fields=['last_synced_at'])

    # ---- DISCORD ----
    if discord_account and plan:
        current_roles = set(discord_account.roles)
        to_add = target_discord - current_roles
        to_remove = current_roles - target_discord

        for role_id in to_add:
            success = DiscordBotService.add_role(discord_account.discord_user_id, role_id)
            if success:
                current_roles.add(role_id)
                UserChannelAssignment.objects.get_or_create(
                    user_id=user_id, platform='discord', external_id=role_id,
                    defaults={'is_active': True}
                )
            BotAccessAudit.objects.create(
                user_id=user_id, action='grant', platform='discord',
                target=role_id, status='success' if success else 'failed'
            )
        for role_id in to_remove:
            success = DiscordBotService.remove_role(discord_account.discord_user_id, role_id)
            if success:
                current_roles.discard(role_id)
                UserChannelAssignment.objects.filter(
                    user_id=user_id, platform='discord', external_id=role_id
                ).update(is_active=False, revoked_at=timezone.now())
            BotAccessAudit.objects.create(
                user_id=user_id, action='revoke', platform='discord',
                target=role_id, status='success' if success else 'failed'
            )
        discord_account.roles = list(current_roles)
        discord_account.save(update_fields=['roles'])

    elif discord_account and not plan:
        # Optionally revoke all Discord roles (you can add similar logic)
        pass