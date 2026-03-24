"""
Subscription services for geo pricing, gifts, admin grants, and events.
"""
import uuid
from typing import Optional, Dict, Any

from django.utils import timezone
from django.conf import settings
from django.db import transaction
from ipware import get_client_ip

from apps.accounts.models import User
from .models import Plan, PlanPrice, Subscription, SubscriptionHistory, UpgradeHistory, GiftSubscription, GeoPlanPrice


# =============================================================================
# REGION MAPPING
# =============================================================================

COUNTRY_TO_REGION = {
    # APAC
    "IN": "APAC", "CN": "APAC", "JP": "APAC", "KR": "APAC", "SG": "APAC",
    "AU": "APAC", "NZ": "APAC", "TH": "APAC", "VN": "APAC", "MY": "APAC",
    "ID": "APAC", "PH": "APAC", "HK": "APAC", "TW": "APAC", "BD": "APAC",
    "PK": "APAC", "LK": "APAC", "NP": "APAC",
    # EU
    "DE": "EU", "FR": "EU", "IT": "EU", "ES": "EU", "NL": "EU", "BE": "EU",
    "AT": "EU", "PT": "EU", "GR": "EU", "IE": "EU", "FI": "EU", "SE": "EU",
    "DK": "EU", "NO": "EU", "PL": "EU", "CZ": "EU", "HU": "EU", "RO": "EU",
    "BG": "EU", "HR": "EU", "SI": "EU", "SK": "EU", "EE": "EU", "LV": "EU",
    "LT": "EU", "LU": "EU", "MT": "EU", "CY": "EU", "CH": "EU", "GB": "EU",
    # NA
    "US": "NA", "CA": "NA", "MX": "NA",
    # LATAM
    "BR": "LATAM", "AR": "LATAM", "CL": "LATAM", "CO": "LATAM", "PE": "LATAM",
    "VE": "LATAM", "EC": "LATAM", "BO": "LATAM", "PY": "LATAM", "UY": "LATAM",
    "CR": "LATAM", "PA": "LATAM", "GT": "LATAM", "HN": "LATAM", "SV": "LATAM",
    "NI": "LATAM", "DO": "LATAM", "JM": "LATAM", "TT": "LATAM",
    # MENA
    "AE": "MENA", "SA": "MENA", "QA": "MENA", "KW": "MENA", "BH": "MENA",
    "OM": "MENA", "JO": "MENA", "LB": "MENA", "IL": "MENA", "EG": "MENA",
    "MA": "MENA", "TN": "MENA", "DZ": "MENA", "LY": "MENA", "IQ": "MENA",
    "IR": "MENA", "TR": "MENA",
    # SSA
    "ZA": "SSA", "NG": "SSA", "KE": "SSA", "GH": "SSA", "UG": "SSA",
    "TZ": "SSA", "ZW": "SSA", "ZM": "SSA", "MW": "SSA", "MZ": "SSA",
    "NA": "SSA", "BW": "SSA", "SZ": "SSA", "LS": "SSA", "RW": "SSA",
    "ET": "SSA", "SN": "SSA", "CI": "SSA", "CM": "SSA", "AO": "SSA",
    "CD": "SSA", "CG": "SSA", "GA": "SSA", "GQ": "SSA",
}


def get_region_for_country(country_code: str) -> Optional[str]:
    """Get region for a country code."""
    return COUNTRY_TO_REGION.get(country_code.upper()) if country_code else None


def get_pricing_country(request) -> Optional[str]:
    """
    Get the country code for pricing based on request.
    Priority:
    1. ?test_country=XX query param (only if DEBUG=True)
    2. IP-based geolocation (via django-ipware)
    """
    if getattr(settings, 'DEBUG', False):
        test_country = request.GET.get('test_country')
        if test_country:
            return test_country.upper()

    client_ip, is_routable = get_client_ip(request)
    if not client_ip:
        return None

    return None


