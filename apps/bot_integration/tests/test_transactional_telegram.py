"""Stage 2 tests: transactional Telegram layer. No real Telegram traffic."""

from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from apps.bot_integration import transactional as tmod
from apps.bot_integration.services.telegram_transport import (
    OUTCOME_ACCEPTED, OUTCOME_INDETERMINATE, TelegramMessageResult)
from apps.bot_integration.transactional import (
    TransactionalTelegramService, render_subscription_activated, render_welcome)
from apps.subscriptions.models import Subscription


def accepted_result(message_id=48123):
    return TelegramMessageResult(outcome=OUTCOME_ACCEPTED, message_id=message_id)


def user_with_account(chat_id=12345, active=True):
    user = mock.Mock(username="alice")
    user.telegram_account = mock.Mock(chat_id=chat_id, is_active=active)
    return user


class ServiceTests(SimpleTestCase):
    @mock.patch.object(tmod.TelegramBotService, "send_message_result",
                       return_value=accepted_result())
    def test_welcome_sends_and_exposes_message_id(self, m_send):
        result = TransactionalTelegramService.send_welcome(user_with_account())
        self.assertTrue(result.sent)
        self.assertEqual(result.message_id, 48123)
        self.assertEqual(m_send.call_args[0][0], 12345)
        self.assertIn("Welcome, alice", m_send.call_args[0][1])

    @mock.patch.object(tmod.TelegramBotService, "send_message_result")
    def test_no_account_skipped_no_transport_call(self, m_send):
        user = mock.Mock(username="bob", spec=["username"])  # no telegram_account
        result = TransactionalTelegramService.send_welcome(user)
        self.assertTrue(result.skipped)
        self.assertIn("no linked telegram account", result.skip_reason)
        m_send.assert_not_called()

    @mock.patch.object(tmod.TelegramBotService, "send_message_result")
    def test_inactive_account_skipped(self, m_send):
        result = TransactionalTelegramService.send_welcome(user_with_account(active=False))
        self.assertTrue(result.skipped)
        self.assertIn("inactive", result.skip_reason)
        m_send.assert_not_called()

    @mock.patch.object(tmod.TelegramBotService, "send_message_result",
                       return_value=TelegramMessageResult(
                           outcome=OUTCOME_INDETERMINATE, error_code="timeout",
                           error_message="Request to Telegram API timed out"))
    def test_indeterminate_never_reported_as_sent(self, _m):
        result = TransactionalTelegramService.send_welcome(user_with_account())
        self.assertFalse(result.sent)
        self.assertEqual(result.outcome, OUTCOME_INDETERMINATE)

    @mock.patch.object(tmod.TelegramBotService, "send_message_result",
                       side_effect=RuntimeError("boom"))
    def test_send_never_raises_even_if_transport_raises(self, _m):
        result = TransactionalTelegramService.send_welcome(user_with_account())
        self.assertFalse(result.sent)
        self.assertEqual(result.error_code, "unexpected_error")

    @override_settings(TELEGRAM_TRANSACTIONAL_ENABLED=False)
    @mock.patch.object(tmod.TelegramBotService, "send_message_result")
    def test_disabled_setting_skips_without_transport_call(self, m_send):
        result = TransactionalTelegramService.send_welcome(user_with_account())
        self.assertTrue(result.skipped)
        self.assertIn("disabled", result.skip_reason)
        m_send.assert_not_called()

    def test_templates_render_key_content(self):
        self.assertIn("alice", render_welcome("alice"))
        rendered = render_subscription_activated("alice", "Pro")
        self.assertIn("Pro", rendered)
        self.assertIn("activated", rendered)

    def test_no_marketing_or_campaign_logic_in_module(self):
        for name in dir(tmod):
            low = name.lower()
            for forbidden in ("consent", "campaign", "marketing", "suppress",
                              "unsubscribe"):
                self.assertNotIn(forbidden, low, "transactional layer leaks: " + name)


class SignalEdgeTests(TestCase):
    """Receivers invoked directly with mock instances — no Plan/User rows,
    no HTTP, deterministic edge control via _old_status."""

    @mock.patch("apps.bot_integration.transactional."
                "TransactionalTelegramService.send_subscription_activated")
    def test_activation_edge_sends(self, m_send):
        from apps.bot_integration.signals import transactional_message_on_transition
        sub = mock.Mock(status=Subscription.Status.ACTIVE, is_active=True,
                        _old_status=None)
        sub.plan.display_name = "Pro"
        with self.captureOnCommitCallbacks(execute=True):
            transactional_message_on_transition(Subscription, sub, created=True)
        m_send.assert_called_once_with(sub.user, plan_name="Pro")

    @mock.patch("apps.bot_integration.transactional."
                "TransactionalTelegramService.send_subscription_expired")
    def test_expired_edge_sends(self, m_send):
        from apps.bot_integration.signals import transactional_message_on_transition
        sub = mock.Mock(status=Subscription.Status.EXPIRED, is_active=False,
                        _old_status=Subscription.Status.ACTIVE)
        sub.plan.display_name = "Pro"
        with self.captureOnCommitCallbacks(execute=True):
            transactional_message_on_transition(Subscription, sub, created=False)
        m_send.assert_called_once_with(sub.user, plan_name="Pro")

    @mock.patch("apps.bot_integration.transactional."
                "TransactionalTelegramService.send_subscription_activated")
    @mock.patch("apps.bot_integration.transactional."
                "TransactionalTelegramService.send_subscription_expired")
    def test_no_edge_no_message(self, m_exp, m_act):
        from apps.bot_integration.signals import transactional_message_on_transition
        sub = mock.Mock(status=Subscription.Status.ACTIVE, is_active=True,
                        _old_status=Subscription.Status.ACTIVE)
        with self.captureOnCommitCallbacks(execute=True):
            transactional_message_on_transition(Subscription, sub, created=False)
        m_act.assert_not_called()
        m_exp.assert_not_called()

    @mock.patch("apps.bot_integration.transactional."
                "TransactionalTelegramService.send_welcome")
    def test_welcome_on_account_link(self, m_send):
        from apps.bot_integration.signals import transactional_welcome_on_link
        from apps.bot_integration.models import TelegramAccount
        account = mock.Mock(user=mock.Mock(username="alice"))
        with self.captureOnCommitCallbacks(execute=True):
            transactional_welcome_on_link(TelegramAccount, account, created=True)
        m_send.assert_called_once_with(account.user)

    @mock.patch("apps.bot_integration.transactional."
                "TransactionalTelegramService.send_welcome")
    def test_welcome_not_resent_on_updates(self, m_send):
        from apps.bot_integration.signals import transactional_welcome_on_link
        from apps.bot_integration.models import TelegramAccount
        account = mock.Mock(user=mock.Mock(username="alice"))
        transactional_welcome_on_link(TelegramAccount, account, created=False)
        m_send.assert_not_called()
