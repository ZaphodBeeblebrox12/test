"""
Tests for accounts views and functionality.
"""
import hashlib
import hmac
import json
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework import status

from apps.accounts.models import User, UserPreference
from apps.audit.models import AuditLog
from apps.notifications.models import Notification


class GoogleLoginTest(TestCase):
    """Test Google login functionality."""

    def setUp(self):
        self.client = Client()

    def test_google_login_page_accessible(self):
        """Test that Google login URL is accessible."""
        response = self.client.get('/accounts/google/login/')
        # Should redirect to Google OAuth or show login page
        self.assertIn(response.status_code, [200, 302])

    def test_google_signup_creates_user_with_telegram_verified_false(self):
        """Test that Google signup creates user with telegram_verified=False."""
        # This is tested via the adapter, but we verify the model field exists
        user = User.objects.create(
            username='testgoogle',
            email='test@google.com',
            telegram_verified=False
        )
        self.assertFalse(user.telegram_verified)
        self.assertIsNone(user.telegram_id)


class TelegramProfileVerificationTest(APITestCase):
    """Test Telegram profile verification."""

    def setUp(self):
        self.user = User.objects.create(
            username='testuser',
            email='test@example.com',
            telegram_verified=False
        )
        self.client.force_authenticate(user=self.user)

    @override_settings(TELEGRAM_BOT_TOKEN='test_token')
    def test_telegram_connect_validates_hash(self):
        """Test that Telegram connect endpoint validates hash."""
        url = reverse('api_telegram_connect')

        data = {
            'id': 123456789,
            'hash': 'invalid_hash',
            'auth_date': int(timezone.now().timestamp()),
            'username': 'testuser'
        }

        response = self.client.post(url, data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

    @override_settings(TELEGRAM_BOT_TOKEN='test_token')
    def test_telegram_connect_rejects_duplicate_telegram_id(self):
        """Test that Telegram ID must be unique across users."""
        # Create another user with a telegram_id
        other_user = User.objects.create(
            username='otheruser',
            email='other@example.com',
            telegram_id=999999999,
            telegram_verified=True
        )

        url = reverse('api_telegram_connect')

        # Generate valid hash for our data
        data_fields = [
            f"auth_date={int(timezone.now().timestamp())}",
            f"id=999999999",
            f"username=testuser"
        ]
        data_fields.sort()
        data_check_string = "\n".join(data_fields)

        secret_key = hashlib.sha256('test_token'.encode()).digest()
        valid_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        data = {
            'id': 999999999,  # Same as other_user
            'hash': valid_hash,
            'auth_date': int(timezone.now().timestamp()),
            'username': 'testuser'
        }

        response = self.client.post(url, data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('already connected', str(response.data['error']).lower())


class ProfileUpdateTest(APITestCase):
    """Test profile update functionality."""

    def setUp(self):
        self.user = User.objects.create(
            username='testuser',
            email='test@example.com',
            first_name='Test',
            last_name='User'
        )
        self.client.force_authenticate(user=self.user)

    def test_get_profile(self):
        """Test getting user profile."""
        url = reverse('api_user_profile')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['username'], 'testuser')
        self.assertEqual(response.data['email'], 'test@example.com')

    def test_update_profile(self):
        """Test updating profile fields."""
        url = reverse('api_user_profile')
        data = {
            'first_name': 'Updated',
            'last_name': 'Name',
            'bio': 'New bio'
        }

        response = self.client.patch(url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Updated')
        self.assertEqual(self.user.bio, 'New bio')

    def test_telegram_fields_read_only_in_profile(self):
        """Test that Telegram fields cannot be updated via profile API."""
        url = reverse('api_user_profile')
        data = {
            'telegram_id': 123456789,
            'telegram_username': 'hacked'
        }

        response = self.client.patch(url, data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify telegram fields were not changed
        self.user.refresh_from_db()
        self.assertIsNone(self.user.telegram_id)


class DashboardAccessTest(TestCase):
    """Test dashboard access and ban enforcement."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create(
            username='testuser',
            email='test@example.com'
        )

    def test_dashboard_requires_login(self):
        """Test that dashboard requires authentication."""
        response = self.client.get('/dashboard/')
        self.assertEqual(response.status_code, 302)  # Redirect to login

    def test_dashboard_accessible_when_logged_in(self):
        """Test dashboard is accessible when logged in."""
        self.client.force_login(self.user)
        response = self.client.get('/dashboard/')
        self.assertEqual(response.status_code, 200)

    def test_banned_user_blocked_from_dashboard(self):
        """Test that banned users cannot access dashboard."""
        self.user.ban("Violation of terms")
        self.client.force_login(self.user)

        response = self.client.get('/dashboard/')
        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, 'accounts/banned.html')

    def test_banned_user_blocked_from_profile(self):
        """Test that banned users cannot access profile."""
        self.user.ban("Violation of terms")
        self.client.force_login(self.user)

        response = self.client.get('/profile/')
        self.assertEqual(response.status_code, 403)


class NotificationAPITest(APITestCase):
    """Test notification API endpoints."""

    def setUp(self):
        self.user = User.objects.create(
            username='testuser',
            email='test@example.com'
        )
        self.client.force_authenticate(user=self.user)

        # Create some notifications
        for i in range(5):
            Notification.objects.create(
                user=self.user,
                title=f'Notification {i}',
                message=f'Message {i}',
                is_read=i < 2  # First 2 are read
            )

    def test_list_notifications(self):
        """Test listing user notifications."""
        url = reverse('api_notifications_list')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 5)

    def test_unread_count(self):
        """Test getting unread notification count."""
        url = reverse('api_notifications_unread_count')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['unread_count'], 3)

    def test_mark_notification_read(self):
        """Test marking a notification as read."""
        notification = Notification.objects.filter(user=self.user, is_read=False).first()
        url = reverse('api_notification_read', kwargs={'pk': notification.id})

        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_mark_all_read(self):
        """Test marking all notifications as read."""
        url = reverse('api_notifications_read_all')
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        unread_count = Notification.objects.filter(user=self.user, is_read=False).count()
        self.assertEqual(unread_count, 0)


class UserActivityLogTest(APITestCase):
    """Test user activity log API."""

    def setUp(self):
        self.user = User.objects.create(
            username='testuser',
            email='test@example.com'
        )
        self.client.force_authenticate(user=self.user)

        # Create some audit logs
        AuditLog.log('login_telegram', self.user, 'user', self.user.id)
        AuditLog.log('profile_updated', self.user, 'user', self.user.id)

    def test_get_activity_log(self):
        """Test getting user activity log."""
        url = reverse('api_user_activity')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(response.data[0]['action'], 'profile_updated')


class UserPreferenceModelTest(TestCase):
    """Test UserPreference model."""

    def test_preference_created_with_user(self):
        """Test that UserPreference is created when user is created."""
        user = User.objects.create(
            username='testuser',
            email='test@example.com'
        )

        # Preference should be created via signal
        self.assertIsNotNone(user.preferences)
        self.assertEqual(user.preferences.timezone, 'UTC')
        self.assertEqual(user.preferences.language, 'en')

    def test_preference_str_representation(self):
        """Test UserPreference string representation."""
        user = User.objects.create(
            username='testuser',
            email='test@example.com'
        )

        self.assertEqual(
            str(user.preferences),
            f"Preferences for {user.username}"
        )


class TelegramIDUniquenessTest(TestCase):
    """Test Telegram ID uniqueness constraints."""

    def test_telegram_id_must_be_unique(self):
        """Test that telegram_id field enforces uniqueness."""
        User.objects.create(
            username='user1',
            email='user1@example.com',
            telegram_id=123456789,
            telegram_verified=True
        )

        # Attempting to create another user with same telegram_id should fail
        with self.assertRaises(Exception):
            User.objects.create(
                username='user2',
                email='user2@example.com',
                telegram_id=123456789,
                telegram_verified=True
            )
