"""G5 analytics: read-only reports over the durable Event store + deliveries.

No materialized cubes: these are efficient grouped queries over authoritative
records (Events for purchases, Deliveries for sends, CampaignMetrics for
per-campaign counters, CouponRedemptions for promotion revenue).
"""
import datetime

from django.db.models import Count
from django.utils import timezone

from apps.campaigns.models import Campaign, CampaignMetrics
from apps.emailing.models import Delivery
from apps.events.models import Event
from apps.promotions.models import CouponRedemption


def signup_analytics(days=30):
    """Signups per day (purchase.completed not required; uses Event store for
    trials + a user count is derived from purchase Events).  Here: purchases
    per day + revenue per day."""
    since = timezone.now() - datetime.timedelta(days=days)
    from django.db.models.functions import TruncDate
    groups = (Event.objects.filter(event_type="purchase.completed", occurred_at__gte=since)
              .annotate(day=TruncDate("occurred_at"))
              .values("day").annotate(count=Count("id")).order_by("day"))
    # Revenue summed in Python: payload.amount is a JSONField (no DB Sum).
    per_day = []
    for g in groups:
        evs = Event.objects.filter(
            event_type="purchase.completed", occurred_at__gte=since,
            occurred_at__date=g["day"]).values_list("payload", flat=True)
        revenue = sum(int(e.get("amount", 0)) for e in evs)
        per_day.append({"day": str(g["day"]), "purchases": g["count"], "revenue_cents": revenue})
    return {
        "period_days": days,
        "purchases_per_day": per_day,
        "total_purchases": Event.objects.filter(
            event_type="purchase.completed", occurred_at__gte=since).count(),
    }


def email_performance(days=30):
    """Delivery outcomes over the window (authoritative Delivery states)."""
    since = timezone.now() - datetime.timedelta(days=days)
    base = Delivery.objects.filter(created_at__gte=since)
    def _c(state):
        return base.filter(state=state).count()
    return {
        "period_days": days,
        "sent": _c(Delivery.State.SENT),
        "failed": _c(Delivery.State.FAILED),
        "bounced": _c(Delivery.State.BOUNCED),
        "complained": _c(Delivery.State.COMPLAINED),
        "suppressed": _c(Delivery.State.SUPPRESSED),
    }


def campaign_performance():
    """Per-campaign counters from the O(1) CampaignMetrics (G2-maintained)."""
    return [
        {
            "campaign_id": str(m.campaign_id),
            "campaign": m.campaign.name,
            "total": m.total, "sent": m.sent, "failed": m.failed,
            "bounced": m.bounced, "complained": m.complained, "suppressed": m.suppressed,
        }
        for m in CampaignMetrics.objects.select_related("campaign").all()
    ]


def promotion_performance():
    """Coupon usage + discounted revenue from authoritative redemptions."""
    rows = (CouponRedemption.objects.values("coupon__code")
            .annotate(redemptions=Count("id"), total_discount=Sum("discount_cents"))
            .order_by("-redemptions"))
    return [
        {"code": r["coupon__code"], "redemptions": r["redemptions"],
         "total_discount_cents": r["total_discount"] or 0}
        for r in rows
    ]


def referral_performance():
    """Completed referrals + rewards (from the Event store's purchase.completed
    plus the growth Referral model)."""
    from apps.growth.models import Referral, ReferralReward
    return {
        "referrals_completed": Referral.objects.filter(status=Referral.Status.COMPLETED).count(),
        "rewards_pending": ReferralReward.objects.filter(status="pending").count(),
        "rewards_credited": ReferralReward.objects.filter(status="credited").count(),
    }
