"""Grant success/failure DMs + bot /start fallback + connect notification."""
import json
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.bot_integration import reconcile
from apps.bot_integration.models import PlanChannelMapping, TelegramAccount
from apps.bot_integration.services.telegram import TelegramBotService
from apps.notifications.models import Notification
from apps.payments.models import PaymentIntent
from apps.payments.services import activate_paid_subscription
from apps.subscriptions.models import Plan

User = get_user_model()


class _StubClient:
    """Answers ANY method with the configured result (method-name agnostic)."""
    def __init__(self, result):
        self._result = result

    def __getattr__(self, name):
        return lambda *a, **k: self._result


class GrantNotificationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="gn1", password="x")
        self.account = TelegramAccount.objects.create(
            user=self.user, chat_id=4242, telegram_user_id=4242)
        self.plan = Plan.objects.create(name="Pro", tier="pro",
                                        is_active=True, display_order=1)
        PlanChannelMapping.objects.create(plan=self.plan, platform="telegram",
                                          external_id="-100", name="VIP Chat")

    def _grant(self, result):
        target = SimpleNamespace(telegram_ids={"-100"})
        with mock.patch.object(reconcile, "ProvisionClient",
                               return_value=_StubClient(result)), \
             mock.patch.object(TelegramBotService, "send_message") as send:
            reconcile._tg_grant(self.user.id, self.account, target, "-100")
        return send

    def test_success_sends_youre_in_dm(self):
        send = self._grant(SimpleNamespace(ok=True, error_code="",
                                           error_message=""))
        send.assert_called_once()
        self.assertIn("VIP Chat", send.call_args[0][1])
        self.assertIn("You're in", send.call_args[0][1])

    def test_failure_sends_self_heal_dm(self):
        send = self._grant(SimpleNamespace(ok=False, error_code="E_FORBIDDEN",
                                           error_message="bot not admin"))
        send.assert_called_once()
        text = send.call_args[0][1]
        self.assertIn("couldn't add you", text)
        self.assertIn("privacy settings", text)
        self.assertIn("support", text)


class BotStartFallbackTests(TestCase):
    def test_bare_start_gets_help_reply(self):
        payload = {"update_id": 1, "message": {
            "message_id": 1, "text": "/start",
            "from": {"id": 555}, "chat": {"id": 555, "type": "private"}}}
        with mock.patch("apps.bot_integration.provision_auth.verify_signed_request",
                        return_value=(json.dumps(payload).encode(), None)), \
             mock.patch.object(TelegramBotService, "send_message") as send:
            r = self.client.post(
                reverse("bot_integration:telegram_webhook"),
                data=json.dumps(payload), content_type="application/json")
        self.assertEqual(r.status_code, 200)
        send.assert_called_once()
        self.assertIn("Connect Telegram", send.call_args[0][1])


class ConnectNotificationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="cn1", password="x")
        self.plan = Plan.objects.create(name="Pro", tier="pro",
                                        is_active=True, display_order=1)
        self.plan_price = self.plan.prices.create(
            price_cents=999, currency="USD", interval="monthly", is_active=True)

    def _intent(self):
        return PaymentIntent.objects.create(
            user=self.user, plan=self.plan, plan_price=self.plan_price,
            base_amount_cents=999, amount=999, currency="USD",
            provider="stripe", status="pending", country="US",
            provider_reference="cs_1", provider_payment_id="pi_1")

    def test_unlinked_user_gets_notification_on_activation(self):
        intent = self._intent()
        with mock.patch("apps.payments.views.providers.verify_provider_payment",
                        return_value=True):
            activated, _sub = activate_paid_subscription(intent)
        self.assertTrue(activated)
        note = Notification.objects.get(user=self.user)
        self.assertEqual(note.notification_type,
                         Notification.NotificationType.TELEGRAM)
        self.assertIn("connect", note.title.lower())

    def test_linked_user_gets_no_notification(self):
        TelegramAccount.objects.create(user=self.user, chat_id=7,
                                       telegram_user_id=7)
        intent = self._intent()
        activated, _sub = activate_paid_subscription(intent)
        self.assertTrue(activated)
        self.assertEqual(Notification.objects.count(), 0)
