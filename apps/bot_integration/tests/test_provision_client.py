"""ProvisionClient.check_membership parsing + retryable conventions."""
import json
from unittest import mock

import requests

from django.test import TestCase, override_settings

from apps.bot_integration.services.provision_client import (
    BotOffline, ProvisionClient)

BASE = "http://bridge:8010"


def _resp(status_code, payload):
    resp = mock.Mock()
    resp.status_code = status_code
    resp.json = lambda: payload
    return resp


@override_settings(PROVISION_BOT_URL=BASE, PROVISION_SHARED_SECRET="secret")
class CheckMembershipClientTests(TestCase):
    def _post_mock(self, resp=None, exc=None):
        patcher = mock.patch(
            "apps.bot_integration.services.provision_client.requests.post")
        m = patcher.start()
        self.addCleanup(patcher.stop)
        if exc is not None:
            m.side_effect = exc
        else:
            m.return_value = resp
        return m

    def test_member_true_parsed(self):
        m = self._post_mock(_resp(200, {"status": "ok", "member": True}))
        r = ProvisionClient().check_membership(123, "-1001")
        self.assertTrue(r.ok)
        self.assertIs(r.detail.get("member"), True)
        self.assertFalse(r.retryable)
        url = m.call_args[0][0]
        self.assertTrue(url.endswith("/provision/v1/check-membership"))
        body = json.loads(m.call_args[1]["data"].decode())
        self.assertEqual(body["telegram_user_id"], 123)
        self.assertEqual(body["channel_id"], "-1001")

    def test_not_member_parsed(self):
        self._post_mock(_resp(200, {"status": "ok", "member": False}))
        r = ProvisionClient().check_membership(123, "-1001")
        self.assertTrue(r.ok)
        self.assertIs(r.detail.get("member"), False)

    def test_retryable_telegram_error(self):
        self._post_mock(_resp(502, {"status": "failed",
                                    "error_code": "telegram_api_error",
                                    "retryable": True}))
        r = ProvisionClient().check_membership(123, "-1001")
        self.assertFalse(r.ok)
        self.assertTrue(r.retryable)
        self.assertEqual(r.error_code, "telegram_api_error")

    def test_non_retryable_control_channel(self):
        self._post_mock(_resp(403, {"status": "failed",
                                    "error_code": "control_channel",
                                    "retryable": False}))
        r = ProvisionClient().check_membership(123, "-1001")
        self.assertFalse(r.ok)
        self.assertFalse(r.retryable)
        self.assertEqual(r.error_code, "control_channel")

    def test_non_retryable_409(self):
        self._post_mock(_resp(409, {"status": "failed",
                                    "error_code": "validation_error",
                                    "retryable": False}))
        r = ProvisionClient().check_membership(123, "-1001")
        self.assertFalse(r.ok)
        self.assertFalse(r.retryable)

    def test_transport_error_is_retryable(self):
        self._post_mock(exc=requests.ConnectionError("boom"))
        r = ProvisionClient().check_membership(123, "-1001")
        self.assertFalse(r.ok)
        self.assertTrue(r.retryable)
        self.assertEqual(r.error_code, "transport_error")

    def test_malformed_json_is_retryable(self):
        resp = mock.Mock()
        resp.status_code = 200
        resp.json = mock.Mock(side_effect=ValueError("no json"))
        self._post_mock(resp=resp)
        r = ProvisionClient().check_membership(123, "-1001")
        self.assertFalse(r.ok)
        self.assertTrue(r.retryable)
        self.assertEqual(r.error_code, "malformed_response")

    def test_bot_offline_is_retryable(self):
        with override_settings(PROVISION_BOT_URL=""):
            r = ProvisionClient().check_membership(123, "-1001")
        self.assertFalse(r.ok)
        self.assertTrue(r.retryable)
        self.assertEqual(r.error_code, "bot_offline")

    def test_bot_offline_exception_type(self):
        with override_settings(PROVISION_BOT_URL=""):
            with self.assertRaises(BotOffline):
                ProvisionClient()._resolve()

    def test_resend_invite_posts_access_contract(self):
        m = self._post_mock(_resp(200, {"status": "applied", "detail": {}}))
        r = ProvisionClient().resend_invite(1631208186, "-1004381928255",
                                            "https://t.me/+eGqmLaWPqgM1NWQ1")
        self.assertTrue(r.ok)
        url = m.call_args[0][0]
        self.assertTrue(url.endswith("/provision/v1/access"))
        body = json.loads(m.call_args[1]["data"].decode())
        self.assertEqual(body["operation"], "resend")
        self.assertEqual(body["telegram_user_id"], 1631208186)
        self.assertEqual(body["channel_id"], "-1004381928255")
        self.assertEqual(body["invite_link"],
                         "https://t.me/+eGqmLaWPqgM1NWQ1")

    def test_resend_invite_failure_not_ok(self):
        self._post_mock(_resp(502, {"status": "failed",
                                    "error_code": "user_unreachable",
                                    "retryable": True}))
        r = ProvisionClient().resend_invite(1631208186, "-1004381928255",
                                            "https://t.me/+x")
        self.assertFalse(r.ok)
        self.assertTrue(r.retryable)
