"""Win-back offer tests (guards + idempotency + existing-wiring reuse)."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.events.models import Event
from apps.growth.services import winback
from apps.payments.models import PaymentIntent
from apps.promotions.models import Coupon
from apps.subscriptions.models import Plan, Subscription

User = get_user_model()


def _expired_user(name="wb1", days_ago=3):
    user = User.objects.create_user(username=name, password="x")
    plan, _ = Plan.objects.get_or_create(
        tier="pro", is_trial=False,
        defaults={"name": "Pro", "is_active": True, "display_order": 1})
    sub = Subscription.objects.create(
        user=user, plan=plan, status=Subscription.Status.EXPIRED,
        is_active=False, price_cents=999, price_currency="USD",
        expires_at=timezone.now() - timezone.timedelta(days=days_ago))
    # Subscription.save() may normalize status; force the true expired state
    # via queryset update (the same way the production sweeper writes it).
    Subscription.objects.filter(pk=sub.pk).update(
        status=Subscription.Status.EXPIRED, is_active=False)
    return user, plan


@mock.patch("apps.growth.services.winback._send_offer")
@mock.patch("apps.notifications.services.NotificationService.send_email")
class WinBackTests(TestCase):
    def test_expired_user_gets_offer(self, _mail, send):
        user, _plan = _expired_user()
        offered = winback.maybe_offer_user(user)
        self.assertTrue(offered)
        coupon = Coupon.objects.get()
        self.assertEqual(coupon.percent_off, 20)
        self.assertTrue(coupon.active)
        self.assertEqual(coupon.max_redemptions, 1)
        self.assertEqual(coupon.max_per_user, 1)
        self.assertGreater(coupon.valid_until, timezone.now())
        send.assert_called_once()
        ev = Event.objects.get(event_type="winback.offered")
        self.assertEqual(ev.payload["code"], coupon.code)

    def test_active_subscriber_skipped(self, _mail, send):
        user, plan = _expired_user()
        Subscription.objects.create(
            user=user, plan=plan, status=Subscription.Status.ACTIVE,
            is_active=True, price_cents=999, price_currency="USD")
        self.assertFalse(winback.maybe_offer_user(user))
        self.assertEqual(Coupon.objects.count(), 0)
        send.assert_not_called()

    def test_second_run_is_noop(self, _mail, send):
        user, _ = _expired_user()
        self.assertTrue(winback.maybe_offer_user(user))
        send.reset_mock()
        self.assertFalse(winback.maybe_offer_user(user))
        self.assertEqual(Coupon.objects.count(), 1)
        send.assert_not_called()

    def test_currency_follows_last_payment(self, _mail, send):
        user, _ = _expired_user()
        plan = Plan.objects.get(tier="pro", is_trial=False)
        PaymentIntent.objects.create(
            user=user, plan=plan, amount=999, currency="EUR",
            provider="stripe", status="success", country="DE",
            provider_reference="x")
        winback.maybe_offer_user(user)
        self.assertEqual(Coupon.objects.get().currency, "EUR")

    def test_sweep_only_targets_window(self, _mail, send):
        from datetime import timedelta
        _expired_user("old", days_ago=10)
        fresh, _ = _expired_user("fresh", days_ago=3)
        # Move "fresh" to mid-window (84h ago): unambiguous under the
        # canonical [96h, 72h) sweep regardless of microsecond boundaries.
        Subscription.objects.filter(user=fresh).update(
            expires_at=timezone.now() - timedelta(hours=84))
        # Loud pre-check: the sweep's own selection must see exactly "fresh".
        sel = list(Subscription.objects.filter(
            status=Subscription.Status.EXPIRED, is_active=False,
            expires_at__gte=timezone.now() - timedelta(days=4),
            expires_at__lt=timezone.now() - timedelta(days=3),
        ).values_list("user", flat=True))
        self.assertEqual(sel, [fresh.pk],
                         "sweep selection mismatch - stale winback service?")
        n = winback.run_win_back(periodic_job=None)
        self.assertEqual(n, 1)
        self.assertEqual(Coupon.objects.count(), 1)
