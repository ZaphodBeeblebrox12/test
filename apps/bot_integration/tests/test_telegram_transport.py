"""Stage 1 tests: reusable Telegram message transport foundation.

Mocks the HTTP boundary (requests.post) only - no real Telegram traffic.
"""

from unittest import mock

import requests
from django.test import SimpleTestCase

from apps.bot_integration.services import telegram_transport as tmod
from apps.bot_integration.services.telegram import TelegramBotService
from apps.bot_integration.services.telegram_transport import (
    ERR_API_REJECTED, ERR_CONFIG, ERR_MALFORMED_RESPONSE, ERR_MISSING_CHAT_ID,
    ERR_NETWORK, ERR_RATE_LIMITED, ERR_TIMEOUT,
    OUTCOME_ACCEPTED, OUTCOME_INDETERMINATE, OUTCOME_REJECTED,
    TelegramMessageTransport,
)

TOKEN = "test-token-abc123"


class FakeResponse:
    def __init__(self, payload=None, status=200, raise_json=False):
        self.status_code = status
        self._payload = payload
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("no json")
        return self._payload


def make_transport(**kw):
    return TelegramMessageTransport(token=kw.pop("token", TOKEN), **kw)


class SuccessTests(SimpleTestCase):
    @mock.patch.object(tmod.requests, "post")
    def test_success_exposes_message_id(self, m_post):
        m_post.return_value = FakeResponse({"ok": True, "result": {"message_id": 48123}})
        result = make_transport().send_message(chat_id=123, text="hello")
        self.assertEqual(result.outcome, OUTCOME_ACCEPTED)
        self.assertTrue(result.ok)
        self.assertEqual(result.message_id, 48123)
        self.assertTrue(result.definite)
        self.assertFalse(result.retryable)

    @mock.patch.object(tmod.requests, "post")
    def test_text_and_chat_id_passed_correctly(self, m_post):
        m_post.return_value = FakeResponse({"ok": True, "result": {"message_id": 1}})
        make_transport().send_message(chat_id=42, text="invoice paid")
        _, kwargs = m_post.call_args
        self.assertEqual(kwargs["json"], {"chat_id": 42, "text": "invoice paid"})
        self.assertEqual(kwargs["timeout"], (5, 10))


class RejectionTests(SimpleTestCase):
    @mock.patch.object(tmod.requests, "post")
    def test_definite_api_rejection(self, m_post):
        m_post.return_value = FakeResponse(
            {"ok": False, "error_code": 400, "description": "chat not found"})
        result = make_transport().send_message(chat_id=1, text="x")
        self.assertEqual(result.outcome, OUTCOME_REJECTED)
        self.assertFalse(result.ok)
        self.assertTrue(result.definite)
        self.assertFalse(result.retryable)          # definite - retry will not help
        self.assertEqual(result.error_code, ERR_API_REJECTED)
        self.assertEqual(result.telegram_error_code, 400)
        self.assertIn("chat not found", result.error_message)


class IndeterminateTests(SimpleTestCase):
    @mock.patch.object(tmod.requests, "post")
    def test_timeout_is_indeterminate(self, m_post):
        m_post.side_effect = requests.exceptions.ReadTimeout()
        result = make_transport().send_message(chat_id=1, text="x")
        self.assertEqual(result.outcome, OUTCOME_INDETERMINATE)
        self.assertEqual(result.error_code, ERR_TIMEOUT)
        self.assertFalse(result.ok)
        self.assertTrue(result.retryable)

    @mock.patch.object(tmod.requests, "post")
    def test_network_error_is_indeterminate(self, m_post):
        m_post.side_effect = requests.exceptions.ConnectionError("refused")
        result = make_transport().send_message(chat_id=1, text="x")
        self.assertEqual(result.outcome, OUTCOME_INDETERMINATE)
        self.assertEqual(result.error_code, ERR_NETWORK)

    @mock.patch.object(tmod.requests, "post")
    def test_malformed_body_is_indeterminate(self, m_post):
        m_post.return_value = FakeResponse(raise_json=True)
        result = make_transport().send_message(chat_id=1, text="x")
        self.assertEqual(result.outcome, OUTCOME_INDETERMINATE)
        self.assertEqual(result.error_code, ERR_MALFORMED_RESPONSE)

    @mock.patch.object(tmod.requests, "post")
    def test_missing_ok_field_is_indeterminate(self, m_post):
        m_post.return_value = FakeResponse({"unexpected": "shape"})
        result = make_transport().send_message(chat_id=1, text="x")
        self.assertEqual(result.outcome, OUTCOME_INDETERMINATE)
        self.assertEqual(result.error_code, ERR_MALFORMED_RESPONSE)


