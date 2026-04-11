"""
Geo Pricing Service for location-aware plan pricing with Trial Support.
"""
import logging
from typing import Optional, Dict, Any
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import PermissionDenied

from .models import Plan, PlanPrice, GeoPlanPrice, Subscription, UserTrialUsage, SubscriptionHistory

logger = logging.getLogger(__name__)

COUNTRY_REGIONS = {
    "IN": "SEA", "PK": "SEA", "BD": "SEA", "LK": "SEA", "NP": "SEA",
    "SG": "SEA", "MY": "SEA", "ID": "SEA", "TH": "SEA", "VN": "SEA", "PH": "SEA",
    "DE": "EU", "FR": "EU", "IT": "EU", "ES": "EU", "NL": "EU", "BE": "EU", "AT": "EU",
    "BR": "LATAM", "MX": "LATAM", "AR": "LATAM", "CL": "LATAM", "CO": "LATAM", "PE": "LATAM",
    "ZA": "AFRICA", "NG": "AFRICA", "KE": "AFRICA", "EG": "AFRICA",
}

CURRENCY_SYMBOLS = {
    "USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "BRL": "R$",
    "MXN": "$", "SGD": "S$", "IDR": "Rp", "MYR": "RM", "THB": "฿", "PHP": "₱", "VND": "₫",
}


def get_pricing_country(request) -> Optional[str]:
    """Determine country for pricing."""
    from django.conf import settings

    is_staff_or_debug = False
    if hasattr(request, 'user') and hasattr(request.user, 'is_staff'):
        if request.user.is_staff or request.user.is_superuser:
            is_staff_or_debug = True
    if settings.DEBUG:
        is_staff_or_debug = True

    if is_staff_or_debug and hasattr(request, 'GET'):
        test_country = request.GET.get("test_country")
        if test_country:
            return test_country.upper()

    cf_country = request.META.get("HTTP_CF_IPCOUNTRY") if hasattr(request, 'META') else None
    if cf_country and cf_country != "XX":
        return cf_country.upper()
    return None


def get_region_for_country(country_code: str) -> Optional[str]:
    """Get region for country code."""
    return COUNTRY_REGIONS.get(country_code)


def format_price(price_cents: int, currency: str) -> str:
    """Format price with currency symbol."""
    symbol = CURRENCY_SYMBOLS.get(currency, currency)
    if currency in ["IDR", "VND"]:
        return f"{symbol}{price_cents / 100 / 1000:.1f}K"
    return f"{symbol}{price_cents / 100:.0f}"


def resolve_plan_price(plan: Plan, country_code: Optional[str] = None) -> Dict[str, Any]:
    """Resolve price for plan based on country/region."""
    is_geo = False
    breakdown = {
        "requested_country": country_code,
        "region": None,
        "matched_tier": "global",
    }

    if country_code:
        country_code = country_code.upper()
        region = get_region_for_country(country_code)
        breakdown["region"] = region

        try:
            country_price = GeoPlanPrice.objects.get(
                plan=plan, country=country_code, is_active=True
            )
            breakdown["matched_tier"] = "country"
            is_geo = True
            return {
                "price_cents": country_price.price_cents,
                "currency": country_price.currency,
                "display": format_price(country_price.price_cents, country_price.currency),
                "geo_pricing": True,
                "breakdown": breakdown,
            }
        except GeoPlanPrice.DoesNotExist:
            pass

        if region:
            try:
                region_price = GeoPlanPrice.objects.get(
                    plan=plan, region=region, country__isnull=True, is_active=True
                )
                breakdown["matched_tier"] = "region"
                is_geo = True
                return {
                    "price_cents": region_price.price_cents,
                    "currency": region_price.currency,
                    "display": format_price(region_price.price_cents, region_price.currency),
                    "geo_pricing": True,
                    "breakdown": breakdown,
                }
            except GeoPlanPrice.DoesNotExist:
                pass

    try:
        standard_price = PlanPrice.objects.get(plan=plan, is_active=True)
        breakdown["matched_tier"] = "global"
        return {
            "price_cents": standard_price.price_cents,
            "currency": standard_price.currency,
            "display": format_price(standard_price.price_cents, standard_price.currency),
            "geo_pricing": is_geo,
            "breakdown": breakdown,
        }
    except PlanPrice.DoesNotExist:
        pass

    raise PlanPrice.DoesNotExist(
        f"No active PlanPrice or GeoPlanPrice found for plan {plan.id} ({plan.name})"
    )