def resolve_plan_price(plan: Plan, interval: str, request) -> GeoPlanPrice:
    """
    Resolve the correct price for a user based on geo location.

    Resolution order:
    1. GeoPlanPrice country match (with interval)
    2. GeoPlanPrice region fallback (with interval)
    3. Legacy PlanPrice global price (with interval)

    Args:
        plan: The Plan to get pricing for
        interval: 'monthly' or 'yearly'
        request: HTTP request object for geo detection

    Returns:
        GeoPlanPrice or PlanPrice: The resolved price (guaranteed exactly one)

    Raises:
        PlanPrice.DoesNotExist: If no price found for plan/interval
    """
    country = get_pricing_country(request)
    region = get_region_for_country(country) if country else None

    # 1. Try GeoPlanPrice country-specific (with interval)
    if country:
        try:
            return GeoPlanPrice.objects.get(
                plan=plan,
                interval=interval,
                country=country.upper(),
                is_active=True
            )
        except GeoPlanPrice.DoesNotExist:
            pass

    # 2. Try GeoPlanPrice regional (with interval)
    if region:
        try:
            return GeoPlanPrice.objects.get(
                plan=plan,
                interval=interval,
                region=region,
                country__isnull=True,
                is_active=True
            )
        except GeoPlanPrice.DoesNotExist:
            pass

    # 3. Fallback to legacy global PlanPrice (with interval)
    try:
        return PlanPrice.objects.get(
            plan=plan,
            interval=interval,
            is_active=True
        )
    except PlanPrice.DoesNotExist:
        raise PlanPrice.DoesNotExist(
            f"No active price found for plan '{plan.name}' with interval '{interval}'"
        )


def create_gift_subscription(
    from_user: User,
    plan: Plan,
    duration_days: int = 30,
    message: str = "",
    request = None
) -> GiftSubscription:
    """Create a gift subscription."""
    gift = GiftSubscription.objects.create(
        plan=plan,
        from_user=from_user,
        message=message,
        gift_code=str(uuid.uuid4())[:16].upper(),
        duration_days=duration_days,
        expires_at=timezone.now() + timezone.timedelta(days=30),
    )

    SubscriptionHistory.objects.create(
        subscription=None,
        user=from_user,
        event_type=SubscriptionHistory.EventType.CREATED,
        metadata={
            "gift_id": str(gift.id),
            "gift_code": gift.gift_code,
            "plan_id": str(plan.id),
            "action": "gift_created"
        },
        notes=f"Created gift subscription for {plan.name}"
    )

    return gift


def claim_gift_subscription(
    gift_code: str,
    to_user: User,
    request = None
) -> Subscription:
    """Claim a gift subscription."""
    gift = GiftSubscription.objects.get(
        gift_code=gift_code.upper(),
        status=GiftSubscription.Status.PENDING
    )

    if gift.expires_at < timezone.now():
        gift.status = GiftSubscription.Status.EXPIRED
        gift.save()
        raise ValueError("Gift code has expired")

    if gift.to_user:
        raise ValueError("Gift already claimed")

    with transaction.atomic():
        expires_at = timezone.now() + timezone.timedelta(days=gift.duration_days)

        subscription = Subscription.objects.create(
            user=to_user,
            plan=gift.plan,
            plan_price=gift.plan_price,
            status=Subscription.Status.ACTIVE,
            is_active=True,
            started_at=timezone.now(),
            expires_at=expires_at,
            is_gift=True,
            gift_from=gift.from_user,
            gift_message=gift.message,
        )

        gift.to_user = to_user
        gift.status = GiftSubscription.Status.CLAIMED
        gift.claimed_at = timezone.now()
        gift.resulting_subscription = subscription
        gift.save()

        SubscriptionHistory.objects.create(
            subscription=subscription,
            user=to_user,
            event_type=SubscriptionHistory.EventType.GIFT_RECEIVED,
            new_plan_id=gift.plan.id,
            new_status=subscription.status,
            metadata={
                "gift_id": str(gift.id),
                "from_user_id": str(gift.from_user.id),
                "from_username": gift.from_user.username,
            },
            notes=f"Claimed gift from {gift.from_user.username}"
        )

        emit_event(
            event_type="SUBSCRIPTION_CREATED",
            subscription=subscription,
            user=to_user,
            metadata={"source": "gift", "gift_id": str(gift.id)}
        )

    return subscription


