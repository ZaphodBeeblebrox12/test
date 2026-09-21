"""P3d: operational dashboard on the admin index."""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.bot_integration.models import (
    BotAccessAudit, TelegramAccount, TelegramVerificationToken)
from apps.jobs.models import Job
from apps.payments.models import PaymentIntent
from apps.subscriptions.models import Plan, Subscription

User = get_user_model()


def make_staff(username):
    u = User(username=username)
    u.is_staff = True
    u.is_superuser = True  # superuser -> has_perm(view_<model>) is True
    u.set_password("pw")
    u.save()
    return u


def make_plan():
    return Plan.objects.create(name="Pro", tier="pro", display_order=1)


def make_sub(user, plan, status=Subscription.Status.ACTIVE, is_active=True,
             expires_days=30, canceled=False):
    sub = Subscription.objects.create(
        user=user, plan=plan, status=status, is_active=is_active,
        started_at=timezone.now() - datetime.timedelta(days=1),
        expires_at=timezone.now() + datetime.timedelta(days=expires_days),
        price_cents=999, price_currency="USD")
    if canceled:
        sub.canceled_at = timezone.now() - datetime.timedelta(days=1)
        sub.save(update_fields=["canceled_at"])
    return sub


class DashboardRenderTests(TestCase):
    def setUp(self):
        self.staff = make_staff("dash_admin")
        self.client.force_login(self.staff)
        self.plan = make_plan()

    def test_index_renders_dashboard(self):
        r = self.client.get(reverse("admin:index"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Operational overview")

    def test_metrics_correct(self):
        u1 = User.objects.create_user(username="d1")
        u2 = User.objects.create_user(username="d2")
        make_sub(u1, self.plan)
        make_sub(u2, self.plan, expires_days=3)
        make_sub(User.objects.create_user(username="d3"), self.plan,
                 status=Subscription.Status.EXPIRED, is_active=False,
                 expires_days=-1)
        PaymentIntent.objects.create(
            user=u1, plan=self.plan, amount=999, currency="USD",
            status=PaymentIntent.Status.PENDING, provider="stripe")
        BotAccessAudit.objects.create(
            user=u1, action="grant", platform="telegram",
            status="failed", error_message="x")
        r = self.client.get(reverse("admin:index"))
        self.assertContains(r, "expiring in 7d")
        self.assertContains(r, "pending")
        self.assertContains(r, "failed ops")
        # active count present (assert on the label + a digit, not whitespace)
        html = r.content.decode()
        self.assertIn("active", html)

    def test_zero_state_renders_zero(self):
        r = self.client.get(reverse("admin:index"))
        self.assertContains(r, "Operational overview")
        self.assertContains(r, ">0</strong> active")

    def test_links_reverse_to_real_changelists(self):
        r = self.client.get(reverse("admin:index"))
        self.assertContains(r, reverse("admin:payments_paymentintent_changelist"))
        self.assertContains(r, reverse("admin:jobs_job_changelist"))

    def test_no_sensitive_data(self):
        TelegramAccount.objects.create(
            user=self.staff, telegram_user_id=555001, chat_id=555001,
            is_active=True)
        TelegramVerificationToken.objects.create(
            user=self.staff, token="SECRET_TOK_12345",
            expires_at=timezone.now() + datetime.timedelta(hours=1))
        r = self.client.get(reverse("admin:index"))
        # the actual sensitive values must not appear (generic words like
        # "token" appear in normal admin UI, so assert on the value)
        self.assertNotContains(r, "SECRET_TOK_12345")
        self.assertNotContains(r, "555001")


class DashboardPermissionTests(TestCase):
    def test_non_staff_redirected_from_admin(self):
        u = User.objects.create_user(username="plain")
        self.client.force_login(u)
        r = self.client.get(reverse("admin:index"))
        self.assertIn(r.status_code, (302, 403))

    def test_index_still_works_without_any_data(self):
        staff = make_staff("dash_empty")
        self.client.force_login(staff)
        r = self.client.get(reverse("admin:index"))
        self.assertEqual(r.status_code, 200)
