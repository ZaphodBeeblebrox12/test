"""
Tests for Telegram authentication.
"""
import hashlib
import hmac

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework import status

from apps.audit.models import AuditLog
from integrations.telegram.auth import (
    parse_telegram_auth_data,
    verify_telegram_auth_hash,
)

User = get_user_model()


class TestTelegramHashVerification:
    """Test Telegram hash verification algorithm."""

    def test_verify_valid_hash(self):
        """Test verification with valid hash."""
        # Create a known valid hash
        token = "test_token_12345"
        data = {
            "id": 123456789,
            "auth_date": 1704067200,
            "first_name": "Test",
            "username": "testuser",
        }

        # Build data-check-string
        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(data.items())
        )

        # Compute hash
        secret_key = hashlib.sha256(token.encode()).digest()
        computed_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        # Add hash to data
        data["hash"] = computed_hash

        # Verify
        assert verify_telegram_auth_hash(data, token) is True

    def test_verify_invalid_hash(self):
        """Test verification with invalid hash."""
        data = {
            "id": 123456789,
            "auth_date": 1704067200,
            "first_name": "Test",
            "hash": "invalid_hash",
        }

        assert verify_telegram_auth_hash(data, "test_token") is False

    def test_verify_missing_hash(self):
        """Test verification with missing hash field."""
        data = {
            "id": 123456789,
            "auth_date": 1704067200,
            "first_name": "Test",
        }

        assert verify_telegram_auth_hash(data, "test_token") is False

    def test_verify_tampered_data(self):
        """Test verification detects tampered data."""
        token = "test_token_12345"
        data = {
            "id": 123456789,
            "auth_date": 1704067200,
            "first_name": "Test",
        }

        # Create valid hash
        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(data.items())
        )
        secret_key = hashlib.sha256(token.encode()).digest()
        computed_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        data["hash"] = computed_hash

        # Verify original works
        assert verify_telegram_auth_hash(data, token) is True

        # Tamper with data
        data["id"] = 999999999

        # Verify fails
        assert verify_telegram_auth_hash(data, token) is False


@pytest.mark.django_db
class TestTelegramAuthView:
    """Test Telegram authentication endpoint."""

    def test_telegram_auth_creates_user(self, client, telegram_auth_data):
        """Test that Telegram auth creates a new user."""
        # Create valid hash for the data
        token = settings.TELEGRAM_BOT_TOKEN or "test_token"
        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(telegram_auth_data.items()) if v
        )
        secret_key = hashlib.sha256(token.encode()).digest()
        computed_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        telegram_auth_data["hash"] = computed_hash

        response = client.post(
            "/api/auth/telegram/",
            telegram_auth_data,
            content_type="application/json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["success"] is True
        assert response.json()["created"] is True

        # Verify user was created
        user = User.objects.get(telegram_id=telegram_auth_data["id"])
        assert user.telegram_username == telegram_auth_data["username"]
        assert user.first_name == telegram_auth_data["first_name"]

        # Verify audit log was created
        assert AuditLog.objects.filter(
            action="user_created",
            object_id=str(user.id)
        ).exists()

    def test_telegram_auth_invalid_hash(self, client, telegram_auth_data):
        """Test that invalid hash is rejected."""
        telegram_auth_data["hash"] = "invalid_hash"

        response = client.post(
            "/api/auth/telegram/",
            telegram_auth_data,
            content_type="application/json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "Invalid authentication hash" in response.json()["error"]

    def test_telegram_auth_banned_user(self, client, banned_user, telegram_auth_data):
        """Test that banned users cannot log in."""
        # Use banned user's telegram_id
        telegram_auth_data["id"] = banned_user.telegram_id

        # Create valid hash
        token = settings.TELEGRAM_BOT_TOKEN or "test_token"
        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(telegram_auth_data.items()) if v
        )
        secret_key = hashlib.sha256(token.encode()).digest()
        computed_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        telegram_auth_data["hash"] = computed_hash

        response = client.post(
            "/api/auth/telegram/",
            telegram_auth_data,
            content_type="application/json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "Account is banned" in response.json()["error"]


class TestParseTelegramAuthData:
    """Test parsing Telegram auth data."""

    def test_parse_valid_data(self):
        """Test parsing valid auth data."""
        data = {
            "id": 123456789,
            "auth_date": 1704067200,
            "first_name": "Test",
            "last_name": "User",
            "username": "testuser",
            "photo_url": "https://example.com/photo.jpg",
            "hash": "abc123",
        }

        result = parse_telegram_auth_data(data)

        assert result.id == 123456789
        assert result.auth_date == 1704067200
        assert result.first_name == "Test"
        assert result.last_name == "User"
        assert result.username == "testuser"
        assert result.photo_url == "https://example.com/photo.jpg"
        assert result.hash == "abc123"

    def test_parse_minimal_data(self):
        """Test parsing minimal auth data."""
        data = {
            "id": 123456789,
            "auth_date": 1704067200,
            "first_name": "Test",
            "hash": "abc123",
        }

        result = parse_telegram_auth_data(data)

        assert result.id == 123456789
        assert result.first_name == "Test"
        assert result.last_name == ""
        assert result.username == ""