def grant_subscription_by_admin(
    user: User,
    plan: Plan,
    granted_by: User,
    duration_days: int = 30,
    reason: str = "",
    request = None
) -> Subscription:
    """Grant a subscription to a user (admin function)."""
    with transaction.atomic():
        expires_at = timezone.now() + timezone.timedelta(days=duration_days)

        subscription = Subscription.objects.create(
            user=user,
            plan=plan,
            status=Subscription.Status.ACTIVE,
            is_active=True,
            started_at=timezone.now(),
            expires_at=expires_at,
            is_admin_grant=True,
            granted_by=granted_by,
            grant_reason=reason,
        )

        SubscriptionHistory.objects.create(
            subscription=subscription,
            user=user,
            event_type=SubscriptionHistory.EventType.ADMIN_GRANTED,
            new_plan_id=plan.id,
            new_status=subscription.status,
            metadata={
                "granted_by_id": str(granted_by.id),
                "granted_by_username": granted_by.username,
                "reason": reason
            },
            notes=f"Admin grant by {granted_by.username}: {plan.name}"
        )

        emit_event(
            event_type="ADMIN_GRANTED_PLAN",
            subscription=subscription,
            user=user,
            metadata={
                "granted_by_id": str(granted_by.id),
                "granted_by_username": granted_by.username,
                "reason": reason
            }
        )

    return subscription


def emit_event(
    event_type: str,
    subscription: Subscription,
    user: User,
    metadata: Dict[str, Any] = None
):
    """Emit a subscription event."""
    import logging

    event_data = {
        "event_type": event_type,
        "subscription_id": str(subscription.id) if subscription else None,
        "user_id": str(user.id),
        "timestamp": timezone.now().isoformat(),
        "metadata": metadata or {}
    }

    logger = logging.getLogger(__name__)
    logger.info(f"SUBSCRIPTION_EVENT: {event_type} - {event_data}")


def start_trial(user: User, plan: Plan, days: int = 14, request = None) -> Subscription:
    """Start a trial subscription for a user."""
    with transaction.atomic():
        expires_at = timezone.now() + timezone.timedelta(days=days)

        subscription = Subscription.objects.create(
            user=user,
            plan=plan,
            status=Subscription.Status.ACTIVE,
            is_active=True,
            started_at=timezone.now(),
            expires_at=expires_at,
            payment_provider="trial",
        )

        SubscriptionHistory.objects.create(
            subscription=subscription,
            user=user,
            event_type=SubscriptionHistory.EventType.TRIAL_STARTED,
            new_plan_id=plan.id,
            new_status=subscription.status,
            metadata={"trial_days": days},
            notes=f"Started {days}-day trial"
        )

        emit_event(
            event_type="TRIAL_STARTED",
            subscription=subscription,
            user=user,
            metadata={"trial_days": days}
        )

    return subscription


def expire_trial(subscription: Subscription):
    """Mark a trial subscription as expired."""
    if subscription.payment_provider != "trial":
        raise ValueError("Only trial subscriptions can be expired this way")

    subscription.status = Subscription.Status.EXPIRED
    subscription.is_active = False
    subscription.save()

    SubscriptionHistory.objects.create(
        subscription=subscription,
        user=subscription.user,
        event_type=SubscriptionHistory.EventType.TRIAL_EXPIRED,
        previous_status=Subscription.Status.ACTIVE,
        new_status=Subscription.Status.EXPIRED,
        notes="Trial expired"
    )

    emit_event(
        event_type="TRIAL_EXPIRED",
        subscription=subscription,
        user=subscription.user,
        metadata={"trial_ended": True}
    )
