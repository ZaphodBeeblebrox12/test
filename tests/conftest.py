"""
Pytest configuration for community platform.
"""
import uuid

import pytest
from django.contrib.auth import get_user_model

from apps.accounts.models import Profile
from apps.audit.models import AuditLog

User = get_user_model()


@pytest.fixture
def user_factory():
    """Factory for creating test users."""
    def factory(**kwargs):
        defaults = {
            # username must be unique per user; generate one unless overridden.
            "username": f"user_{uuid.uuid4().hex[:12]}",
            "telegram_id": 123456789,
            "telegram_username": "testuser",
            "first_name": "Test",
            "last_name": "User",
        }
        defaults.update(kwargs)
        user = User.objects.create(**defaults)
        Profile.objects.create(user=user)
        return user
    return factory


@pytest.fixture
def admin_user(user_factory):
    """Create an admin user."""
    return user_factory(
        telegram_id=999999999,
        telegram_username="admin",
        role=User.Role.ADMIN,
        is_staff=True,
        is_superuser=True,
        is_staff_approved=True,
    )


@pytest.fixture
def staff_user(user_factory):
    """Create a staff user pending approval."""
    return user_factory(
        telegram_id=888888888,
        telegram_username="staff",
        role=User.Role.STAFF,
        is_staff=False,
        is_staff_approved=False,
    )


@pytest.fixture
def banned_user(user_factory, db):
    """Create a banned user."""
    user = user_factory(
        telegram_id=777777777,
        telegram_username="banned",
    )
    user.ban("Test ban reason")
    return user


@pytest.fixture
def telegram_auth_data():
    """Sample Telegram auth data for testing."""
    return {
        "id": 123456789,
        "auth_date": 1704067200,
        "first_name": "Test",
        "last_name": "User",
        "username": "testuser",
        "photo_url": "https://t.me/i/userpic/320/test.jpg",
    }


@pytest.fixture
def google_social_app(db):
    """Provide a Google OAuth SocialApp for tests. Uses dummy test-only
    credentials (never real secrets); satisfies allauth's SocialApp.DoesNotExist
    so the login URL can be exercised without external OAuth config."""
    from allauth.socialaccount.models import SocialApp
    from django.contrib.sites.models import Site
    app = SocialApp.objects.create(
        provider="google", name="Google",
        client_id="test-google-client-id", secret="test-google-secret")
    site = Site.objects.get_or_create(id=1, defaults={"domain": "testserver", "name": "testserver"})[0]
    app.sites.add(site)
    return app
