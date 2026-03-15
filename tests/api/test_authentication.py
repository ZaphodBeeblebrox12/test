"""
Tests for API authentication.
"""
from django.test import TestCase
from rest_framework.test import APITestCase
from rest_framework import status

from apps.accounts.models import User
from apps.api.models import APIKey


class APIKeyAuthenticationTest(APITestCase):
    """Test API key authentication."""

    def setUp(self):
        self.user = User.objects.create(
            username='testuser',
            email='test@example.com'
        )

        # Create API key
        raw_key = APIKey.generate_key()
        self.api_key = APIKey.objects.create(
            user=self.user,
            name='Test Key',
            key_hash=APIKey.hash_key(raw_key),
            key_prefix=raw_key[:8],
            is_active=True
        )
        self.raw_key = raw_key

    def test_api_key_list_requires_auth(self):
        """Test that API key list requires authentication."""
        response = self.client.get('/api/auth/keys/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_api_key_list_authenticated(self):
        """Test API key list when authenticated."""
        self.client.force_authenticate(user=self.user)
        response = self.client.get('/api/auth/keys/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_api_key_generation(self):
        """Test API key generation."""
        key = APIKey.generate_key()
        self.assertTrue(key.startswith('live_'))
        self.assertEqual(len(key), 39)  # 'live_' + 32 char token + padding

    def test_api_key_hashing(self):
        """Test API key hashing."""
        key = 'test_key_12345'
        hash1 = APIKey.hash_key(key)
        hash2 = APIKey.hash_key(key)

        self.assertEqual(hash1, hash2)
        self.assertEqual(len(hash1), 64)  # SHA-256 hex length
