"""
Test settings for the Provision Contract v1 integration suite.

Runs `apps.bot_integration` tests without the full production dependency
stack (allauth, dj-stripe, ...).  NOT for production use.

    DJANGO_SETTINGS_MODULE=config.settings.test_provision \
        python manage.py test apps.bot_integration
"""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEBUG = True
SECRET_KEY = "provision-test-only"
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.sites",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "djstripe",
    "apps.accounts",
    "apps.subscriptions",
    "apps.bot_integration",
    "apps.jobs",
]

AUTH_USER_MODEL = "accounts.User"

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "/static/"
SITE_ID = 1
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [os.path.join(BASE_DIR, "templates")],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]

# Provision Contract v1 (values mirrored by the bot-side test suite)
PROVISION_SHARED_SECRET = "test-secret"
PROVISION_BOT_URL = ""
PROVISION_FRESHNESS_SECONDS = 90
PROVISION_CONTROL_CHANNEL_ID = "-1009999999999"
PROVISION_TIMEOUT = 10

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

ROOT_URLCONF = "config.urls_test"

# dj-stripe requires explicit FK target declaration
DJSTRIPE_FOREIGN_KEY_TO_FIELD = "id"
STRIPE_LIVE_MODE = False
STRIPE_TEST_SECRET_KEY = "sk_test_provision"
STRIPE_TEST_PUBLISHABLE_KEY = "pk_test_provision"
STRIPE_SECRET_KEY = STRIPE_TEST_SECRET_KEY
STRIPE_PUBLISHABLE_KEY = STRIPE_TEST_PUBLISHABLE_KEY
