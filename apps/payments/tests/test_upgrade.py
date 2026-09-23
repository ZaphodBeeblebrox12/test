"""Self-serve upgrade: proration math, eligibility, activation finalize."""
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.payments.models import PaymentIntent
from apps.payments.services import (
    UpgradeError, activate_paid_subscription, compute_upgrade_quote)
from apps.subscriptions.models import (
    Plan, Subscription, SubscriptionHistory, UpgradeHistory)

User = get_user_model()


def _plans():
    basic = Plan.objects.create(name="Basic", tier="basic", is_active=True,
                                display_order=1)
    pro = Plan.objects.create(name="Pro", tier="pro", is_active=True,
                              display_order=2)
    vip = Plan.objects.create(name="VIP", tier="vip", is_active=True,
                              display_order=3)
    bp = basic.prices.create(price_cents=3000, currency="USD",
                             interval="monthly", is_active=True)
    pp = pro.prices.create(price_cents=5000, currency="USD",
                           interval="monthly", is_active=True)
    vip.prices.create(price_cents=9000, currency="USD",
                      interval="monthly", is_active=True)
    return basic, pro, vip, bp, pp


class UpgradeQuoteTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="up1", password="x")
        self.basic, self.pro, self.vip, self.bp, self.pp = _plans()
        self.sub = Subscription.objects.create(
            user=self.user, plan=self.basic, plan_price=self.bp,
            status=Subscription.Status.ACTIVE, is_active=True,
            price_cents=3000, price_currency="USD",
            started_at=timezone.now() - timezone.timedelta(days=15),
            # +5min buffer: floor-day math must still see 15 full days.
            expires_at=timezone.now() + timezone.timedelta(days=15, minutes=5))

    def _resolved(self, cents):
        return SimpleNamespace(price_cents=cents, currency="USD")

    def test_proration_math(self):
        with mock.patch("apps.subscriptions.services.resolve_plan_price") as rp, \
             mock.patch("apps.subscriptions.services.split_resolved_price",
                        return_value={"plan_price": self.pp, "geo_plan_price": None,
                                      "price_currency": "USD"}):
            rp.return_value = self._resolved(5000)
            quote = compute_upgrade_quote(self.user, self.pro, None)
        # 15 of 30 days remaining: credit = 3000 * 15/30 = 1500; due = 5000-1500
        self.assertEqual(quote["prorated_credit_cents"], 1500)
        self.assertEqual(quote["amount_due_cents"], 3500)

    def test_same_plan_rejected(self):
        with self.assertRaises(UpgradeError):
            compute_upgrade_quote(self.user, self.basic, None)

    def test_lower_plan_rejected(self):
        with self.assertRaises(UpgradeError):
            compute_upgrade_quote(self.user, self.basic, None)
        sub2 = Subscription.objects.create(
            user=User.objects.create_user(username="up2", password="x"),
            plan=self.pro, plan_price=self.pp, status=Subscription.Status.ACTIVE,
            is_active=True, price_cents=5000, price_currency="USD")
        with self.assertRaises(UpgradeError):
            compute_upgrade_quote(sub2.user, self.basic, None)

    def test_no_subscription_rejected(self):
        u = User.objects.create_user(username="up3", password="x")
        with self.assertRaises(UpgradeError):
            compute_upgrade_quote(u, self.pro, None)


class UpgradeFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="up4", password="x")
        self.basic, self.pro, self.vip, self.bp, self.pp = _plans()
        self.sub = Subscription.objects.create(
            user=self.user, plan=self.basic, plan_price=self.bp,
            status=Subscription.Status.ACTIVE, is_active=True,
            price_cents=3000, price_currency="USD",
            expires_at=timezone.now() + timezone.timedelta(days=15))

    def test_page_renders_options(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("upgrade-page"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Pro")
        self.assertContains(r, "Due today")

    def test_start_creates_intent_and_history(self):
        self.client.force_login(self.user)
        checkout = SimpleNamespace(provider_reference="cs_up1")
        with mock.patch("apps.payments.views.providers.create_hosted_checkout",
                        return_value=checkout):
            r = self.client.post(reverse("upgrade-start"),
                                 {"plan_id": str(self.pro.pk)})
        self.assertEqual(r.status_code, 302)
        intent = PaymentIntent.objects.get()
        self.assertTrue(intent.is_upgrade)
        self.assertEqual(intent.plan, self.pro)
        uh = UpgradeHistory.objects.get()
        self.assertFalse(uh.is_successful)
        self.assertEqual(uh.from_plan, self.basic)
        self.assertEqual(uh.to_plan, self.pro)

    def test_activation_finalizes_upgrade(self):
        intent = PaymentIntent.objects.create(
            user=self.user, plan=self.pro, amount=3500, currency="USD",
            provider="stripe", status=PaymentIntent.Status.PENDING, country="US",
            provider_reference="cs_up2", is_upgrade=True)
        UpgradeHistory.objects.create(
            user=self.user, from_subscription=self.sub, from_plan=self.basic,
            to_plan=self.pro, from_price_cents=3000, to_price_cents=5000,
            prorated_credit_cents=1500, amount_due_cents=3500,
            is_successful=False)
        activated, new_sub = activate_paid_subscription(intent)
        self.assertTrue(activated)
        uh = UpgradeHistory.objects.get()
        self.assertTrue(uh.is_successful)
        self.assertEqual(uh.to_subscription, new_sub)
        # old sub auto-canceled by deactivation; upgraded event recorded
        self.sub.refresh_from_db()
        self.assertFalse(self.sub.is_active)
        ev = SubscriptionHistory.objects.get(
            event_type=SubscriptionHistory.EventType.UPGRADED)
        self.assertEqual(ev.previous_plan_id, self.basic.pk)
        self.assertEqual(ev.new_plan_id, self.pro.pk)
