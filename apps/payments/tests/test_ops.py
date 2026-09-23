"""Phase 3 ops-cockpit tests."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.bot_integration.models import (
    BotAccessAudit, PlanChannelMapping, TelegramAccount, UserChannelAssignment)
from apps.jobs.models import Job
from apps.notifications.models import Notification
from apps.payments.models import PaymentIntent
from apps.subscriptions.models import Plan, Subscription

User = get_user_model()


class OpsAccessTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="op1", password="x",
                                              is_staff=True)
        self.plain = User.objects.create_user(username="op2", password="x")

    def test_anonymous_redirected(self):
        self.assertIn(self.client.get("/staff/ops/").status_code, (301, 302))

    def test_non_staff_forbidden(self):
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get("/staff/ops/").status_code, 302)

    def test_staff_sees_dashboard(self):
        self.client.force_login(self.staff)
        r = self.client.get("/staff/ops/")
        self.assertEqual(r.status_code, 200)
        for marker in ("Pending jobs", "Paid but no channel access",
                       "Periodic jobs", "Failed payments"):
            self.assertContains(r, marker)


class OpsWorklistTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="ow1", password="x",
                                              is_staff=True)
        self.user = User.objects.create_user(username="ow2", password="x")
        self.plan = Plan.objects.create(name="Pro", tier="pro",
                                        is_active=True, display_order=1)
        self.client.force_login(self.staff)

    def test_paid_no_access_listed(self):
        Subscription.objects.create(
            user=self.user, plan=self.plan, status=Subscription.Status.ACTIVE,
            is_active=True, price_cents=999, price_currency="USD")
        PlanChannelMapping.objects.create(plan=self.plan, platform="telegram",
                                          external_id="-100", name="VIP")
        r = self.client.get("/staff/ops/")
        self.assertContains(r, "ow2")
        self.assertContains(r, "-100")

    def test_reconcile_action_enqueues(self):
        with mock.patch("apps.jobs.enqueue.enqueue_reconcile") as enq:
            r = self.client.post(reverse("ops-reconcile-user", args=[self.user.pk]))
        self.assertEqual(r.status_code, 302)
        enq.assert_called_once()

    def test_everyone_in_shows_green(self):
        sub = Subscription.objects.create(
            user=self.user, plan=self.plan, status=Subscription.Status.ACTIVE,
            is_active=True, price_cents=999, price_currency="USD")
        PlanChannelMapping.objects.create(plan=self.plan, platform="telegram",
                                          external_id="-100", name="VIP")
        UserChannelAssignment.objects.create(user=self.user, platform="telegram",
                                             external_id="-100")
        r = self.client.get("/staff/ops/")
        self.assertContains(r, "Everyone entitled is in")


class OpsAlertTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_superuser(username="oa1", email="a@a.com",
                                                   password="x")
        self.client.force_login(self.staff)

    def test_queue_backlog_alerts_staff(self):
        Job.objects.create(kind="process_webhook_event", payload={},
                           status="pending",
                           next_attempt_at=timezone.now()
                           - timezone.timedelta(minutes=30))
        self.client.get("/staff/ops/")
        self.assertTrue(Notification.objects.filter(
            user=self.staff, notification_type=Notification.NotificationType.SYSTEM,
            title__icontains="pending").exists())

    def test_no_alert_when_healthy(self):
        self.client.get("/staff/ops/")
        self.assertEqual(Notification.objects.count(), 0)


class OpsRevenueAndCsvTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="or1", password="x",
                                              is_staff=True)
        self.user = User.objects.create_user(username="or2", password="x")
        self.plan = Plan.objects.create(name="Pro", tier="pro",
                                        is_active=True, display_order=1)
        PaymentIntent.objects.create(
            user=self.user, plan=self.plan, amount=5000, currency="USD",
            provider="stripe", status="success", country="US",
            provider_reference="cs_9")
        self.client.force_login(self.staff)

    def test_revenue_rendered(self):
        r = self.client.get("/staff/ops/")
        self.assertContains(r, "50.00")

    def test_csv_export(self):
        r = self.client.get("/staff/ops/payments.csv")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "text/csv")
        self.assertIn(b"cs_9", r.content)

    def test_user360(self):
        r = self.client.get(reverse("ops-user-detail", args=[self.user.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "or2")
        self.assertContains(r, "Subscriptions")
        self.assertContains(r, "Payments")
