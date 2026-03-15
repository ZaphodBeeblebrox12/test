"""
Tests for notifications models.
"""
from django.test import TestCase
from apps.accounts.models import User
from apps.notifications.models import Notification, EmailLog


class NotificationModelTest(TestCase):
    """Test Notification model."""

    def setUp(self):
        self.user = User.objects.create(
            username='testuser',
            email='test@example.com'
        )

    def test_notification_creation(self):
        """Test creating a notification."""
        notification = Notification.objects.create(
            user=self.user,
            notification_type='system',
            title='Test Notification',
            message='This is a test message'
        )

        self.assertEqual(notification.title, 'Test Notification')
        self.assertEqual(notification.user, self.user)
        self.assertFalse(notification.is_read)

    def test_notification_str(self):
        """Test notification string representation."""
        notification = Notification.objects.create(
            user=self.user,
            title='Test',
            message='Message'
        )

        self.assertEqual(str(notification), f"Test - {self.user.username}")


class EmailLogModelTest(TestCase):
    """Test EmailLog model."""

    def setUp(self):
        self.user = User.objects.create(
            username='testuser',
            email='test@example.com'
        )

    def test_email_log_creation(self):
        """Test creating an email log."""
        log = EmailLog.objects.create(
            user=self.user,
            email='test@example.com',
            template='welcome',
            subject='Welcome!',
            status='sent'
        )

        self.assertEqual(log.template, 'welcome')
        self.assertEqual(log.status, 'sent')