class GuardTests(SimpleTestCase):
    @mock.patch.object(tmod.requests, "post")
    def test_missing_chat_id_never_fabricated_no_request(self, m_post):
        for bad in (None, "", "   ", True):
            result = make_transport().send_message(chat_id=bad, text="x")
            self.assertEqual(result.error_code, ERR_MISSING_CHAT_ID, bad)
            self.assertEqual(result.outcome, OUTCOME_REJECTED)
        m_post.assert_not_called()

    @mock.patch.object(tmod.requests, "post")
    def test_missing_token_is_config_error_no_request(self, m_post):
        result = make_transport(token="").send_message(chat_id=1, text="x")
        self.assertEqual(result.error_code, ERR_CONFIG)
        self.assertEqual(result.outcome, OUTCOME_INDETERMINATE)
        m_post.assert_not_called()

    @mock.patch.object(tmod.requests, "post")
    def test_token_never_leaked_in_errors(self, m_post):
        # Exception string deliberately contains the token, as requests does.
        m_post.side_effect = requests.exceptions.ConnectionError(
            "Max retries exceeded with url: /bot" + TOKEN + "/sendMessage")
        result = make_transport().send_message(chat_id=1, text="x")
        self.assertNotIn(TOKEN, result.error_message)
        self.assertNotIn(TOKEN, str(result))
        self.assertNotIn(TOKEN, result.error_code)


class FloodRetryTests(SimpleTestCase):
    @mock.patch.object(tmod.time, "sleep")  # never really sleep in tests
    @mock.patch.object(tmod.requests, "post")
    def test_flood_429_retries_then_succeeds(self, m_post, _sleep):
        m_post.side_effect = [
            FakeResponse({"ok": False, "error_code": 429,
                          "description": "Too Many Requests",
                          "parameters": {"retry_after": 0}}),
            FakeResponse({"ok": True, "result": {"message_id": 77}}),
        ]
        result = make_transport().send_message(chat_id=1, text="x")
        self.assertTrue(result.ok)
        self.assertEqual(result.message_id, 77)
        self.assertEqual(result.attempts, 2)

    @mock.patch.object(tmod.time, "sleep")
    @mock.patch.object(tmod.requests, "post")
    def test_persistent_flood_is_rejected_but_retryable(self, m_post, _sleep):
        m_post.return_value = FakeResponse(
            {"ok": False, "error_code": 429, "description": "Too Many Requests",
             "parameters": {"retry_after": 0}})
        result = make_transport().send_message(chat_id=1, text="x")
        self.assertEqual(result.outcome, OUTCOME_REJECTED)
        self.assertEqual(result.error_code, ERR_RATE_LIMITED)
        self.assertTrue(result.retryable)
        self.assertEqual(result.attempts, 3)


class ServiceCompatibilityTests(SimpleTestCase):
    """Existing TelegramBotService.send_message(...) -> bool contract intact."""

    def _svc(self, m_post, payload=None, side_effect=None):
        m_post.return_value = payload
        m_post.side_effect = side_effect
        patcher = mock.patch.object(TelegramBotService, "_get_token", return_value=TOKEN)
        patcher.start()
        self.addCleanup(patcher.stop)
        return TelegramBotService

    @mock.patch.object(tmod.requests, "post")
    def test_send_message_bool_success(self, m_post):
        svc = self._svc(m_post, payload=FakeResponse({"ok": True, "result": {"message_id": 9}}))
        self.assertTrue(svc.send_message(123, "hi"))

    @mock.patch.object(tmod.requests, "post")
    def test_send_message_bool_definite_rejection_false(self, m_post):
        svc = self._svc(m_post, payload=FakeResponse({"ok": False, "error_code": 400,
                                                      "description": "blocked"}))
        self.assertFalse(svc.send_message(123, "hi"))

    @mock.patch.object(tmod.requests, "post")
    def test_send_message_bool_timeout_false_not_true(self, m_post):
        svc = self._svc(m_post, side_effect=requests.exceptions.Timeout())
        self.assertFalse(svc.send_message(123, "hi"))  # indeterminate is never True

    @mock.patch.object(TelegramBotService, "_get_token", return_value="")
    def test_service_config_error_returns_false(self, _tok):
        self.assertFalse(TelegramBotService.send_message(1, "x"))
        result = TelegramBotService.send_message_result(1, "x")
        self.assertEqual(result.error_code, ERR_CONFIG)

    @mock.patch.object(TelegramBotService, "_get_token", return_value=TOKEN)
    @mock.patch.object(TelegramBotService, "_api_request")
    def test_provisioning_helpers_unaffected(self, m_api, _tok):
        # invite/grant path still uses _api_request unchanged
        m_api.return_value = {"ok": True, "result": {"invite_link": "https://t.me/+x"}}
        self.assertEqual(TelegramBotService.create_one_time_invite_link(-1001),
                         "https://t.me/+x")

    def test_transport_contains_no_marketing_or_campaign_logic(self):
        # The low-level transport must be usable by transactional callers
        # without any marketing consent machinery.
        for name in dir(tmod):
            low = name.lower()
            for forbidden in ("consent", "campaign", "marketing", "suppress",
                              "subscription", "invoice", "referral"):
                self.assertNotIn(forbidden, low, "transport leaks: " + name)

    def test_transactional_caller_needs_no_consent_object(self):
        # Direct transport use = transactional path; no consent machinery.
        self.assertFalse(hasattr(TelegramMessageTransport, "check_consent"))
        self.assertFalse(hasattr(TelegramMessageTransport, "is_marketing"))
