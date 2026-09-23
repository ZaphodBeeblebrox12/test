"""Phase 0-2 page tests: support/policies, manage-sub, receipt ownership."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.payments.models import PaymentIntent
from apps.subscriptions.models import Plan

User = get_user_model()


class StaticPageTests(TestCase):
    def test_support_page(self):
        self.assertEqual(self.client.get("/support/").status_code, 200)

    def test_policy_pages(self):
        for path in ("/policies/refund/", "/policies/terms/", "/policies/risk/"):
            self.assertEqual(self.client.get(path).status_code, 200, path)


class ManageSubscriptionPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ms1", password="x")
        self.plan = Plan.objects.create(name="Pro", tier="pro",
                                        is_active=True, display_order=1)

    def test_requires_login(self):
        r = self.client.get(reverse("manage-subscription"))
        self.assertIn(r.status_code, (301, 302))

    def test_renders_for_authenticated(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("manage-subscription"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Manage Subscription")


class ReceiptPageTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="rc1", password="x")
        self.other = User.objects.create_user(username="rc2", password="x")
        self.plan = Plan.objects.create(name="Pro", tier="pro",
                                        is_active=True, display_order=1)
        self.intent = PaymentIntent.objects.create(
            user=self.owner, plan=self.plan, amount=999, currency="USD",
            provider="stripe", status="success", country="US",
            provider_reference="cs_1")

    def test_owner_can_view(self):
        self.client.force_login(self.owner)
        r = self.client.get(f"/receipt/{self.intent.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Total paid")

    def test_other_user_gets_404(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f"/receipt/{self.intent.pk}/").status_code, 404)

    def test_anonymous_redirected(self):
        r = self.client.get(f"/receipt/{self.intent.pk}/")
        self.assertIn(r.status_code, (301, 302))
