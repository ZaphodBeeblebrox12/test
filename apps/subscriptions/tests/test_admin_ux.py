"""P3c: Subscription detail page is a coherent investigation screen."""
import datetime
from unittest import mock

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.bot_integration.models import (
    BotAccessAudit, PlanChannelMapping, TelegramAccount, UserChannelAssignment,
    TelegramVerificationToken)
from apps.jobs.models import Job
from apps.payments.models import PaymentIntent
from apps.subscriptions.admin import (
    SubscriptionAdmin, SubscriptionHistoryInline)
from apps.subscriptions.models import (
    Plan, PlanPrice, Subscription, SubscriptionHistory)

User = get_user_model()
rf = RequestFactory()


def make_admin():
    u = User(username=f"adm_{timezone.now().microsecond}")
    u.is_staff = u.is_superuser = True
    u.set_password("pw")
    u.save()
    return u


def authed_req():
    r = rf.get("/admin/subscriptions/subscription/1/change/")
    r.user = make_admin()
    return r


def make_plan():
    return Plan.objects.create(name="Pro", tier="pro", display_order=1)


def make_sub(user, plan, **kw):
    kw.setdefault("status", Subscription.Status.ACTIVE)
    kw.setdefault("is_active", True)
    kw.setdefault("started_at", timezone.now() - datetime.timedelta(days=1))
    kw.setdefault("expires_at", timezone.now() + datetime.timedelta(days=30))
    kw.setdefault("price_cents", 999)
    kw.setdefault("price_currency", "USD")
    return Subscription.objects.create(user=user, plan=plan, **kw)


class StructureTests(TestCase):
    def setUp(self):
        self.admin = SubscriptionAdmin(Subscription, admin.site)

    def test_inlines_present(self):
        self.assertIn(SubscriptionHistoryInline, self.admin.inlines)

    def test_fieldsets_have_expected_groups(self):
        names = [f[0] for f in self.admin.fieldsets]
        for group in ("Identity", "Lifecycle", "Source", "Investigate", "Audit"):
            self.assertIn(group, names)
        pricing = [f for f in self.admin.fieldsets
                   if f[0].startswith("Pricing snapshot")]
        self.assertTrue(pricing, "pricing snapshot group missing")

    def test_p3b_actions_preserved(self):
        for action in ("cancel_subscriptions", "expire_subscriptions",
                       "extend_subscriptions", "reconcile_subscriptions"):
            self.assertIn(action, self.admin.actions)


class HistoryInlineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="h1", password="pw")
        self.plan = make_plan()
        self.sub = make_sub(self.user, self.plan)

    def test_history_readonly_no_add_no_delete(self):
        inline = SubscriptionHistoryInline(SubscriptionHistory, admin.site)
        self.assertFalse(inline.has_add_permission(authed_req(), self.sub))
        self.assertFalse(inline.can_delete)

    def test_history_capped_and_newest_first(self):
        for i in range(20):
            SubscriptionHistory.objects.create(
                subscription=self.sub, user=self.user,
                event_type=SubscriptionHistory.EventType.CREATED,
                notes=f"evt {i}")
        inline = SubscriptionHistoryInline(SubscriptionHistory, admin.site)
        qs = inline.get_queryset(authed_req())
        self.assertLessEqual(qs.count(), 15)
        # newest first
        first = qs.first()
        self.assertIn("evt 19", first.notes)


class LinkTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="l1", password="pw")
        self.plan = make_plan()
        self.sub = make_sub(self.user, self.plan)
        self.admin = SubscriptionAdmin(Subscription, admin.site)

    def test_user_link_reverses(self):
        html = self.admin.user_link(self.sub)
        self.assertIn(f"/admin/accounts/user/{self.user.pk}/change/", html)

    def test_plan_link_reverses(self):
        html = self.admin.plan_link(self.sub)
        self.assertIn(f"/admin/subscriptions/plan/{self.plan.pk}/change/", html)

    def test_payments_link_is_user_filtered_not_fake_fk(self):
        html = self.admin.payments_link(self.sub)
        self.assertIn("payments/paymentintent", html)
        self.assertIn(f"user__id__exact={self.user.pk}", html)
        self.assertNotIn(str(self.sub.pk), html)  # not linked to the sub pk

    def test_access_link_reverses(self):
        html = self.admin.access_link(self.sub)
        self.assertIn("bot_integration/userchannelassignment", html)

    def test_no_sensitive_token_exposed(self):
        TelegramVerificationToken.objects.create(
            user=self.user, token="SECRET123",
            expires_at=timezone.now() + datetime.timedelta(hours=1))
        # none of the investigation methods surface a token
        for meth in ("user_link", "plan_link", "payments_link",
                     "access_link", "jobs_link", "gift_link", "status_badge"):
            out = getattr(self.admin, meth)(self.sub)
            self.assertNotIn("SECRET123", str(out))

    def test_gift_link_absent_when_not_gift(self):
        self.assertEqual(self.admin.gift_link(self.sub), "—")


class StatusBadgeTests(TestCase):
    def test_badge_renders(self):
        user = User.objects.create_user(username="s1", password="pw")
        sub = make_sub(user, make_plan(),
                       status=Subscription.Status.EXPIRED, is_active=False)
        out = SubscriptionAdmin(Subscription, admin.site).status_badge(sub)
        self.assertIn("expired", str(out).lower())  # TextChoices value lowercases


class AccessStateTests(TestCase):
    def test_access_state_summarizes_user_assignments(self):
        from apps.bot_integration.models import UserChannelAssignment
        user = User.objects.create_user(username="as1", password="pw")
        sub = make_sub(user, make_plan())
        UserChannelAssignment.objects.create(
            user=user, platform="telegram", external_id="-1001", is_active=True)
        out = str(SubscriptionAdmin(Subscription, admin.site).access_state(sub))
        self.assertIn("telegram", out)
        self.assertIn("active", out)
        self.assertIn("-1001", out)


class QueryCountTests(TestCase):
    def test_detail_queryset_uses_select_related(self):
        ma = SubscriptionAdmin(Subscription, admin.site)
        # the changelist queryset should not N+1 user/plan
        self.assertIn("user", ma.list_select_related)
        self.assertIn("plan", ma.list_select_related)
