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
    return COUNTRY_TO_REGION.get(country_code.upper()) if country_code else None

def get_request_country(request) -> str:
    import logging
    logger = logging.getLogger(__name__)
    cf_country = request.META.get("HTTP_CF_IPCOUNTRY", "").upper()
    if cf_country and cf_country != "XX" and len(cf_country) == 2:
        return cf_country
    client_ip, is_routable = get_client_ip(request)
    if client_ip:
        if is_routable:
            logger.debug(f"IP detected ({client_ip}) but no geo mapping available")
        else:
            logger.debug(f"Private/non-routable IP detected ({client_ip})")
    default_country = getattr(settings, "DEFAULT_COUNTRY", "US")
    return default_country.upper()

def get_pricing_country(request) -> Optional[str]:
    if getattr(settings, "DEBUG", False):
        test_country = request.GET.get("test_country")
        if test_country:
            return test_country.upper()
    return get_request_country(request)

def resolve_plan_price(plan: Plan, interval: str, request):
    country = get_pricing_country(request)
    region = get_region_for_country(country) if country else None
    if country:
        try:
            return GeoPlanPrice.objects.get(
                plan=plan, interval=interval, country=country.upper(), is_active=True
            )
        except GeoPlanPrice.DoesNotExist:
            pass
    if region:
        try:
            return GeoPlanPrice.objects.get(
                plan=plan, interval=interval, region=region, country__isnull=True, is_active=True
            )
        except GeoPlanPrice.DoesNotExist:
            pass
    try:
        return PlanPrice.objects.get(plan=plan, interval=interval, is_active=True)
    except PlanPrice.DoesNotExist:
        raise PlanPrice.DoesNotExist(f"No active price for plan '{plan.name}' interval '{interval}'")

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
    # NOTE: SubscriptionHistory NOT created here - no Subscription exists yet.
    # History is created when gift is claimed and real Subscription is created.
    return gift

def claim_gift_subscription(
    gift_code: str,
    to_user: User,
    request = None
) -> Subscription:
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
