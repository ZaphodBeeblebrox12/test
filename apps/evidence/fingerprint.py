"""Deterministic, privacy-conscious device fingerprinting.

WHAT THIS IS
    A reproducible, versioned hash over signals a normal web application can
    legitimately observe (user agent, languages, timezone, screen, platform,
    hardware concurrency, touch points). NOT a covert tracker: no canvas,
    no audio, no WebGL, no MAC address, no hardware serials, no persistent
    cookies supercookie-style storage.

TWO SEPARATE IDENTIFIERS (by design)
    device_fingerprint  - evidence-oriented: sha256 of canonicalized signals
                          (64 hex chars >= 20 chars, derived from >=2 device
                          properties). Stored WITH the canonical signals so the
                          derivation is reproducible and reviewable. Suitable
                          as the device identifier element for Visa CE3.0-style
                          matching (per current rule interpretations).
    risk_device_id      - internal continuity: HMAC(server salt, fingerprint).
                          Rotating the salt severs continuity; it cannot be
                          correlated with anything outside this system.

LIMITATIONS (documented honestly)
    * Shared devices/browsers, corporate images, anti-fingerprinting modes,
      and mobile WebViews reduce stability - treat as pattern evidence, never
      as identity proof on its own.
    * This is OUR deterministic method, not a network-mandated standard; the
      network requirement is a >=20-char identifier from >=2 device signals.
    * Signals can change (browser updates, displays); continuity matching
      should tolerate single-signal drift (compare majority of components).
"""
from __future__ import annotations

import hashlib
import hmac
import json

from django.conf import settings

FINGERPRINT_VERSION = "1"

# Non-sensitive, legitimately-observable browser signals only.
SIGNAL_KEYS = (
    "user_agent", "language", "languages", "timezone", "screen",
    "color_depth", "pixel_ratio", "platform", "hardware_concurrency",
    "max_touch_points",
)


def canonicalize(signals: dict) -> dict:
    """Normalize posted signals into a stable canonical dict."""
    out = {}
    for key in SIGNAL_KEYS:
        value = signals.get(key)
        if value is None:
            value = ""
        if isinstance(value, (list, tuple)):
            value = ",".join(str(v).strip().lower() for v in value)
        out[key] = str(value).strip()[:300]
    return out


def compute_fingerprint(signals: dict) -> tuple[str, dict]:
    """Returns (fingerprint_hex, canonical_signals)."""
    canonical = canonicalize(signals)
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(
        f"{FINGERPRINT_VERSION}|{payload}".encode("utf-8")).hexdigest()
    return digest, canonical


def compute_risk_device_id(fingerprint_hex: str) -> str:
    """Salted HMAC - internal-only continuity identifier."""
    salt = getattr(settings, "RISK_DEVICE_SALT", None) or settings.SECRET_KEY
    return hmac.new(str(salt).encode(), fingerprint_hex.encode(),
                    hashlib.sha256).hexdigest()


def collect_from_request(request) -> dict:
    """Server-side signals (used when the client payload is absent)."""
    meta = request.META
    return {
        "user_agent": meta.get("HTTP_USER_AGENT", ""),
        "language": meta.get("HTTP_ACCEPT_LANGUAGE", "").split(",")[0],
        "languages": meta.get("HTTP_ACCEPT_LANGUAGE", ""),
        "timezone": "",
        "screen": "",
        "color_depth": "",
        "pixel_ratio": "",
        "platform": "",
        "hardware_concurrency": "",
        "max_touch_points": "",
    }
