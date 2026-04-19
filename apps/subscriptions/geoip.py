"""
MaxMind GeoLite2 geolocation fallback module.

Provides singleton-based GeoIP lookup with Django caching.
"""
import ipaddress
import logging
from typing import Optional
from functools import lru_cache

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Singleton storage for MaxMind reader
_maxmind_reader = None


def _is_private_ip(ip: str) -> bool:
    """Check if IP is private/local."""
    try:
        ip_obj = ipaddress.ip_address(ip)
        return ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_reserved
    except ValueError:
        return True


def get_client_ip(request) -> Optional[str]:
    """
    Extract client IP from request.

    Priority:
    1. HTTP_X_FORWARDED_FOR (first IP if multiple)
    2. REMOTE_ADDR

    Returns None for private/invalid IPs.
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        # Take the first IP in the chain (closest to client)
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')

    if not ip or ip == '127.0.0.1' or _is_private_ip(ip):
        return None

    return ip


def _get_maxmind_reader():
    """Get or create singleton MaxMind reader instance."""
    global _maxmind_reader

    if not settings.MAXMIND_ENABLED:
        return None

    if _maxmind_reader is not None:
        return _maxmind_reader

    try:
        import geoip2.database

        db_path = settings.MAXMIND_DB_PATH
        if not db_path or not os.path.exists(db_path):
            logger.warning(f"MaxMind DB not found at: {db_path}")
            return None

        _maxmind_reader = geoip2.database.Reader(db_path)
        logger.info(f"MaxMind reader initialized: {db_path}")
        return _maxmind_reader

    except ImportError:
        logger.warning("geoip2 library not installed")
        return None
    except Exception as e:
        logger.error(f"Failed to initialize MaxMind reader: {e}")
        return None


def get_country_from_maxmind(request) -> Optional[str]:
    """
    Get country code from MaxMind GeoLite2 database.

    Uses Django cache (24h TTL) to avoid repeated lookups.
    Returns 2-letter country code or None on failure.
    """
    if not settings.MAXMIND_ENABLED:
        return None

    client_ip = get_client_ip(request)
    if not client_ip:
        return None

    # Check cache first
    cache_key = f"maxmind:country:{client_ip}"
    cached_country = cache.get(cache_key)
    if cached_country is not None:
        return cached_country if cached_country else None

    reader = _get_maxmind_reader()
    if not reader:
        return None

    try:
        response = reader.country(client_ip)
        country_code = response.country.iso_code

        # Cache result (even if None) for 24 hours
        cache_ttl = getattr(settings, 'MAXMIND_CACHE_TTL', 86400)
        cache.set(cache_key, country_code or "", cache_ttl)

        logger.debug(f"MaxMind lookup: {client_ip} -> {country_code}")
        return country_code

    except geoip2.errors.AddressNotFoundError:
        # Cache negative result to avoid repeated lookups
        cache_ttl = getattr(settings, 'MAXMIND_CACHE_TTL', 86400)
        cache.set(cache_key, "", cache_ttl)
        logger.debug(f"MaxMind: IP not found in database: {client_ip}")
        return None
    except Exception as e:
        logger.error(f"MaxMind lookup failed for {client_ip}: {e}")
        return None


def close_maxmind_reader():
    """Close the MaxMind reader (useful for testing/cleanup)."""
    global _maxmind_reader
    if _maxmind_reader:
        try:
            _maxmind_reader.close()
        except Exception:
            pass
        _maxmind_reader = None
