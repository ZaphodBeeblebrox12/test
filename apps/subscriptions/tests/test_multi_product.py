"""Product-scoped subscriptions: simultaneous subscriptions, per-product
uniqueness, and legacy global behavior when no product is configured."""
import datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from apps.subscriptions.models import (
    GeoPlanPrice, Plan, PlanPrice, Product, Subscription,
)
from apps.subscriptions.services import purchase_plan

User = get_user_model()


def make_user(username="mpuser"):
    return User.objects.create_user(username=username, email=f"{username}@t.co")


def make_product(name, slug, order=0):
    return Product.objects.create(name=name, slug=slug, display_order=order)


def make_plan(name, tier, order, product=None, **kwargs):
    return Plan.objects.create(name=name, tier=tier, display_order=order,
                               product=product, **kwargs)


class ProductModelTests(TestCase):
    def test_same_tier_allowed_in_different_products(self):
        p1 = make_product("Trade Thesis", "trade-thesis")
        p2 = make_product("Trade Terminal", "trade-terminal")
        make_plan("Pro", "pro", 10, product=p1)
        make_plan("Pro", "pro", 10, product=p2)  # must not raise

    def test_duplicate_tier_within_product_rejected(self):
        p1 = make_product("Trade Thesis", "trade-thesis")
        make_plan("Pro", "pro", 10, product=p1)
        with self.assertRaises((IntegrityError, ValidationError)):
            make_plan("Pro Again", "pro", 11, product=p1)

    def test_duplicate_tier_ungrouped_rejected(self):
        make_plan("Pro", "pro", 10)
        with self.assertRaises((IntegrityError, ValidationError)):
            make_plan("Pro 2", "pro", 11)

    def test_product_and_ungrouped_same_tier_coexist(self):
        p1 = make_product("Trade Thesis", "trade-thesis")
        make_plan("Pro", "pro", 10)                 # ungrouped
        make_plan("Pro", "pro", 10, product=p1)     # product — must not raise


class SimultaneousSubscriptionTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.thesis = make_product("Trade Thesis", "trade-thesis")
        self.terminal = make_product("Trade Terminal", "trade-terminal")
        self.thesis_plan = make_plan("Thesis Pro", "pro", 10, product=self.thesis)
        self.terminal_plan = make_plan("Terminal Pro", "pro", 10, product=self.terminal)

    def test_two_products_can_be_active_simultaneously(self):
        s1 = Subscription.objects.create(
            user=self.user, plan=self.thesis_plan, is_active=True,
            status=Subscription.Status.ACTIVE)
        s2 = Subscription.objects.create(
            user=self.user, plan=self.terminal_plan, is_active=True,
            status=Subscription.Status.ACTIVE)
        s1.refresh_from_db(); s2.refresh_from_db()
        self.assertTrue(s1.is_active)
        self.assertTrue(s2.is_active)
        self.assertEqual(s1.product_id, self.thesis.id)   # snapshot set
        self.assertEqual(s2.product_id, self.terminal.id)

    def test_swap_within_product_does_not_touch_other_products(self):
        higher = make_plan("Thesis Elite", "enterprise", 20, product=self.thesis)
        s1 = Subscription.objects.create(
            user=self.user, plan=self.thesis_plan, is_active=True,
            status=Subscription.Status.ACTIVE)
        s2 = Subscription.objects.create(
            user=self.user, plan=self.terminal_plan, is_active=True,
            status=Subscription.Status.ACTIVE)
        Subscription.objects.create(   # upgrade inside thesis product
            user=self.user, plan=higher, is_active=True,
            status=Subscription.Status.ACTIVE)
        s1.refresh_from_db(); s2.refresh_from_db()
        self.assertFalse(s1.is_active)          # old thesis sub canceled
        self.assertEqual(s1.status, Subscription.Status.CANCELED)
        self.assertTrue(s2.is_active)           # terminal untouched

    def test_legacy_ungrouped_still_globally_exclusive(self):
        a = make_plan("Basic", "basic", 10)
        b = make_plan("Pro", "pro", 20)
        s1 = Subscription.objects.create(
            user=self.user, plan=a, is_active=True,
            status=Subscription.Status.ACTIVE)
        Subscription.objects.create(
            user=self.user, plan=b, is_active=True,
            status=Subscription.Status.ACTIVE)
        s1.refresh_from_db()
        self.assertFalse(s1.is_active)  # global behavior preserved

    def test_purchase_plan_trial_scoped_to_product(self):
        from unittest import mock
        trial = Plan.objects.create(
            name="Terminal Trial", tier="pro", display_order=5,
            product=self.terminal, is_trial=True, trial_duration_days=7)
        geo = GeoPlanPrice.objects.create(
            plan=trial, country="US", price_cents=0, currency="USD")
        Subscription.objects.create(
            user=self.user, plan=self.thesis_plan, is_active=True,
            status=Subscription.Status.ACTIVE)
        # Patch the region gate so this test exercises PRODUCT SCOPING,
        # not geo resolution.
        path = "apps.subscriptions.services.get_geo_price_for_trial"
        with mock.patch(path, return_value=geo):
            sub = purchase_plan(self.user, trial)
        self.assertTrue(sub.is_active)
        self.assertEqual(sub.product_id, self.terminal.id)
        thesis_sub = Subscription.objects.get(
            user=self.user, plan=self.thesis_plan)
        self.assertTrue(thesis_sub.is_active)  # NOT deactivated
