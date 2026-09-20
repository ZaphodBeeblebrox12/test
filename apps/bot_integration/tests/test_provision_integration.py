"""Django-side tests for the Provision v1 integration layer."""

import hashlib
import hmac
import json
import time
import uuid
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from apps.bot_integration.models import PlanChannelMapping, TelegramAccount
from apps.bot_integration.provision_auth import sign_payload
from apps.bot_integration.runtime_models import BotRuntimeState
from apps.bot_integration.services import provision_client as pc_module
from apps.bot_integration.services.provision_client import (
    BotOffline, ProvisionClient, ProvisionResult,
)

SECRET = "test-secret"
HEADERS = ("X-Bot-Instance", "X-Bot-Timestamp", "X-Bot-Nonce", "X-Bot-Signature")


def signed_headers(body: bytes, secret=SECRET, ts=None, nonce=None, instance="bot-1"):
    ts = ts or str(int(time.time()))
    nonce = nonce or uuid.uuid4().hex
    return {
        "X-Bot-Instance": instance,
        "X-Bot-Timestamp": ts,
        "X-Bot-Nonce": nonce,
        "X-Bot-Signature": sign_payload(secret, ts, nonce, body),
    }


@override_settings(PROVISION_SHARED_SECRET=SECRET, PROVISION_BOT_URL="")
class RegistrationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()

    def _post(self, url, payload, headers=None, **kw):
        body = json.dumps(payload).encode()
        hdrs = headers if headers is not None else signed_headers(body)
        return self.client.post(url, data=body, content_type="application/json",
                                headers=hdrs, **kw)

    def test_register_happy_path(self):
        r = self._post("/bot/api/register/", {
            "instance_id": "i1", "base_url": "http://127.0.0.1:8010",
            "contract": "provision", "version": 1,
            "operations": ["grant", "revoke"],
            "bot": {"id": 123, "username": "sig_bot"}})
        self.assertEqual(r.status_code, 200)
        state = BotRuntimeState.objects.get(instance_id="i1")
        self.assertEqual(state.base_url, "http://127.0.0.1:8010")
        self.assertEqual(state.status, "online")

    def test_register_rejects_bad_signature(self):
        body = json.dumps({"instance_id": "i2", "base_url": "http://127.0.0.1:8010"}).encode()
        hdrs = signed_headers(body, secret="wrong")
        r = self.client.post("/bot/api/register/", data=body,
                             content_type="application/json", **hdrs)
        self.assertEqual(r.status_code, 401)

    def test_register_rejects_nonce_replay(self):
        payload = {"instance_id": "i3", "base_url": "http://127.0.0.1:8010",
                   "contract": "provision", "version": 1, "operations": ["grant", "revoke"]}
        body = json.dumps(payload).encode()

        def hdrs():
            return signed_headers(body, nonce="replay-me")
        self.assertEqual(self.client.post("/bot/api/register/", data=body,
                           content_type="application/json", headers=hdrs()).status_code, 200)
        self.assertEqual(self.client.post("/bot/api/register/", data=body,
                           content_type="application/json", headers=hdrs()).status_code, 401)

    def test_register_rejects_stale_timestamp(self):
        body = json.dumps({"instance_id": "i4", "base_url": "http://127.0.0.1:8010"}).encode()
        hdrs = signed_headers(body, ts=str(int(time.time()) - 1000))
        r = self.client.post("/bot/api/register/", data=body,
                             content_type="application/json", **hdrs)
        self.assertEqual(r.status_code, 401)

    def test_register_rejects_bad_scheme(self):
        r = self._post("/bot/api/register/", {
            "instance_id": "i5", "base_url": "file:///etc/passwd",
            "contract": "provision", "version": 1, "operations": ["grant"]})
        self.assertEqual(r.status_code, 400)

    def test_register_marks_incompatible(self):
        r = self._post("/bot/api/register/", {
            "instance_id": "i6", "base_url": "http://127.0.0.1:8010",
            "contract": "legacy", "version": 0, "operations": []})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(BotRuntimeState.objects.get(instance_id="i6").status, "incompatible")

    def test_heartbeat_updates_last_seen_and_unknown_404(self):
        self._post("/bot/api/register/", {
            "instance_id": "i7", "base_url": "http://127.0.0.1:8010",
            "contract": "provision", "version": 1, "operations": ["grant", "revoke"]})
        # Age the record deliberately; a heartbeat must refresh last_seen,
        # otherwise freshness-based endpoint resolution discards live bots.
        from django.utils import timezone as tz
        from datetime import timedelta
        BotRuntimeState.objects.filter(instance_id="i7").update(
            last_seen=tz.now() - timedelta(hours=6))
        stale_seen = BotRuntimeState.objects.get(instance_id="i7").last_seen
        r = self._post("/bot/api/heartbeat/", {"instance_id": "i7"})
        self.assertEqual(r.status_code, 200)
        state = BotRuntimeState.objects.get(instance_id="i7")
        self.assertGreater(state.last_seen, stale_seen)   # last_seen ADVANCED
        # ...and the fresh record is now visible to endpoint resolution.
        from apps.bot_integration.services.provision_client import ProvisionClient
        base, source = ProvisionClient().resolve_endpoint()
        self.assertEqual((base, source), ("http://127.0.0.1:8010", "registered"))
        # unknown instance still 404s (unchanged behavior)
        r = self._post("/bot/api/heartbeat/", {"instance_id": "nope"})
        self.assertEqual(r.status_code, 404)


@override_settings(PROVISION_SHARED_SECRET=SECRET,
                   PROVISION_BOT_URL="http://127.0.0.1:8010")
