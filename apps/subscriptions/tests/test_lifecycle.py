"""P2: subscription lifecycle — expiry enforcement + user cancellation."""
import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from django.urls import reverse
from rest_framework.test import APIClient

from apps.bot_integration.models import (
    BotAccessAudit, PlanChannelMapping, TelegramAccount, UserChannelAssignment)
from apps.bot_integration.services.provision_client import ProvisionResult
from apps.jobs.models import Job
from apps.subscriptions.models import (
    Plan, Subscription, SubscriptionHistory)
from apps.subscriptions.services import (
    cancel_subscription, expire_due_subscriptions, expire_subscription)

User = get_user_model()
NOW = timezone.now()


def make_user(name):
    return User.objects.create_user(username=name, password="pw")


def make_plan(name="Pro", tier="pro"):
    return Plan.objects.create(name=name, tier=tier, display_order=1)


def make_sub(user, plan, days=30, status=Subscription.Status.ACTIVE,
             is_active=True, expires_at=None):
    return Subscription.objects.create(
        user=user, plan=plan, status=status, is_active=is_active,
        started_at=NOW - datetime.timedelta(days=days),
        expires_at=expires_at if expires_at is not None
        else NOW + datetime.timedelta(days=days),
        price_cents=999, price_currency="USD")


def ok():
    return ProvisionResult(ok=True, status="applied", source="fallback")


def patched_transport():
    """Patch the ProvisionClient used by reconcile; returns (patcher, client)."""
    instance = mock.Mock()
    instance.grant = mock.Mock(return_value=ok())
    instance.revoke = mock.Mock(return_value=ok())
    return (mock.patch("apps.bot_integration.reconcile.ProvisionClient",
                       return_value=instance), instance)


