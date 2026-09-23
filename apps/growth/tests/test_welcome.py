"""Welcome sequence tests (windows, guards, once-per-user dedupe)."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.bot_integration.models import TelegramAccount
from apps.events.models import Event
from apps.growth.services import welcomesequence as ws
from apps.subscriptions.models import Plan, Subscription

User = get_user_model()


def _user(name, hours_ago):
    return User.objects.create_user(
        username=name, password="x",
        date_joined=timezone.now() - timezone.timedelta(hours=hours_ago))


@mock.patch("apps.growth.services.welcomesequence._send")
class WelcomeTests(TestCase):
    def test_day1_sent_in_window(self, send):
        u = _user("w1", 30)
        self.assertTrue(ws._day1(u))
        send.assert_called_once()
        self.assertEqual(Event.objects.filter(event_type="welcome.day1").count(), 1)

    def test_day1_not_repeated(self, send):
        u = _user("w2", 30)
        ws._day1(u)
        send.reset_mock()
        self.assertFalse(ws._day1(u))
        send.assert_not_called()

    def test_day3_skipped_when_subscribed(self, send):
        u = _user("w3", 80)
        plan = Plan.objects.create(name="Pro", tier="pro", is_active=True,
                                   display_order=1)
        Subscription.objects.create(
            user=u, plan=plan, status=Subscription.Status.ACTIVE, is_active=True,
            price_cents=999, price_currency="USD")
        self.assertFalse(ws._day3(u))
        send.assert_not_called()

    def test_day3_skipped_when_linked(self, send):
        u = _user("w4", 80)
        TelegramAccount.objects.create(user=u, chat_id=9, telegram_user_id=9)
        self.assertFalse(ws._day3(u))
        send.assert_not_called()

    def test_day3_sent_to_stuck_signup(self, send):
        u = _user("w5", 80)
        self.assertTrue(ws._day3(u))
        send.assert_called_once()
        self.assertEqual(Event.objects.filter(event_type="welcome.day3").count(), 1)

    def test_sweep_respects_windows(self, send):
        _user("too_new", 5)     # <24h: nothing
        _user("d1a", 30)        # day-1 window
        _user("d1b", 40)        # day-1 window
        _user("d3a", 80)        # day-3 window
        _user("old", 200)       # >96h: nothing
        n = ws.run_welcome_sequence(periodic_job=None)
        self.assertEqual(n, 3)
        self.assertEqual(Event.objects.filter(event_type="welcome.day1").count(), 2)
        self.assertEqual(Event.objects.filter(event_type="welcome.day3").count(), 1)
