"""Expiry reminder tests (Stage 3). Transactional; no real Telegram/email."""

from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.bot_integration.services.telegram_transport import (
    OUTCOME_ACCEPTED, TelegramMessageResult)
from apps.subscriptions.models import (
    Plan, Subscription, SubscriptionReminder)
from apps.subscriptions.services import send_expiry_reminders

OK_TG = TelegramMessageResult(outcome=OUTCOME_ACCEPTED, message_id=481516)


def make_sub(user, plan, expires_at, status=Subscription.Status.ACTIVE,
             is_active=True):
    return Subscription.objects.create(
        user=user, plan=plan, status=status, is_active=is_active,
        expires_at=expires_at)


def user_with_telegram(email="c@example.com", chat_id=424242):
    u = User.objects.create_user(username="c1", password="pw", email=email)
    from apps.bot_integration.models import TelegramAccount
    TelegramAccount.objects.create(user=u, chat_id=chat_id, is_active=True)
    return u


class ExpiryReminderTests(TestCase):
    def setUp(self):
        self.plan = Plan.objects.create(name="Pro", tier="pro", display_order=1)
        self.now = timezone.now()

    def send_calls(self, m_tg):
        return [c for c in m_tg.call_args_list]

    @mock.patch("apps.notifications.services.NotificationService.send_email")
    @mock.patch("apps.bot_integration.transactional.TelegramBotService.send_message_result",
                return_value=OK_TG)
    def test_pre_expiry_sends_both_channels_and_records_row(self, m_tg, m_email):
        u = user_with_telegram()
        sub = make_sub(u, self.plan, self.now + timedelta(days=3, hours=2))
        self.assertEqual(send_expiry_reminders(now=self.now), 1)
        rem = SubscriptionReminder.objects.get(subscription=sub)
        self.assertEqual(rem.kind, SubscriptionReminder.Kind.PRE_EXPIRY)
        self.assertEqual(rem.telegram_message_id, 481516)
        self.assertTrue(rem.email_sent)
        self.assertEqual(rem.email_recipient, "c@example.com")
        m_email.assert_called_once()
        m_tg.assert_called_once()
        self.assertIn("expires in 3 days", m_tg.call_args[0][1])

    @mock.patch("apps.notifications.services.NotificationService.send_email")
    @mock.patch("apps.bot_integration.transactional.TelegramBotService.send_message_result",
                return_value=OK_TG)
    def test_second_run_sends_nothing(self, m_tg, m_email):
        u = user_with_telegram()
        make_sub(u, self.plan, self.now + timedelta(days=3, hours=2))
        self.assertEqual(send_expiry_reminders(now=self.now), 1)
        m_tg.reset_mock(); m_email.reset_mock()
        self.assertEqual(send_expiry_reminders(now=self.now), 0)
        m_tg.assert_not_called()
        m_email.assert_not_called()
        self.assertEqual(SubscriptionReminder.objects.count(), 1)

    @mock.patch("apps.notifications.services.NotificationService.send_email")
    @mock.patch("apps.bot_integration.transactional.TelegramBotService.send_message_result",
                return_value=OK_TG)
    def test_no_channels_releases_claim_for_retry(self, m_tg, m_email):
        u = User.objects.create_user(username="c2", password="pw", email="")
        make_sub(u, self.plan, self.now + timedelta(days=3, hours=2))
        self.assertEqual(send_expiry_reminders(now=self.now), 0)
        self.assertEqual(SubscriptionReminder.objects.count(), 0)
        # later, user links telegram -> next run delivers exactly once
        from apps.bot_integration.models import TelegramAccount
        TelegramAccount.objects.create(user=u, chat_id=999, is_active=True)
        self.assertEqual(send_expiry_reminders(now=self.now), 1)
        self.assertEqual(SubscriptionReminder.objects.count(), 1)

    @mock.patch("apps.notifications.services.NotificationService.send_email")
    @mock.patch("apps.bot_integration.transactional.TelegramBotService.send_message_result",
                return_value=OK_TG)
    def test_email_only_user_gets_email_row_kept(self, m_tg, m_email):
        u = User.objects.create_user(username="c3", password="pw",
                                     email="only@example.com")
        make_sub(u, self.plan, self.now + timedelta(days=3, hours=2))
        self.assertEqual(send_expiry_reminders(now=self.now), 1)
        rem = SubscriptionReminder.objects.get()
        self.assertTrue(rem.email_sent)
        self.assertIsNone(rem.telegram_message_id)
        m_email.assert_called_once()
        m_tg.assert_not_called()

    @mock.patch("apps.notifications.services.NotificationService.send_email")
    @mock.patch("apps.bot_integration.transactional.TelegramBotService.send_message_result",
                return_value=OK_TG)
    def test_post_expiry_reminder_after_expiry(self, m_tg, m_email):
        u = user_with_telegram()
        make_sub(u, self.plan, self.now - timedelta(hours=1),
                 status=Subscription.Status.EXPIRED, is_active=False)
        self.assertEqual(send_expiry_reminders(now=self.now), 1)
        rem = SubscriptionReminder.objects.get()
        self.assertEqual(rem.kind, SubscriptionReminder.Kind.POST_EXPIRY)
        self.assertIn("access has ended", m_tg.call_args[0][1])
        m_email.assert_called_once()

    @mock.patch("apps.notifications.services.NotificationService.send_email")
    @mock.patch("apps.bot_integration.transactional.TelegramBotService.send_message_result",
                return_value=OK_TG)
    def test_renewal_before_expiry_no_stale_reminder(self, m_tg, m_email):
        u = user_with_telegram()
        sub = make_sub(u, self.plan, self.now + timedelta(days=3, hours=2))
        # renewal moves expiry far outside the window
        sub.expires_at = self.now + timedelta(days=30)
        sub.save()
        self.assertEqual(send_expiry_reminders(now=self.now), 0)
        self.assertEqual(SubscriptionReminder.objects.count(), 0)
        m_tg.assert_not_called(); m_email.assert_not_called()

    @mock.patch("apps.notifications.services.NotificationService.send_email")
    @mock.patch("apps.bot_integration.transactional.TelegramBotService.send_message_result",
                return_value=OK_TG)
    def test_window_boundaries(self, m_tg, m_email):
        u = user_with_telegram()
        make_sub(u, self.plan, self.now + timedelta(days=2, hours=23))  # <3d: skip
        make_sub(u, self.plan, self.now + timedelta(days=4))            # >=4d: skip
        make_sub(u, self.plan, self.now + timedelta(days=3, minutes=1)) # in window
        self.assertEqual(send_expiry_reminders(now=self.now), 1)
        self.assertEqual(SubscriptionReminder.objects.count(), 1)

    def test_no_marketing_or_consent_logic_in_reminder_path(self):
        import apps.subscriptions.services as svc
        for name in dir(svc):
            low = name.lower()
            for bad in ("consent", "marketing", "suppress", "unsubscribe", "campaign"):
                self.assertNotIn(bad, low, "reminder path leaks: " + name)
