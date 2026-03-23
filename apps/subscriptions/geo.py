"""
Cloudflare-first Geo Detection Service for Django.

This module provides reliable country code detection using Cloudflare headers
with a simple fallback chain:
1. user.country (if set)
2. CF-IPCountry header (Cloudflare)
3. fallback ("US")

Usage:
    from apps.subscriptions.geo import get_user_country

    # In a view
    geo_info = get_user_country(request, request.user if request.user.is_authenticated else None)
    country = geo_info["country"]  # "US", "IN", etc.
    source = geo_info["source"]    # "user", "header", "fallback"
"""

import logging
import re
from typing import Optional, Dict

from django.conf import settings

logger = logging.getLogger(__name__)

# ISO 3166-1 alpha-2 valid country codes
VALID_COUNTRY_CODES = {
    "AD", "AE", "AF", "AG", "AI", "AL", "AM", "AO", "AQ", "AR", "AS", "AT", "AU", "AW", "AX", "AZ",
    "BA", "BB", "BD", "BE", "BF", "BG", "BH", "BI", "BJ", "BL", "BM", "BN", "BO", "BQ", "BR", "BS",
    "BT", "BV", "BW", "BY", "BZ", "CA", "CC", "CD", "CF", "CG", "CH", "CI", "CK", "CL", "CM", "CN",
    "CO", "CR", "CU", "CV", "CW", "CX", "CY", "CZ", "DE", "DJ", "DK", "DM", "DO", "DZ", "EC", "EE",
    "EG", "EH", "ER", "ES", "ET", "FI", "FJ", "FK", "FM", "FO", "FR", "GA", "GB", "GD", "GE", "GF",
    "GG", "GH", "GI", "GL", "GM", "GN", "GP", "GQ", "GR", "GS", "GT", "GU", "GW", "GY", "HK", "HM",
    "HN", "HR", "HT", "HU", "ID", "IE", "IL", "IM", "IN", "IO", "IQ", "IR", "IS", "IT", "JE", "JM",
    "JO", "JP", "KE", "KG", "KH", "KI", "KM", "KN", "KP", "KR", "KW", "KY", "KZ", "LA", "LB", "LC",
    "LI", "LK", "LR", "LS", "LT", "LU", "LV", "LY", "MA", "MC", "MD", "ME", "MF", "MG", "MH", "MK",
    "ML", "MM", "MN", "MO", "MP", "MQ", "MR", "MS", "MT", "MU", "MV", "MW", "MX", "MY", "MZ", "NA",
    "NC", "NE", "NF", "NG", "NI", "NL", "NO", "NP", "NR", "NU", "NZ", "OM", "PA", "PE", "PF", "PG",
    "PH", "PK", "PL", "PM", "PN", "PR", "PS", "PT", "PW", "PY", "QA", "RE", "RO", "RS", "RU", "RW",
    "SA", "SB", "SC", "SD", "SE", "SG", "SH", "SI", "SJ", "SK", "SL", "SM", "SN", "SO", "SR", "SS",
    "ST", "SV", "SX", "SY", "SZ", "TC", "TD", "TF", "TG", "TH", "TJ", "TK", "TL", "TM", "TN", "TO",
    "TR", "TT", "TV", "TW", "TZ", "UA", "UG", "UM", "US", "UY", "UZ", "VA", "VC", "VE", "VG", "VI",
    "VN", "VU", "WF", "WS", "XK", "YE", "YT", "ZA", "ZM", "ZW",
}

# Default fallback country
DEFAULT_COUNTRY = getattr(settings, "GEO_DEFAULT_COUNTRY", "US")


def _normalize_country_code(country: Optional[str]) -> Optional[str]:
    """
    Normalize country code to ISO 3166-1 alpha-2 format.

    Args:
        country: Raw country code string

    Returns:
        Normalized 2-letter uppercase country code or None if invalid
    """
    if not country:
        return None

    # Strip whitespace and convert to uppercase
    normalized = country.strip().upper()

    # Remove any non-alphabetic characters
    normalized = re.sub(r"[^A-Z]", "", normalized)

    # Must be exactly 2 letters
    if len(normalized) != 2:
        return None

    # Validate against known country codes
    if normalized not in VALID_COUNTRY_CODES:
        logger.warning(f"Unknown country code detected: {country} -> {normalized}")
        return None

    return normalized


def _get_country_from_user(user) -> Optional[str]:
    """
    Get country from user profile if available.

    Args:
        user: Django User instance

    Returns:
        Country code or None if not available
    """
    if not user or not getattr(user, "is_authenticated", False):
        return None

    country = getattr(user, "country", None)

    if country:
        normalized = _normalize_country_code(country)
        if normalized:
            return normalized

    return None


def _get_country_from_cf_header(request) -> Optional[str]:
    """
    Extract country from Cloudflare CF-IPCountry header.

    Args:
        request: Django request object

    Returns:
        Country code or None if not available/invalid
    """
    meta = getattr(request, "META", {})

    # Cloudflare header (HTTP_CF_IPCOUNTRY in Django's META)
    value = meta.get("HTTP_CF_IPCOUNTRY", "")

    if not value:
        return None

    # Handle XX (unknown) from Cloudflare
    if value.upper() == "XX":
        logger.debug("Cloudflare returned unknown country (XX)")
        return None

    return _normalize_country_code(value)


def get_user_country(request, user=None) -> Dict[str, str]:
    """
    Detect user country using Cloudflare-first strategy.

    Detection Priority:
        1. user.country (if authenticated and exists)
        2. CF-IPCountry header (Cloudflare)
        3. Default fallback ("US")

    Args:
        request: Django HTTP request object
        user: Optional User instance (if already available)

    Returns:
        Dict with keys:
            - "country": 2-letter ISO country code (e.g., "US", "IN")
            - "source": Detection source ("user", "header", "fallback")

    Example:
        >>> get_user_country(request, request.user)
        {"country": "IN", "source": "header"}

        >>> get_user_country(request)
        {"country": "US", "source": "fallback"}

    Notes:
        - Never raises exceptions - always returns valid result
        - Always returns uppercase 2-letter country code
        - Logs detection source at INFO level
    """
    country = None
    source = None

    try:
        # Stage 1: Check user profile (highest priority)
        if user is not None:
            country = _get_country_from_user(user)
            if country:
                source = "user"
                logger.info(f"Geo detected via user: {country}")

        # Stage 2: Check Cloudflare header
        if not country and request is not None:
            country = _get_country_from_cf_header(request)
            if country:
                source = "header"
                logger.info(f"Geo detected via header: {country}")

        # Stage 3: Default fallback
        if not country:
            country = DEFAULT_COUNTRY
            source = "fallback"
            logger.info(f"Geo using fallback: {country}")

        return {
            "country": country,
            "source": source
        }

    except Exception as e:
        # Ultimate safety net - never crash
        logger.error(f"Geo detection failed: {e}")
        return {
            "country": DEFAULT_COUNTRY,
            "source": "fallback"
        }


def get_country_from_request(request) -> str:
    """
    Convenience function to get just the country code from request.

    Args:
        request: Django HTTP request object

    Returns:
        2-letter country code string

    Example:
        >>> country = get_country_from_request(request)
        >>> print(country)
        "US"
    """
    result = get_user_country(request, getattr(request, "user", None))
    return result["country"]
