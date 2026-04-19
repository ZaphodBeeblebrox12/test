"""
MaxMind GeoLite2 geolocation module with automatic updates (Celery-driven).
"""
import ipaddress
import logging
import os
import shutil
import tempfile
from pathlib import Path
from threading import Lock
from typing import Optional

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Singleton storage for MaxMind reader
_maxmind_reader = None
_reader_lock = Lock()

# GeoIP2 import deferred until needed
geoip2 = None


def _import_geoip2():
    global geoip2
    if geoip2 is None:
        try:
            import geoip2.database
            geoip2 = geoip2
        except ImportError:
            logger.error("geoip2 library not installed. Run: pip install geoip2")
            raise


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
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')

    if not ip or ip == '127.0.0.1' or _is_private_ip(ip):
        return None
    return ip


def download_geolite2_database(license_key: str, edition_id: str = "GeoLite2-Country") -> Optional[Path]:
    """
    Download the latest GeoLite2 database from MaxMind.
    Returns path to downloaded file or None on failure.
    """
    url = "https://download.maxmind.com/app/geoip_download"
    params = {
        "edition_id": edition_id,
        "license_key": license_key,
        "suffix": "tar.gz",
    }
    try:
        logger.info(f"Downloading {edition_id} database...")
        response = requests.get(url, params=params, stream=True, timeout=60)
        response.raise_for_status()

        # Save to temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz") as tmp:
            for chunk in response.iter_content(chunk_size=8192):
                tmp.write(chunk)
            tmp_path = Path(tmp.name)

        # Extract the .mmdb file from the tar.gz
        import tarfile
        extract_dir = tempfile.mkdtemp()
        with tarfile.open(tmp_path, "r:gz") as tar:
            mmdb_member = None
            for member in tar.getmembers():
                if member.name.endswith(".mmdb"):
                    mmdb_member = member
                    break
            if not mmdb_member:
                logger.error("No .mmdb file found in archive")
                return None
            tar.extract(mmdb_member, path=extract_dir)

        extracted_mmdb = Path(extract_dir) / mmdb_member.name
        return extracted_mmdb

    except Exception as e:
        logger.exception(f"Failed to download GeoLite2 database: {e}")
        return None


def update_database_file(source_path: Path, target_path: Path) -> bool:
    """
    Atomically replace the target database file with the new one.
    """
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_target = target_path.with_suffix(".tmp")
        shutil.copy2(source_path, temp_target)
        os.replace(temp_target, target_path)
        logger.info(f"Database updated at {target_path}")
        return True
    except Exception as e:
        logger.exception(f"Failed to update database file: {e}")
        return False


def check_and_update_database(force: bool = False) -> bool:
    """
    Check if the database needs updating and perform update if necessary.
    Returns True if database is ready (either already fresh or updated successfully).
    """
    if not settings.MAXMIND_ENABLED:
        return False

    license_key = getattr(settings, 'MAXMIND_LICENSE_KEY', '')
    if not license_key:
        logger.warning("MAXMIND_LICENSE_KEY not set; cannot auto-update database.")
        return os.path.exists(settings.MAXMIND_DB_PATH)

    db_path = Path(settings.MAXMIND_DB_PATH)
    update_interval = getattr(settings, 'MAXMIND_UPDATE_INTERVAL_DAYS', 7)

    # Check if file exists and is recent enough
    if not force and db_path.exists():
        import time
        file_age = time.time() - db_path.stat().st_mtime
        if file_age < update_interval * 86400:
            logger.debug(f"Database is less than {update_interval} days old, skipping update.")
            return True

    logger.info("Updating GeoLite2 database...")
    new_db = download_geolite2_database(license_key)
    if new_db:
        success = update_database_file(new_db, db_path)
        # Clean up temporary files
        try:
            new_db.unlink()
            shutil.rmtree(new_db.parent, ignore_errors=True)
        except Exception:
            pass
        if success:
            # Invalidate reader so next lookup reloads
            global _maxmind_reader
            with _reader_lock:
                if _maxmind_reader:
                    try:
                        _maxmind_reader.close()
                    except Exception:
                        pass
                    _maxmind_reader = None
            return True
    return False


def _get_maxmind_reader():
    """Get or create singleton MaxMind reader instance."""
    global _maxmind_reader

    if not settings.MAXMIND_ENABLED:
        return None

    # Ensure database exists and is up to date (on-demand check)
    check_and_update_database(force=False)

    with _reader_lock:
        if _maxmind_reader is not None:
            return _maxmind_reader

        _import_geoip2()
        db_path = settings.MAXMIND_DB_PATH
        if not db_path or not os.path.exists(db_path):
            logger.warning(f"MaxMind DB not found at: {db_path}")
            return None

        try:
            _maxmind_reader = geoip2.database.Reader(db_path)
            logger.info(f"MaxMind reader initialized: {db_path}")
            return _maxmind_reader
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

        cache_ttl = getattr(settings, 'MAXMIND_CACHE_TTL', 86400)
        cache.set(cache_key, country_code or "", cache_ttl)

        logger.debug(f"MaxMind lookup: {client_ip} -> {country_code}")
        return country_code

    except geoip2.errors.AddressNotFoundError:
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
    with _reader_lock:
        if _maxmind_reader:
            try:
                _maxmind_reader.close()
            except Exception:
                pass
            _maxmind_reader = None