def get_cached_plan_price(plan: Plan, country_code: Optional[str] = None) -> Dict[str, Any]:
    """Get plan price with caching."""
    cache_key = f"plan_price:{plan.id}:{country_code or 'global'}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    result = resolve_plan_price(plan, country_code)
    cache.set(cache_key, result, 3600)
    return result


def has_user_used_trial(user, plan: Plan) -> bool:
    """Check if user has already used a specific trial plan."""
    if not plan.is_trial:
        return False
    return UserTrialUsage.objects.filter(user=user, plan=plan).exists()


@transaction.atomic
def purchase_plan(user, plan: Plan, request=None) -> Subscription:
    """
    Purchase a plan (regular or trial) with full geo pricing support.

    This is the MAIN entry point for plan purchases.
    """
    country_code = get_pricing_country(request) if request else None

    if plan.is_trial:
        if has_user_used_trial(user, plan):
            raise PermissionDenied(
                f"You have already used the {plan.name} trial. "
                "Each trial plan can only be claimed once."
            )

        duration_days = plan.trial_duration_days
        expires_at = timezone.now() + timedelta(days=duration_days)

        subscription = Subscription.objects.create(
            user=user,
            plan=plan,
            plan_price=None,
            status=Subscription.Status.ACTIVE,
            is_active=True,
            started_at=timezone.now(),
            expires_at=expires_at,
            payment_provider="trial",
            provider_subscription_id="",
            pricing_country=country_code,
            pricing_region=get_region_for_country(country_code) if country_code else None,
            is_trial=True,
        )

        UserTrialUsage.objects.create(
            user=user,
            plan=plan,
            subscription=subscription,
            expires_at=expires_at,
        )

        SubscriptionHistory.objects.create(
            subscription=subscription,
            user=user,
            event_type=SubscriptionHistory.EventType.TRIAL_STARTED,
            new_plan_id=plan.id,
            new_status=subscription.status,
            metadata={
                "trial_duration_days": duration_days,
                "expires_at": expires_at.isoformat(),
                "pricing_country": country_code,
            },
            notes=f"Trial started: {plan.name} for {duration_days} days",
        )

        logger.info(f"Trial created: {user.username} - {plan.name} ({duration_days} days)")
        return subscription

    else:
        price_info = resolve_plan_price(plan, country_code)
        interval = price_info.get("interval", "monthly")
        duration_days = 365 if interval == "yearly" else 30
        expires_at = timezone.now() + timedelta(days=duration_days)

        subscription = Subscription.objects.create(
            user=user,
            plan=plan,
            plan_price=None,
            status=Subscription.Status.ACTIVE,
            is_active=True,
            started_at=timezone.now(),
            expires_at=expires_at,
            payment_provider="stripe",
            provider_subscription_id="",
            pricing_country=country_code,
            pricing_region=price_info.get("breakdown", {}).get("region"),
            is_trial=False,
        )

        SubscriptionHistory.objects.create(
            subscription=subscription,
            user=user,
            event_type=SubscriptionHistory.EventType.CREATED,
            new_plan_id=plan.id,
            new_status=subscription.status,
            metadata={
                "price_cents": price_info["price_cents"],
                "currency": price_info["currency"],
                "geo_pricing": price_info["geo_pricing"],
                "pricing_country": country_code,
            },
            notes=f"Subscription created: {plan.name}",
        )

        logger.info(f"Subscription created: {user.username} - {plan.name}")
        return subscription


def create_gift_subscription(from_user, plan, duration_days, message="", request=None):
    from apps.subscriptions.models import GiftSubscription
    return GiftSubscription.objects.create(
        from_user=from_user, plan=plan, duration_days=duration_days, message=message,
    )


def claim_gift_subscription(gift, user, request=None):
    gift.to_user = user
    gift.status = "claimed"
    gift.save()
    return gift


def grant_subscription_by_admin(user, plan, duration_days, admin_user, reason=""):
    from apps.subscriptions.models import Subscription
    return Subscription.objects.create(
        user=user, plan=plan, status=Subscription.Status.ACTIVE,
    )


def start_trial(user, plan, duration_days=7):
    from apps.subscriptions.models import Subscription
    return Subscription.objects.create(
        user=user, plan=plan, status=Subscription.Status.ACTIVE, is_trial=True,
    )
