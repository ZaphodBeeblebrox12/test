"""Desired-access computation (pure reads, no platform calls).

Single source of truth for what a user SHOULD have. Used by the reconcile
engine to diff against current assignments."""
from dataclasses import dataclass, field

from .models import PlanChannelMapping
from apps.subscriptions.models import Subscription


@dataclass
class AccessTarget:
    has_plan: bool = False
    telegram_ids: set = field(default_factory=set)
    discord_ids: set = field(default_factory=set)


def get_active_plan(user_id):
    return (Subscription.objects
            .filter(user_id=user_id, is_active=True, status="active")
            .select_related("plan")
            .order_by("-created_at")
            .first())


def compute_target_access(user_id) -> AccessTarget:
    sub = get_active_plan(user_id)          # active Subscription (or None)
    target = AccessTarget(has_plan=sub is not None)
    if sub:
        # PlanChannelMapping.plan FKs to subscriptions.Plan, not Subscription —
        # pass the related Plan instance, not the Subscription.
        for platform, external_id in PlanChannelMapping.objects.filter(
                plan=sub.plan).values_list("platform", "external_id"):
            if platform == "telegram":
                target.telegram_ids.add(external_id)
            elif platform == "discord":
                target.discord_ids.add(external_id)
    return target
