"""
Base settings for community platform.
"""
import os
from pathlib import Path

import environ

# Build paths inside the project
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Environment variables
env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
)

# Read .env file if it exists
environ.Env.read_env(os.path.join(BASE_DIR, ".env"))

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env("SECRET_KEY", default="change-me-in-production")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env("DEBUG")

ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# Application definition
DJANGO_APPS = [
    "apps.admin_config.DashboardAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "django.contrib.humanize",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
]

LOCAL_APPS = [
    "apps.accounts",
    "apps.api",
    "apps.audit",
    "apps.core",
    "apps.notifications",
    "apps.system_settings",
    "apps.subscriptions",
    "apps.payments",
    "apps.growth",
    "apps.bot_integration",
    "apps.jobs",
    "apps.events",
    "apps.emailing",
    "apps.campaigns",
    "apps.automation",
    "apps.promotions",
    "apps.analytics",
    "apps.advanced_growth",
    "apps.public_views",
    "apps.support",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "apps.bot_integration.middleware.DisableCSRFForWebhook",  # FIXED: correct class name
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.accounts.middleware.BanEnforcementMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "apps.accounts.email_verification_middleware.EmailVerificationMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": env.db_url(
        "DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}

# ─── Background jobs: Django-native durable job system (apps.jobs) ──────────
# No Celery/Redis.  Run the worker alongside the app:
#     python manage.py runjobs --loop
# The old Redis-backed Celery settings were removed; REDIS_URL is now unused.

TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", default="")
TELEGRAM_BOT_USERNAME = env("TELEGRAM_BOT_USERNAME", default="")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

# CSRF trusted origins — allow all origins when DEBUG=True (for ngrok testing)
if DEBUG:
    CSRF_TRUSTED_ORIGINS = ["http://*", "https://*"]
else:
    CSRF_TRUSTED_ORIGINS = [
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "https://dbb7-223-190-85-112.ngrok-free.app",
    ]

# ============================================================
# DJANGO-ALLAUTH CONFIGURATION
# ============================================================

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_AUTHENTICATION_METHOD = "email"
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_USER_MODEL_USERNAME_FIELD = "username"
ACCOUNT_EMAIL_CONFIRMATION_EXPIRE_DAYS = 3
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
ACCOUNT_LOGIN_ON_PASSWORD_RESET = True
ACCOUNT_SESSION_REMEMBER = True
ACCOUNT_SIGNUP_PASSWORD_ENTER_TWICE = True

SOCIALACCOUNT_EMAIL_VERIFICATION = "none"
SOCIALACCOUNT_EMAIL_REQUIRED = True
SOCIALACCOUNT_AUTO_SIGNUP = True

SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "SCOPE": [
            "profile",
            "email",
        ],
        "AUTH_PARAMS": {
            "access_type": "online",
        },
        "OAUTH_PKCE_ENABLED": True,
    }
}

EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend"
)

EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env("EMAIL_PORT", default=587)
EMAIL_USE_TLS = env("EMAIL_USE_TLS", default=True)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="noreply@example.com")

SITE_ID = 1

# ============================================================
# MAXMIND GEOLITE2 CONFIGURATION
# ============================================================

# Path to MaxMind GeoLite2 Country database file
MAXMIND_DB_PATH = env("MAXMIND_DB_PATH", default=os.path.join(BASE_DIR, "data", "GeoLite2-Country.mmdb"))

# Enable/disable MaxMind fallback
MAXMIND_ENABLED = env("MAXMIND_ENABLED", default=False)

# Cache TTL for MaxMind lookups (seconds) - 24 hours default
MAXMIND_CACHE_TTL = env("MAXMIND_CACHE_TTL", default=86400)

# License key for automatic database downloads (free from maxmind.com)
MAXMIND_LICENSE_KEY = env("MAXMIND_LICENSE_KEY", default="")

# Optional: Update interval in days (default 7)
MAXMIND_UPDATE_INTERVAL_DAYS = env("MAXMIND_UPDATE_INTERVAL_DAYS", default=7)


# ============================================================
# LOGIN REDIRECTS
# ============================================================

LOGIN_REDIRECT_URL = "/dashboard/"
ACCOUNT_LOGOUT_REDIRECT_URL = "/"

# ─── Provision Contract v1 (Telegram bot bridge integration) ───────────────
# Shared HMAC secret — MUST equal the bot's PROVISION_SHARED_SECRET. Env only.
PROVISION_SHARED_SECRET = os.environ.get("PROVISION_SHARED_SECRET", "")
# Bootstrap fallback ONLY, before the bot has registered itself.
PROVISION_BOT_URL = os.environ.get("PROVISION_BOT_URL", "")
PROVISION_FRESHNESS_SECONDS = int(os.environ.get("PROVISION_FRESHNESS_SECONDS", "90"))
# Admin/control channel — NEVER a valid subscriber provisioning target.
PROVISION_CONTROL_CHANNEL_ID = os.environ.get(
    "TELEGRAM_CONTROL_CHANNEL_ID",
    os.environ.get("PROVISION_CONTROL_CHANNEL_ID", ""),
)
PROVISION_TIMEOUT = float(os.environ.get("PROVISION_TIMEOUT", "10"))

# Periodic access sweep is enqueued by the jobs worker (apps.jobs);
# see apps/jobs/handlers.py::schedule_periodic / the "sweep" job kind.


# --- Payment providers (P1 hosted checkout) ---------------------------------
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
# Base URL used to build provider success/cancel redirects.
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "http://127.0.0.1:8000")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
