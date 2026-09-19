"""Reconcile engine tests: diff correctness, idempotence, defect regressions.
Platform services are mocked — no network, no real Telegram/Discord."""
from unittest import mock
from django.test import TestCase

from apps.accounts.models import User
from apps.bot_integration.models import (
    TelegramAccount, DiscordAccount, PlanChannelMapping,
    UserChannelAssignment, BotAccessAudit)
from apps.bot_integration.reconcile import reconcile_user_access
from apps.subscriptions.models import Plan, Subscription


# The post_save signal enqueues a Celery task; under test there is no broker.
# Patch the signal module's task reference so reconcile runs synchronously
# in-process. This tests the reconcile logic, not the queue transport.
import apps.bot_integration.signals as _sig
from apps.bot_integration import reconcile as _reconcile_mod

def _sync_reconcile(user_id):
    _reconcile_mod.reconcile_user_access(user_id)

_sig.reconcile_user_access_task = type("T", (), {"delay": staticmethod(_sync_reconcile)})


def make_user(u):
    return User.objects.create(username=u, email=f"{u}@x.com")


class TelegramReconcileTests(TestCase):
    def setUp(self):
        self.user = make_user("tg1")
        self.plan = Plan.objects.create(name="Pro", display_order=1)
        PlanChannelMapping.objects.create(
            plan=self.plan, platform="telegram", external_id="-1001", name="Pro Chat")
        self.account = TelegramAccount.objects.create(
            user=self.user, telegram_user_id=555, chat_id=555, is_active=True)

    @mock.patch("apps.bot_integration.reconcile.TelegramBotService")
    def test_grant_sends_invite_and_records_assignment(self, svc):
        svc.create_one_time_invite_link.return_value = "https://t.me/+abc"
        svc.unban_user.return_value = (True, "")
        svc.ban_user.return_value = (True, "")
        svc.send_message.return_value = True
        Subscription.objects.create(user=self.user, plan=self.plan,
                                    status="active", is_active=True)
        reconcile_user_access(self.user.id)
        svc.send_message.assert_called_once()
        self.assertTrue(UserChannelAssignment.objects.filter(
            user=self.user, external_id="-1001", is_active=True).exists())
        self.assertEqual(BotAccessAudit.objects.filter(
            status="success", action="grant").count(), 1)

    @mock.patch("apps.bot_integration.reconcile.TelegramBotService")
    def test_second_run_is_silent_noop(self, svc):
        svc.create_one_time_invite_link.return_value = "https://t.me/+abc"
        svc.unban_user.return_value = (True, "")
        svc.ban_user.return_value = (True, "")
        svc.send_message.return_value = True
        Subscription.objects.create(user=self.user, plan=self.plan,
                                    status="active", is_active=True)
        reconcile_user_access(self.user.id)
        audits = BotAccessAudit.objects.count(); calls = svc.send_message.call_count
        reconcile_user_access(self.user.id)  # duplicate trigger — must do nothing
        self.assertEqual(BotAccessAudit.objects.count(), audits)
        self.assertEqual(svc.send_message.call_count, calls)

    @mock.patch("apps.bot_integration.reconcile.TelegramBotService")
    def test_failed_send_not_marked_active_and_retried(self, svc):
        svc.create_one_time_invite_link.return_value = "https://t.me/+abc"
        svc.unban_user.return_value = (True, "")
        svc.ban_user.return_value = (True, "")
        # send_message fails on the FIRST reconcile only; the Subscription
        # post_save signal runs that first reconcile synchronously during
        # create() (side_effect[0]=False). Production creates the assignment
        # ONLY on send success, so none must exist after the failed attempt.
        svc.send_message.side_effect = [False, True]
        Subscription.objects.create(user=self.user, plan=self.plan,
                                    status="active", is_active=True)
        self.assertFalse(UserChannelAssignment.objects.exists())
        # retry: reconcile runs again (side_effect[1]=True) -> grant succeeds
        reconcile_user_access(self.user.id)
        self.assertTrue(UserChannelAssignment.objects.filter(is_active=True).exists())

    @mock.patch("apps.bot_integration.reconcile.TelegramBotService")
    def test_plan_change_revokes_old_channel(self, svc):
        svc.create_one_time_invite_link.return_value = "https://t.me/+abc"
        svc.unban_user.return_value = (True, "")
        svc.ban_user.return_value = (True, "")
        svc.send_message.return_value = True
        sub = Subscription.objects.create(user=self.user, plan=self.plan,
                                          status="active", is_active=True)
        reconcile_user_access(self.user.id)
        plan2 = Plan.objects.create(name="Elite", tier="pro", display_order=2)
        PlanChannelMapping.objects.create(plan=plan2, platform="telegram",
                                          external_id="-1002")
        sub.plan = plan2; sub.save()
        reconcile_user_access(self.user.id)
        svc.ban_user.assert_called_once_with("-1001", 555)
        self.assertFalse(UserChannelAssignment.objects.filter(
            external_id="-1001", is_active=True).exists())

    @mock.patch("apps.bot_integration.reconcile.TelegramBotService")
    def test_lapse_bans_everything(self, svc):
        svc.create_one_time_invite_link.return_value = "https://t.me/+abc"
        svc.unban_user.return_value = (True, "")
        svc.ban_user.return_value = (True, "")
        svc.send_message.return_value = True
        sub = Subscription.objects.create(user=self.user, plan=self.plan,
                                          status="active", is_active=True)
        reconcile_user_access(self.user.id)
        sub.status = "expired"; sub.is_active = False; sub.save()
        reconcile_user_access(self.user.id)
        svc.ban_user.assert_called_once_with("-1001", 555)


class DiscordReconcileTests(TestCase):
    def setUp(self):
        self.user = make_user("dc1")
        self.plan = Plan.objects.create(name="Pro", display_order=1)
        PlanChannelMapping.objects.create(plan=self.plan, platform="discord",
                                          external_id="111")
        self.account = DiscordAccount.objects.create(
            user=self.user, discord_user_id="9", roles=[], is_active=True)

    @mock.patch("apps.bot_integration.reconcile.DiscordBotService")
    def test_lapse_removes_roles(self, svc):
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
