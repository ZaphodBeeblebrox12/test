"""
Production settings — used on Render (and any real deployment).

Overrides base with security hardening, env-driven hosts, Postgres via
DATABASE_URL (handled in base), and whitenoise manifest storage.
"""
from .base import *

DEBUG = env("DEBUG", default=False)

# Hosts: comma-separated env (Render injects its own URL too).
ALLOWED_HOSTS = env(
    "ALLOWED_HOSTS",
    default=".onrender.com",
)

# CSRF: env list + the app URL. base.py only allows all-origins when DEBUG.
CSRF_TRUSTED_ORIGINS = env(
    "CSRF_TRUSTED_ORIGINS",
    default="https://*.onrender.com",
)

# --- Security hardening ---------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = env("SECURE_SSL_REDIRECT", default=True)
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# --- Static files: whitenoise compressed+hashed ---------------------------
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

# Keep DB connections warm across requests (gunicorn workers).
CONN_MAX_AGE = 60

# Public URL of this deployment — Stripe/Razorpay success/cancel redirects
# and Telegram/email links all build from SITE_BASE_URL (base.py).
# Set SITE_BASE_URL in Render env vars to your https://<app>.onrender.com.

# NOTE: ACCOUNT_EMAIL_VERIFICATION is "mandatory" (base.py). Without SMTP
# credentials users cannot complete signup. Configure EMAIL_* env vars on
# Render (e.g. Gmail app password, SendGrid, Mailgun) BEFORE opening
# registration, or override ACCOUNT_EMAIL_VERIFICATION here for launch.
