"""P3b: Admin lifecycle actions orchestrate services (no duplication)."""
from unittest import mock

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone
import datetime

from apps.bot_integration.models import (
    BotAccessAudit, PlanChannelMapping, TelegramAccount, UserChannelAssignment)
from apps.bot_integration.services.provision_client import ProvisionResult
from apps.jobs.models import Job
from apps.subscriptions.admin import SubscriptionAdmin
from apps.subscriptions.models import Plan, Subscription, SubscriptionHistory
from apps.subscriptions.services import extend_subscription

User = get_user_model()
rf = RequestFactory()


def make_admin():
    u = User(username=f"adm_{timezone.now().microsecond}")
    u.is_staff = u.is_superuser = True
    u.set_password("pw")
    u.save()
    return u


def req():
    r = rf.post("/admin/subscriptions/subscription/")
    r.user = make_admin()
    from django.contrib.messages.storage.fallback import FallbackStorage
    r.session = "session"
    r._messages = FallbackStorage(r)
    return r


def make_plan():
    return Plan.objects.create(name="Pro", tier="pro", display_order=1)


def make_sub(user, plan, status=Subscription.Status.ACTIVE, is_active=True,
             expires_days=30):
    return Subscription.objects.create(
        user=user, plan=plan, status=status, is_active=is_active,
        started_at=timezone.now() - datetime.timedelta(days=1),
        expires_at=timezone.now() + datetime.timedelta(days=expires_days),
        price_cents=999, price_currency="USD")


def ok():
    return ProvisionResult(ok=True, status="applied", source="fallback")


def fake_transport():
    inst = mock.Mock()
    inst.grant = mock.Mock(return_value=ok())
    inst.revoke = mock.Mock(return_value=ok())
    return mock.patch("apps.bot_integration.reconcile.ProvisionClient",
                      return_value=inst), inst


class CancelActionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="c1", password="pw")
        self.plan = make_plan()
        self.admin = SubscriptionAdmin(Subscription, admin.site)

    @mock.patch("apps.jobs.enqueue.enqueue_reconcile")
    def test_cancels_active_and_skips_others(self, _rec):
        active = make_sub(self.user, self.plan)
        canceled = make_sub(self.user, self.plan,
                            status=Subscription.Status.CANCELED, is_active=False)
        expired = make_sub(self.user, self.plan,
                           status=Subscription.Status.EXPIRED, is_active=False)
        qs = Subscription.objects.filter(pk__in=[active.pk, canceled.pk, expired.pk])
        self.admin.cancel_subscriptions(req(), qs)
        active.refresh_from_db()
        self.assertEqual(active.status, Subscription.Status.CANCELED)
        self.assertFalse(active.is_active)
        # only the active one produced history + reconcile
        self.assertEqual(SubscriptionHistory.objects.count(), 1)
        self.assertEqual(_rec.call_count, 1)

    @mock.patch("apps.jobs.enqueue.enqueue_reconcile")
    def test_cancel_writes_history_once(self, _rec):
        sub = make_sub(self.user, self.plan)
        self.admin.cancel_subscriptions(req(), Subscription.objects.filter(pk=sub.pk))
        h = SubscriptionHistory.objects.get()
        self.assertEqual(h.event_type, SubscriptionHistory.EventType.CANCELED)


class ExpireActionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="e1", password="pw")
        self.plan = make_plan()
        self.admin = SubscriptionAdmin(Subscription, admin.site)

    @mock.patch("apps.jobs.enqueue.enqueue_reconcile")
    def test_expires_due_and_skips_not_due(self, _rec):
        due = make_sub(self.user, self.plan,
                       expires_days=-1)  # already past
        future = make_sub(User.objects.create_user(username="e2"),
                          self.plan, expires_days=30)
        qs = Subscription.objects.filter(pk__in=[due.pk, future.pk])
        self.admin.expire_subscriptions(req(), qs)
        due.refresh_from_db()
        future.refresh_from_db()
        self.assertEqual(due.status, Subscription.Status.EXPIRED)
        self.assertFalse(due.is_active)
        self.assertTrue(future.is_active)  # not due -> untouched
        self.assertEqual(SubscriptionHistory.objects.count(), 1)


class ExtendActionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="x1", password="pw")
        self.plan = make_plan()
        self.admin = SubscriptionAdmin(Subscription, admin.site)

    @mock.patch("apps.jobs.enqueue.enqueue_reconcile")
    def test_extend_moves_expiry_and_keeps_snapshot(self, _rec):
        sub = make_sub(self.user, self.plan, expires_days=10)
        old_price = sub.price_cents
        old_expiry = sub.expires_at
        self.admin.extend_subscriptions(req(), Subscription.objects.filter(pk=sub.pk))
        sub.refresh_from_db()
        self.assertGreater(sub.expires_at, old_expiry)
        self.assertEqual(sub.price_cents, old_price)  # snapshot preserved
        self.assertTrue(sub.is_active)
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)
        h = SubscriptionHistory.objects.get()
        self.assertEqual(h.event_type, SubscriptionHistory.EventType.RENEWED)

    @mock.patch("apps.jobs.enqueue.enqueue_reconcile")
    def test_extend_skips_inactive(self, _rec):
        sub = make_sub(self.user, self.plan,
                       status=Subscription.Status.CANCELED, is_active=False)
        old_expiry = sub.expires_at
        self.assertFalse(extend_subscription(sub, days=30))
        sub.refresh_from_db()
        self.assertEqual(sub.expires_at, old_expiry)
        self.assertEqual(SubscriptionHistory.objects.count(), 0)


class ReconcileActionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="r1", password="pw")
        self.plan = make_plan()
        self.admin = SubscriptionAdmin(Subscription, admin.site)

    def test_enqueues_durable_job_not_sync_telegram(self):
        sub = make_sub(self.user, self.plan)
        with fake_transport()[0] as _no_sync:
            self.admin.reconcile_subscriptions(
                req(), Subscription.objects.filter(pk=sub.pk))
        self.assertEqual(Job.objects.filter(kind="reconcile").count(), 1)
        # transport was never instantiated (no synchronous grant/revoke)
        _no_sync.assert_not_called()

    def test_dedupes_multiple_subs_same_user(self):
        sub = make_sub(self.user, self.plan)
        self.admin.reconcile_subscriptions(
            req(), Subscription.objects.filter(pk=sub.pk))
        self.admin.reconcile_subscriptions(
            req(), Subscription.objects.filter(pk=sub.pk))
        # enqueue is idempotent by reconcile:{user_id} -> single job row
        self.assertEqual(Job.objects.filter(kind="reconcile").count(), 1)


class GrantNotAnActionTests(TestCase):
    """Grant is intentionally NOT a bulk admin action.

    The existing service grant_subscription_by_admin(plan, user, ...) needs a
    PLAN + USER, but an admin action only receives Subscription rows (whose
    user is already subscribed).  Exposing it as an action would either target
    already-subscribed users or silently choose an arbitrary plan -- both
    unsafe.  Grant remains available through its existing dedicated flow.
    """

    def test_grant_service_not_in_actions(self):
        self.assertNotIn("grant_subscriptions", SubscriptionAdmin.actions)


class TelegramFollowsActionTests(TestCase):
    """After a cancel/expire action, the reconcile diff revokes access."""

    def setUp(self):
        self.user = User.objects.create_user(username="t1", password="pw")
        self.plan = make_plan()
        PlanChannelMapping.objects.create(
            plan=self.plan, platform="telegram", external_id="-1001")
        TelegramAccount.objects.create(
            user=self.user, telegram_user_id=700, chat_id=700, is_active=True)
        UserChannelAssignment.objects.create(
            user=self.user, platform="telegram", external_id="-1001",
            is_active=True)
        self.admin = SubscriptionAdmin(Subscription, admin.site)

    @mock.patch("apps.jobs.enqueue.enqueue_reconcile")
    def test_cancel_then_reconcile_revokes(self, _rec):
        sub = make_sub(self.user, self.plan)
        self.admin.cancel_subscriptions(req(), Subscription.objects.filter(pk=sub.pk))
        patcher, client = fake_transport()
        from apps.bot_integration import reconcile
        with patcher:
            reconcile.reconcile_user_access(self.user.id)
        a = UserChannelAssignment.objects.get(user=self.user)
        self.assertFalse(a.is_active)
        self.assertEqual(client.revoke.call_count, 1)


class PermissionActionTests(TestCase):
    def test_action_respects_change_permission(self):
        ma = SubscriptionAdmin(Subscription, admin.site)
        anon = rf.post("/admin/subscriptions/subscription/")
        anon.user = User(username="nobody")  # not staff, no perms
        anon.user.save()
        self.assertFalse(ma.has_change_permission(anon))
