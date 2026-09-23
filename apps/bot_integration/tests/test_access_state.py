"""Access-state machine tests (pay -> access flow)."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.bot_integration.models import (
    BotAccessAudit, PlanChannelMapping, TelegramAccount, UserChannelAssignment)
from apps.bot_integration.services.channel_sync import get_telegram_access_state
from apps.subscriptions.models import Plan, Subscription

User = get_user_model()


class AccessStateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="as1", password="x")
        self.plan = Plan.objects.create(name="Pro", tier="pro",
                                        is_active=True, display_order=1)

    def _sub(self):
        return Subscription.objects.create(
            user=self.user, plan=self.plan, status=Subscription.Status.ACTIVE,
            is_active=True, price_cents=999, price_currency="USD")

    def test_no_subscription(self):
        self.assertEqual(get_telegram_access_state(self.user)["state"],
                         "no_subscription")

    def test_not_linked(self):
        self._sub()
        st = get_telegram_access_state(self.user)
        self.assertEqual(st["state"], "not_linked")
        self.assertIn("connect", st["cta_url"])

    def test_ready_when_no_channels_mapped(self):
        self._sub()
        TelegramAccount.objects.create(user=self.user, chat_id=1,
                                       telegram_user_id=1)
        self.assertEqual(get_telegram_access_state(self.user)["state"], "ready")

    def test_pending_until_granted(self):
        self._sub()
        TelegramAccount.objects.create(user=self.user, chat_id=1,
                                       telegram_user_id=1)
        PlanChannelMapping.objects.create(plan=self.plan, platform="telegram",
                                          external_id="-100", name="VIP")
        self.assertEqual(get_telegram_access_state(self.user)["state"], "pending")

    def test_ready_when_granted(self):
        self._sub()
        TelegramAccount.objects.create(user=self.user, chat_id=1,
                                       telegram_user_id=1)
        PlanChannelMapping.objects.create(plan=self.plan, platform="telegram",
                                          external_id="-100", name="VIP")
        UserChannelAssignment.objects.create(user=self.user, platform="telegram",
                                             external_id="-100")
        self.assertEqual(get_telegram_access_state(self.user)["state"], "ready")

    def test_needs_action_after_failed_grant(self):
        self._sub()
        TelegramAccount.objects.create(user=self.user, chat_id=1,
                                       telegram_user_id=1)
        PlanChannelMapping.objects.create(plan=self.plan, platform="telegram",
                                          external_id="-100", name="VIP")
        BotAccessAudit.objects.create(
            user=self.user, action="grant", platform="telegram",
            target="-100", status="failed", error_message="x",
            created_at=timezone.now())
        st = get_telegram_access_state(self.user)
        self.assertEqual(st["state"], "needs_action")
        self.assertEqual(st["cta_url"], "/support/")

    def test_old_failure_does_not_block(self):
        self._sub()
        TelegramAccount.objects.create(user=self.user, chat_id=1,
                                       telegram_user_id=1)
        PlanChannelMapping.objects.create(plan=self.plan, platform="telegram",
                                          external_id="-100", name="VIP")
        audit = BotAccessAudit.objects.create(
            user=self.user, action="grant", platform="telegram",
            target="-100", status="failed", error_message="x")
        # auto_now_add stamps creation time; backdate via update().
        BotAccessAudit.objects.filter(pk=audit.pk).update(
            created_at=timezone.now() - timezone.timedelta(hours=25))
        self.assertEqual(get_telegram_access_state(self.user)["state"], "pending")
