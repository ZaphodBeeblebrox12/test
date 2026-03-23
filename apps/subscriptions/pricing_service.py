"""
Geo-based pricing resolution service.

Provides price selection based on user's detected country/currency.
"""
from typing import Optional

from apps.subscriptions.geo import get_user_country
from apps.subscriptions.pricing_map import get_currency_for_country


def get_price_for_user(plan, request, user=None) -> Optional:
    """
    Resolve the appropriate PlanPrice for a user based on their geo-location.

    Resolution strategy:
        1. Exact currency match for user's country
        2. USD fallback (if no local currency price exists)
        3. First available price (final fallback)

    Args:
        plan: Plan instance to get pricing for
        request: Django HTTP request object (for geo detection)
        user: Optional User instance (for user profile country override)

    Returns:
        PlanPrice instance or None if no prices available

    Example:
        >>> # In a view
        >>> plans = Plan.objects.filter(is_active=True)
        >>> for plan in plans:
        ...     plan.resolved_price = get_price_for_user(plan, request, request.user)
        >>> 
        >>> # Template usage
        >>> {{ plan.resolved_price.formatted_price }}
    """
    # Get geo info
    geo = get_user_country(request, user)
    country = geo["country"]

    # Get target currency for this country
    currency = get_currency_for_country(country)

    # Strategy 1: Exact currency match
    price = plan.prices.filter(currency=currency, is_active=True).first()

    # Strategy 2: USD fallback
    if not price and currency != "USD":
        price = plan.prices.filter(currency="USD", is_active=True).first()

    # Strategy 3: Final fallback - any active price
    if not price:
        price = plan.prices.filter(is_active=True).first()

    return price


def get_price_for_country(plan, country_code: str) -> Optional:
    """
    Resolve price for a specific country code (useful for async/celery tasks).

    Args:
        plan: Plan instance
        country_code: ISO 3166-1 alpha-2 country code

    Returns:
        PlanPrice instance or None
    """
    currency = get_currency_for_country(country_code)

    # Strategy 1: Exact currency match
    price = plan.prices.filter(currency=currency, is_active=True).first()

    # Strategy 2: USD fallback
    if not price and currency != "USD":
        price = plan.prices.filter(currency="USD", is_active=True).first()

    # Strategy 3: Any active price
    if not price:
        price = plan.prices.filter(is_active=True).first()

    return price