@mock.patch("apps.jobs.enqueue.enqueue_reconcile")
class ExpiryTests(TestCase):
    def setUp(self):
        self.user = make_user("exp1")
        self.plan = make_plan()
        PlanChannelMapping.objects.create(
            plan=self.plan, platform="telegram", external_id="-1001")
        TelegramAccount.objects.create(
            user=self.user, telegram_user_id=555, chat_id=555,
            is_active=True)

    def test_unexpired_subscription_remains_active(self, _enqueue):
        sub = make_sub(self.user, self.plan, days=30)  # expires in future
        self.assertFalse(expire_subscription(sub))
        sub.refresh_from_db()
        self.assertTrue(sub.is_active)
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)
        self.assertEqual(SubscriptionHistory.objects.count(), 0)

    def test_expired_subscription_becomes_expired_inactive(self, enqueue):
        sub = make_sub(self.user, self.plan,
                       expires_at=NOW - datetime.timedelta(minutes=1))
        self.assertTrue(expire_subscription(sub))
        sub.refresh_from_db()
        self.assertFalse(sub.is_active)
        self.assertEqual(sub.status, Subscription.Status.EXPIRED)
        self.assertIsNotNone(sub.expires_at)  # preserved
        self.assertEqual(enqueue.call_count, 1)
        self.assertEqual(enqueue.call_args[0][0], self.user.id)

    def test_expiry_writes_history_once(self, _enqueue):
        sub = make_sub(self.user, self.plan,
                       expires_at=NOW - datetime.timedelta(minutes=1))
        expire_subscription(sub)
        self.assertEqual(SubscriptionHistory.objects.count(), 1)
        h = SubscriptionHistory.objects.get()
        self.assertEqual(h.event_type, SubscriptionHistory.EventType.EXPIRED)
        self.assertEqual(h.previous_status, Subscription.Status.ACTIVE)
        self.assertEqual(h.new_status, Subscription.Status.EXPIRED)

    def test_repeated_expiry_no_duplicate_history(self, enqueue):
        sub = make_sub(self.user, self.plan,
                       expires_at=NOW - datetime.timedelta(minutes=1))
        self.assertTrue(expire_subscription(sub))
        self.assertFalse(expire_subscription(sub))
        self.assertFalse(expire_subscription(sub))
        self.assertEqual(SubscriptionHistory.objects.count(), 1)
        self.assertEqual(enqueue.call_count, 1)  # reconcile once, not 3x

    def test_sweep_does_not_expire_newer_replacement(self, _enqueue):
        # NOTE: Subscription.save() enforces ONE active subscription per
        # user, so a same-user "replacement" can't coexist with the old sub.
        # The sweep is global, so verify per-row protection across two users:
        # one past-due (must expire), one future-dated (must NOT be touched).
        other_user = make_user("exp2")
        past_due = make_sub(self.user, self.plan,
                            expires_at=NOW - datetime.timedelta(days=1))
        future = make_sub(other_user, self.plan, days=30)
        n = expire_due_subscriptions()
        self.assertEqual(n, 1)  # only the past-due one
        past_due.refresh_from_db()
        future.refresh_from_db()
        self.assertEqual(past_due.status, Subscription.Status.EXPIRED)
        self.assertTrue(future.is_active)
        self.assertEqual(future.status, Subscription.Status.ACTIVE)

    def test_sweep_idempotent_across_runs(self, _enqueue):
        # Two different users: the model enforces a single ACTIVE sub per
        # user (saving a second active sub cancels the first), so two due
        # subs must belong to two users.
        other_user = make_user("exp3")
        make_sub(self.user, self.plan,
                 expires_at=NOW - datetime.timedelta(hours=2))
        make_sub(other_user, self.plan,
                 expires_at=NOW - datetime.timedelta(hours=1))
        self.assertEqual(expire_due_subscriptions(), 2)
        self.assertEqual(expire_due_subscriptions(), 0)  # nothing left to claim
        self.assertEqual(SubscriptionHistory.objects.count(), 2)

    def test_due_filter_excludes_non_expired_and_inactive(self, _enqueue):
        other_user = make_user("exp4")
        future = make_sub(self.user, self.plan, days=10)
        already = make_sub(other_user, self.plan,
                           expires_at=NOW - datetime.timedelta(hours=1))
        Subscription.objects.filter(pk=already.pk).update(
            status=Subscription.Status.EXPIRED, is_active=False)
        self.assertEqual(expire_due_subscriptions(), 0)
        future.refresh_from_db()
        already.refresh_from_db()
        self.assertTrue(future.is_active)
        self.assertEqual(already.status, Subscription.Status.EXPIRED)

    def setUp(self):
        self.user = make_user("can1")
        self.plan = make_plan()
        PlanChannelMapping.objects.create(
            plan=self.plan, platform="telegram", external_id="-1001")
        TelegramAccount.objects.create(
            user=self.user, telegram_user_id=556, chat_id=556,
            is_active=True)

    def test_active_subscription_can_be_canceled(self, enqueue):
        sub = make_sub(self.user, self.plan)
        self.assertTrue(cancel_subscription(sub))
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.CANCELED)
        self.assertFalse(sub.is_active)
        self.assertIsNotNone(sub.canceled_at)
        h = SubscriptionHistory.objects.get()
        self.assertEqual(h.event_type, SubscriptionHistory.EventType.CANCELED)
        self.assertEqual(h.previous_status, Subscription.Status.ACTIVE)
        self.assertEqual(h.new_status, Subscription.Status.CANCELED)
        self.assertEqual(enqueue.call_count, 1)

    def test_cancellation_is_idempotent(self, enqueue):
        sub = make_sub(self.user, self.plan)
        self.assertTrue(cancel_subscription(sub))
        self.assertFalse(cancel_subscription(sub))  # second call: no-op
        self.assertEqual(SubscriptionHistory.objects.count(), 1)
        self.assertEqual(enqueue.call_count, 1)

    def test_cancel_already_expired_is_noop(self, _enqueue):
        sub = make_sub(self.user, self.plan,
                       status=Subscription.Status.EXPIRED, is_active=False)
        self.assertFalse(cancel_subscription(sub))
        self.assertEqual(SubscriptionHistory.objects.count(), 0)

    def test_cancel_trial_sub_works(self, _enqueue):
        plan = make_plan("Trial", "trial-x")
        sub = make_sub(self.user, plan)
        self.assertTrue(cancel_subscription(sub))
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.CANCELED)