class EndpointResolutionTests(TestCase):
    def test_registered_endpoint_preferred_over_fallback(self):
        BotRuntimeState.touch("fresh", base_url="http://127.0.0.1:8010",
                              contract="provision", contract_version=1,
                              operations=["grant", "revoke"])
        base, source = ProvisionClient().resolve_endpoint()
        self.assertEqual((base, source), ("http://127.0.0.1:8010", "registered"))

    def test_fallback_used_when_nothing_registered(self):
        BotRuntimeState.objects.all().delete()
        base, source = ProvisionClient().resolve_endpoint()
        self.assertEqual((base, source), ("http://127.0.0.1:8010", "fallback"))

    def test_offline_when_no_registration_and_no_fallback(self):
        BotRuntimeState.objects.all().delete()
        with override_settings(PROVISION_BOT_URL=""):
            with self.assertRaises(BotOffline):
                ProvisionClient()._resolve()

    def test_incompatible_registration_not_used(self):
        BotRuntimeState.touch("old", base_url="http://127.0.0.1:8010",
                              contract="legacy", contract_version=0, operations=[])
        base, source = ProvisionClient().resolve_endpoint()
        self.assertEqual(source, "fallback")


@override_settings(PROVISION_SHARED_SECRET=SECRET,
                   PROVISION_CONTROL_CHANNEL_ID="-1009999999999")

class RealTransportTests(TestCase):
    """Exercises ProvisionClient over a REAL requests transport against a
    live local HTTP server (no transport mocks).  Would fail if production
    code ever reads the nonexistent requests.Response.status again."""

    def test_grant_over_real_requests_transport(self):
        import hashlib, hmac as hmac_mod, json, threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from apps.bot_integration.runtime_models import BotRuntimeState
        from apps.bot_integration.provision_auth import sign_payload

        captured = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                captured["body"] = body
                captured["headers"] = dict(self.headers)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "applied", "detail": {"invite_link": "https://t.me/+real"}}')

            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            BotRuntimeState.touch("real-bridge", base_url=f"http://127.0.0.1:{port}",
                                  contract="provision", contract_version=1,
                                  operations=["grant", "revoke"])
            res = ProvisionClient().grant(1631208186, "-1004381928255")
            self.assertTrue(res.ok, f"{res.error_code} | {res.error_message} | src={res.source}")
            self.assertEqual(res.status, "applied")
            self.assertEqual(res.source, "registered")
            # the request was signed with the shared secret
            ts = captured["headers"]["X-Bot-Timestamp"]
            nonce = captured["headers"]["X-Bot-Nonce"]
            mac = hmac_mod.new(SECRET.encode(), digestmod=hashlib.sha256)
            mac.update(f"{ts}.{nonce}.".encode())
            mac.update(captured["body"])
            self.assertTrue(hmac_mod.compare_digest(
                mac.hexdigest(), captured["headers"]["X-Bot-Signature"]))
            payload = json.loads(captured["body"])
            self.assertEqual(payload["operation"], "grant")
        finally:
            server.shutdown()
            server.server_close()



class ProvisionClientContractTests(TestCase):
    class _Resp:
        """Faithful requests.Response stand-in: exposes ONLY .status_code,
        so any production code touching the nonexistent .status fails loudly."""
        def __init__(self, status, data):
            self.status_code = status
            self._data = data
        def json(self):
            if isinstance(self._data, (dict, list)):
                return self._data
            import json as _json
            return _json.loads(self._data.decode() if isinstance(self._data, (bytes, bytearray)) else self._data)

    def _fake_post(self, status, payload):
        def _post(url, json=None, data=None, headers=None, timeout=None):
            self.captured = {"url": url, "headers": headers, "body": data}
            return self._Resp(status, payload)
        return _post

    def test_grant_success_marks_ok(self):
        BotRuntimeState.touch("b", base_url="http://127.0.0.1:8010",
                              contract="provision", contract_version=1,
                              operations=["grant", "revoke"])
        with mock.patch("apps.bot_integration.services.provision_client.requests.post",
                        side_effect=self._fake_post(200, {"status": "applied"})):
            res = ProvisionClient().grant(123, "-1001")
        self.assertTrue(res.ok)
        self.assertEqual(res.status, "applied")
        body = json.loads(self.captured["body"])
        self.assertEqual(body["operation"], "grant")
        self.assertEqual(body["telegram_user_id"], 123)
        # request is signed
        self.assertIn("X-Bot-Signature", self.captured["headers"])

    def test_5xx_retryable(self):
        BotRuntimeState.touch("b", base_url="http://127.0.0.1:8010",
                              contract="provision", contract_version=1,
                              operations=["grant", "revoke"])
        with mock.patch("apps.bot_integration.services.provision_client.requests.post",
                        side_effect=self._fake_post(502, {"status": "failed",
                                                          "error_code": "telegram_api_error",
                                                          "retryable": True})):
            res = ProvisionClient().revoke(123, "-1001")
        self.assertFalse(res.ok)
        self.assertTrue(res.retryable)

    def test_validation_error_not_retryable(self):
        BotRuntimeState.touch("b", base_url="http://127.0.0.1:8010",
                              contract="provision", contract_version=1,
                              operations=["grant", "revoke"])
        with mock.patch("apps.bot_integration.services.provision_client.requests.post",
                        side_effect=self._fake_post(403, {"status": "failed",
                                                          "error_code": "control_channel",
                                                          "retryable": False})):
            res = ProvisionClient().grant(123, "-1009999999999")
        self.assertFalse(res.ok)
        self.assertFalse(res.retryable)
