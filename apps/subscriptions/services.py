"""
Geo Pricing Service for location-aware plan pricing.
"""
import logging
from typing import Optional, Dict, Any

from django.core.cache import cache

from .models import Plan, PlanPrice, GeoPlanPrice

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
    """
    Determine country for pricing.

    Priority:
    1. test_country query param (if user is staff, or in DEBUG mode)
    2. CloudFlare IP country header
    3. None (uses global pricing)
    """
    from django.conf import settings

    # Check for test country in query params
    # Allow if: user is staff/superuser OR we're in DEBUG mode
    is_staff_or_debug = False

    if hasattr(request, 'user') and hasattr(request.user, 'is_staff'):
        if request.user.is_staff or request.user.is_superuser:
            is_staff_or_debug = True

    # FIXED: Also allow test_country in DEBUG mode for easier testing
    if settings.DEBUG:
        is_staff_or_debug = True

    if is_staff_or_debug and hasattr(request, 'GET'):
        test_country = request.GET.get("test_country")
        if test_country:
            logger.info(f"Using test_country: {test_country.upper()}")
            return test_country.upper()

    # Check for CloudFlare IP country header
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
    """
    Resolve price for plan based on country/region.

    Priority: Country -> Region -> Global (PlanPrice)
    """
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

        # Try country-specific price
        try:
            country_price = GeoPlanPrice.objects.get(
                plan=plan,
                country=country_code,
                is_active=True
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

        # Try regional price
        if region:
            try:
                region_price = GeoPlanPrice.objects.get(
                    plan=plan,
                    region=region,
                    country__isnull=True,
                    is_active=True
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

    # FALLBACK: Try to get standard price from PlanPrice
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

    # Raise clear error - Plan model doesn't have base_price_cents
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


# Placeholder service functions
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
        user=user, plan=plan, status=Subscription.Status.TRIAL,
    )