@mock.patch("apps.jobs.enqueue.enqueue_reconcile")
class TelegramFollowsEntitlementTests(TestCase):
    """After expiry/cancel, the reconcile diff must revoke the assignment."""

    def setUp(self):
        self.user = make_user("tg1")
        self.plan = make_plan()
        PlanChannelMapping.objects.create(
            plan=self.plan, platform="telegram", external_id="-1001")
        TelegramAccount.objects.create(
            user=self.user, telegram_user_id=557, chat_id=557,
            is_active=True)
        UserChannelAssignment.objects.create(
            user=self.user, platform="telegram", external_id="-1001",
            is_active=True)

    def test_reconcile_revokes_after_expiry(self, _enqueue):
        sub = make_sub(self.user, self.plan,
                       expires_at=NOW - datetime.timedelta(minutes=1))
        expire_subscription(sub)
        patcher, client = patched_transport()
        from apps.bot_integration import reconcile
        with patcher:
            reconcile.reconcile_user_access(self.user.id)
        a = UserChannelAssignment.objects.get(user=self.user)
        self.assertFalse(a.is_active)
        self.assertIsNotNone(a.revoked_at)
        self.assertEqual(client.revoke.call_count, 1)

    def test_reconcile_revokes_after_cancel(self, _enqueue):
        sub = make_sub(self.user, self.plan)
        cancel_subscription(sub)
        patcher, client = patched_transport()
        from apps.bot_integration import reconcile
        with patcher:
            reconcile.reconcile_user_access(self.user.id)
        a = UserChannelAssignment.objects.get(user=self.user)
        self.assertFalse(a.is_active)
        self.assertEqual(client.revoke.call_count, 1)


class CancelEndpointTests(TestCase):
    def setUp(self):
        self.user = make_user("ep1")
        self.plan = make_plan()
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = reverse("subscriptions:cancel-subscription")

    def test_endpoint_cancels_active(self):
        make_sub(self.user, self.plan)
        r = self.client.post(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], "canceled")

    def test_endpoint_idempotent(self):
        make_sub(self.user, self.plan)
        r1 = self.client.post(self.url)
        r2 = self.client.post(self.url)
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.data.get("already_processed"))

    def test_endpoint_404_without_active(self):
        r = self.client.post(self.url)
        self.assertEqual(r.status_code, 404)

    def test_endpoint_requires_auth(self):
        anon = APIClient()
        r = anon.post(self.url)
        self.assertIn(r.status_code, (401, 403))


class ExpiryJobTests(TestCase):
    def test_periodic_scheduled(self):
        from apps.jobs.handlers import register_builtin_handlers, schedule_periodic
        register_builtin_handlers()
        schedule_periodic()
        job = Job.objects.get(kind="subscription_expiry")
        self.assertIn(job.status, (Job.STATUS_PENDING, Job.STATUS_RUNNING))

    def test_handler_runs_sweep(self):
        from apps.jobs import handlers
        handlers.register_builtin_handlers()
        user = make_user("job1")
        plan = make_plan()
        make_sub(user, plan, expires_at=NOW - datetime.timedelta(minutes=5))
        job = Job.objects.create(kind="subscription_expiry",
                                 payload={}, status=Job.STATUS_RUNNING,
                                 attempts=1, max_attempts=3)
        result = handlers.HANDLERS["subscription_expiry"]({}, job)
        self.assertEqual(result["expired"], 1)
        sub = Subscription.objects.get(user=user)
        self.assertFalse(sub.is_active)
        self.assertEqual(sub.status, Subscription.Status.EXPIRED)
