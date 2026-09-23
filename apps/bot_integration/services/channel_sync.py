"""Observed channel membership for the dashboard (DISPLAY ONLY).

Checks real Telegram membership via getChatMember for CommunityChannels
(free / bot-administered channels) and caches results in
ChannelMembershipSnapshot. This NEVER drives access control -- entitlements
live in UserChannelAssignment / reconcile. Observed membership only informs
the dashboard and support queries.
"""
import logging
import time

from django.utils import timezone

from ..models import (
    BotAccessAudit,
    ChannelMembershipSnapshot,
    CommunityChannel,
    PlanChannelMapping,
    TelegramAccount,
    UserChannelAssignment,
)
from .telegram import TelegramBotService
from apps.subscriptions.models import Subscription

logger = logging.getLogger(__name__)

MEMBER_STATUSES = ("member", "administrator", "creator")


def sync_channel_memberships_for_user(user_id):
    """Refresh membership snapshots for one user (post-commit hook / sync btn).

    Failures are logged and skipped - a failed channel keeps its last snapshot.
    """
    account = TelegramAccount.objects.filter(
        user_id=user_id, is_active=True).first()
    if account is None:
        return
    tg_id = account.telegram_user_id or account.chat_id
    channels = CommunityChannel.objects.filter(
        is_active=True, platform="telegram")
    for ch in channels:
        try:
            resp = TelegramBotService._api_request(
                "getChatMember",
                {"chat_id": ch.external_id, "user_id": tg_id})
            if not resp.get("ok"):
                logger.info("getChatMember not ok for %s in %s: %s",
                            tg_id, ch.external_id, resp.get("description"))
                continue
            status = (resp.get("result") or {}).get("status", "")
            ChannelMembershipSnapshot.objects.update_or_create(
                user_id=user_id, channel=ch,
                defaults={"is_member": status in MEMBER_STATUSES,
                          "checked_at": timezone.now()})
        except Exception:
            logger.exception("membership check failed user=%s channel=%s",
                             user_id, ch.external_id)


def sync_channel_memberships_window(periodic_job):
    """Trickle sync: consume at most `periodic_job.max_calls_per_run`
    getChatMember calls, resuming from the persisted cursor and wrapping
    around. One PeriodicJob tick = one window. Self-regulating: a 10-call
    budget at 1-min ticks sweeps ~14k calls/day regardless of community size.
    """
    budget = getattr(periodic_job, "max_calls_per_run", 10) or 10
    channels = list(CommunityChannel.objects.filter(
        is_active=True, platform="telegram"))
    if not channels or budget <= 0:
        return 0
    after = (periodic_job.state or {}).get("after_user_id") or None
    calls = 0
    last_id = None
    qs = TelegramAccount.objects.filter(is_active=True)
    if after:
        qs = qs.filter(user_id__gt=after)
    completed = True
    for account in qs.order_by("user_id").iterator():
        if calls + len(channels) > budget:
            completed = False
            break
        sync_channel_memberships_for_user(account.user_id)
        calls += len(channels)
        last_id = account.user_id
    if completed:
        last_id = None  # full pass done -> wrap cursor to the start
    state = dict(periodic_job.state or {})
    state["after_user_id"] = str(last_id) if last_id else None
    periodic_job.state = state
    periodic_job.save(update_fields=["state"])
    return calls


def sync_all_channel_memberships(sleep=0.05):
    """Batch-refresh snapshots for ALL linked accounts (periodic job / command)."""
    accounts = (TelegramAccount.objects.filter(is_active=True)
                .select_related("user").order_by("id"))
    done = 0
    for account in accounts.iterator():
        sync_channel_memberships_for_user(account.user_id)
        done += 1
        if sleep:
            time.sleep(sleep)
    return done


def get_telegram_access_state(user):
    """Derive the pay->access state machine for the dashboard/manage page.

    States: no_subscription | not_linked | ready | pending | needs_action.
    Pure function of: subscription, TelegramAccount, assignments, grant audit.
    """
    from apps.subscriptions.models import Subscription

    subscription = Subscription.objects.filter(
        user=user, status=Subscription.Status.ACTIVE, is_active=True).first()
    if subscription is None:
        return {"state": "no_subscription", "label": "No active subscription",
                "cta_url": "/dashboard/", "cta_label": "View plans",
                "detail": ""}
    account = TelegramAccount.objects.filter(user=user, is_active=True).first()
    if account is None:
        return {"state": "not_linked", "label": "Connect Telegram",
                "cta_url": "/bot/telegram/connect/",
                "cta_label": "Connect Telegram",
                "detail": "Your subscription is active. Connect Telegram to "
                          "unlock your channel access."}
    entitled = set(PlanChannelMapping.objects.filter(
        platform="telegram", plan=subscription.plan).values_list("external_id", flat=True))
    if not entitled:
        return {"state": "ready", "label": "Access ready", "cta_url": "",
                "cta_label": "", "detail": "No channels are mapped to your plan."}
    granted = set(UserChannelAssignment.objects.filter(
        user=user, platform="telegram", is_active=True).values_list("external_id", flat=True))
    if entitled <= granted:
        return {"state": "ready", "label": "Access ready", "cta_url": "",
                "cta_label": "",
                "detail": "Your Telegram access is active. Check Telegram."}
    failed_recently = BotAccessAudit.objects.filter(
        user=user, action="grant", platform="telegram", status="failed",
        created_at__gte=timezone.now() - timezone.timedelta(hours=24)).exists()
    if failed_recently:
        return {"state": "needs_action", "label": "Needs action",
                "cta_url": "/support/",
                "cta_label": "Get help",
                "detail": "We couldn't add you to a channel. Open the bot and "
                          "press Start, check Telegram privacy settings, or "
                          "contact support."}
    return {"state": "pending", "label": "Connecting now", "cta_url": "",
            "cta_label": "",
            "detail": "We're connecting your Telegram access now - usually "
                      "within a few minutes."}


def build_channel_display(user):
    """Dashboard data: free channels (observed truth) + paid (entitlement).

    Free list  - from ChannelMembershipSnapshot (real Telegram state).
    Paid list  - from PlanChannelMapping + active subscription + assignments.
    """
    snapshots = {
        s.channel_id: s for s in
        ChannelMembershipSnapshot.objects.filter(
            user_id=user.id).select_related("channel")
    }
    free = []
    for ch in (CommunityChannel.objects
               .filter(is_active=True, platform="telegram")
               .order_by("name")):
        snap = snapshots.get(ch.id)
        free.append({
            "name": ch.name or ch.external_id,
            "is_member": bool(snap and snap.is_member),
            "checked": bool(snap),
            "invite_url": ch.invite_url or "",
        })
    active_plan_ids = set(Subscription.objects.filter(
        user=user, status=Subscription.Status.ACTIVE, is_active=True,
    ).values_list("plan_id", flat=True))
    assigned_ids = set(UserChannelAssignment.objects.filter(
        user=user, platform="telegram", is_active=True,
    ).values_list("external_id", flat=True))
    paid = []
    for m in (PlanChannelMapping.objects
              .filter(platform="telegram")
              .select_related("plan")
              .order_by("plan__display_order", "name")):
        entitled = m.plan_id in active_plan_ids
        paid.append({
            "name": m.name or m.external_id,
            "plan_name": m.plan.name,
            "entitled": entitled,
            "assigned": m.external_id in assigned_ids,
        })
    return {"free": free, "paid": paid}
