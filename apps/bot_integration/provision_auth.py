"""
HMAC request authentication shared by the registration/heartbeat endpoints.

Mirrors the bot-side scheme (provision_api.py / provision_registration.py):

    signature = hex(HMAC_SHA256(secret, f"{timestamp}.{nonce}.{raw_body}"))
    headers: X-Bot-Instance, X-Bot-Timestamp, X-Bot-Nonce, X-Bot-Signature
"""

from __future__ import annotations

import hashlib
import hmac
import time

from django.conf import settings
from django.core.cache import cache

MAX_SKEW_SECONDS = 300
NONCE_TTL_SECONDS = 600

NONCE_CACHE_PREFIX = "provision-nonce:"


def sign_payload(secret: str, timestamp: str, nonce: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), digestmod=hashlib.sha256)
    mac.update(f"{timestamp}.{nonce}.".encode())
    mac.update(body)
    return mac.hexdigest()


def verify_signed_request(request):
    """Returns (payload_bytes, error_message).  error_message is None on success."""
    secret = getattr(settings, "PROVISION_SHARED_SECRET", "") or ""
    if not secret:
        return None, "PROVISION_SHARED_SECRET is not configured"

    ts = request.headers.get("X-Bot-Timestamp", "")
    nonce = request.headers.get("X-Bot-Nonce", "")
    sig = request.headers.get("X-Bot-Signature", "")
    if not (ts and nonce and sig):
        return None, "missing signature headers"

    try:
        if abs(time.time() - float(ts)) > MAX_SKEW_SECONDS:
            return None, "timestamp outside allowed skew"
    except ValueError:
        return None, "invalid timestamp"

    # Replay protection through the shared cache (Redis in production).
    if not cache.add(f"{NONCE_CACHE_PREFIX}{nonce}", 1, NONCE_TTL_SECONDS):
        return None, "nonce replay detected"

    body = request.body
    expected = sign_payload(secret, ts, nonce, body)
    if not hmac.compare_digest(expected, sig):
        return None, "signature mismatch"
    return body, None
