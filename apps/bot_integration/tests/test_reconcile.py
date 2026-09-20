"""Reconcile bridge tests (Django-native job system).

The reconcile body is run_user_reconcile (apps.bot_integration.reconcile_jobs):
diff -> ProvisioningOperation state machine -> UserChannelAssignment finalization.
ProvisionTransport is mocked (no network, no real Telegram calls).
Discord behavior is unchanged (direct service)."""
from unittest import mock
from django.test import TestCase

from apps.accounts.models import User
from apps.bot_integration.models import (
    TelegramAccount, DiscordAccount, PlanChannelMapping,
    UserChannelAssignment, BotAccessAudit)
from apps.bot_integration.reconcile_jobs import run_user_reconcile
from apps.jobs.models import ProvisioningOperation
from apps.subscriptions.models import Plan, Subscription


def make_user(u):
    return User.objects.create(username=u, email=f"{u}@x.com")


class FakeTransport:
    """Happy-path transport: grant/revoke succeed immediately."""

    def __init__(self):
        self.grants = []    # (tg_id, channel_id)
        self.revokes = []

    def create_invite_link(self, telegram_user_id, channel_id):
        self.grants.append((telegram_user_id, channel_id))
        return "https://t.me/+fake"

    def send_invite_dm(self, telegram_user_id, invite_link):
        return True

    def is_member(self, telegram_user_id, channel_id):
        return True   # user joins immediately; grant reaches 'completed'

    def revoke(self, telegram_user_id, channel_id, idempotency_key=None):
        self.revokes.append((telegram_user_id, channel_id))
        return mock.Mock(ok=True, retryable=False)


def run(uid, transport):
    run_user_reconcile(uid, transport=transport)


class TelegramReconcileBridgeTests(TestCase):
    """Each (user, channel, intent) gets ONE open ProvisioningOperation;
    a new lifecycle after completion gets a NEW operation row."""

    def setUp(self):
        self.user = make_user("tg1")
        self.plan = Plan.objects.create(name="Pro", display_order=1)
        PlanChannelMapping.objects.create(
            plan=self.plan, platform="telegram", external_id="-1001", name="Pro Chat")
        self.account = TelegramAccount.objects.create(
            user=self.user, telegram_user_id=555, chat_id=555, is_active=True)

    def test_grant_creates_operation_and_assignment(self):
        Subscription.objects.create(user=self.user, plan=self.plan,
                                    status="active", is_active=True)
        t = FakeTransport()
        run(self.user.id, t)
        self.assertIn((555, "-1001"), t.grants)
        op = ProvisioningOperation.objects.get(user_id=self.user.id,
                                               channel_id="-1001",
                                               operation="grant")
        self.assertEqual(op.state, ProvisioningOperation.ST_COMPLETED)
        self.assertEqual(op.invite_link, "https://t.me/+fake")
        self.assertTrue(UserChannelAssignment.objects.filter(
            user=self.user, external_id="-1001", is_active=True).exists())
        self.assertEqual(BotAccessAudit.objects.filter(
            status="success", action="grant").count(), 1)

    def test_second_run_is_noop_no_new_operation(self):
        Subscription.objects.create(user=self.user, plan=self.plan,
                                    status="active", is_active=True)
        t = FakeTransport()
        run(self.user.id, t)
        audits = BotAccessAudit.objects.count()
        ops = ProvisioningOperation.objects.count()
        run(self.user.id, t)   # duplicate trigger: no new operation, no new grant
        self.assertEqual(ProvisioningOperation.objects.count(), ops)
        self.assertEqual(len(t.grants), 1)
        # finalize steps are idempotent (assignment get_or_create; audit may repeat
        # only when a NEW operation runs -- none did here)
        self.assertEqual(BotAccessAudit.objects.count(), audits)

    def test_plan_change_revokes_old_channel(self):
        Subscription.objects.create(user=self.user, plan=self.plan,
                                    status="active", is_active=True)
        t = FakeTransport()
        run(self.user.id, t)
        plan2 = Plan.objects.create(name="Elite", tier="pro", display_order=2)
        PlanChannelMapping.objects.create(plan=plan2, platform="telegram",
                                          external_id="-1002")
        sub = Subscription.objects.get(user=self.user)
        sub.plan = plan2
        sub.save()
        run(self.user.id, t)
        self.assertIn((555, "-1001"), t.revokes)
        self.assertFalse(UserChannelAssignment.objects.filter(
            external_id="-1001", is_active=True).exists())

    def test_lapse_revokes_everything(self):
        Subscription.objects.create(user=self.user, plan=self.plan,
                                    status="active", is_active=True)
        t = FakeTransport()
        run(self.user.id, t)
        sub = Subscription.objects.get(user=self.user)
        sub.status = "expired"
        sub.is_active = False
        sub.save()
        run(self.user.id, t)
        self.assertIn((555, "-1001"), t.revokes)
        self.assertFalse(UserChannelAssignment.objects.filter(
            external_id="-1001", is_active=True).exists())

    def test_grant_revoke_grant_new_lifecycle_new_operation(self):
        sub = Subscription.objects.create(user=self.user, plan=self.plan,
                                          status="active", is_active=True)
        t = FakeTransport()
        run(self.user.id, t)
        first_op = ProvisioningOperation.objects.get(operation="grant")
        # revoke
        sub.status = "expired"; sub.is_active = False; sub.save()
        run(self.user.id, t)
        # re-grant
        sub.status = "active"; sub.is_active = True; sub.save()
        run(self.user.id, t)
        grants = ProvisioningOperation.objects.filter(operation="grant")
        self.assertEqual(grants.count(), 2)
        self.assertNotEqual(grants.order_by("id")[0].operation_id,
                            grants.order_by("id")[1].operation_id)
        self.assertTrue(UserChannelAssignment.objects.filter(
            external_id="-1001", is_active=True).exists())


class DiscordReconcileTests(TestCase):
    """Discord transport unchanged — direct service calls."""

    def setUp(self):
        self.user = make_user("dc1")
        self.plan = Plan.objects.create(name="Pro", display_order=1)
        PlanChannelMapping.objects.create(plan=self.plan, platform="discord",
                                          external_id="111")
        self.account = DiscordAccount.objects.create(
            user=self.user, discord_user_id="9", roles=[], is_active=True)

    @mock.patch("apps.bot_integration.reconcile.DiscordBotService")
    def test_lapse_removes_roles(self, svc):
        from apps.bot_integration.reconcile import reconcile_user_access
        svc.add_role.return_value = True
        svc.remove_role.return_value = True
        sub = Subscription.objects.create(user=self.user, plan=self.plan,
                                          status="active", is_active=True)
        reconcile_user_access(self.user.id)
        self.account.refresh_from_db()
        self.assertEqual(self.account.roles, ["111"])
        sub.status = "expired"; sub.is_active = False; sub.save()
        reconcile_user_access(self.user.id)
        svc.remove_role.assert_called_once_with("9", "111")
        self.account.refresh_from_db()
        self.assertEqual(self.account.roles, [])
