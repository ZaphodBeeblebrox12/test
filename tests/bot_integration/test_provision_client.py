"""Django side of Provision Contract v1: client behavior + reconcile transport.

Rewritten against the CURRENT client API (ProvisionResult with
ok/retryable/error_code; X-Bot-* HMAC signing; /provision/v1/access grant &
revoke). The previous generation of this file imported exception classes and
used grant/revoke signatures that no longer exist in
apps.bot_integration.services.provision_client.
"""
import hashlib
import hmac
import json
from unittest import mock

import pytest
import requests

from apps.bot_integration.models import (
    BotAccessAudit, PlanChannelMapping, TelegramAccount, UserChannelAssignment)
from apps.bot_integration.services import provision_client
from apps.bot_integration.services.provision_client import (
    BotOffline, ProvisionClient, ProvisionResult)
from apps.subscriptions.models import Plan, Subscription

pytestmark = pytest.mark.django_db

BASE = "http://bridge:8010"
TG = 123456789
CH = "-100111222333"


def _resp(status_code, payload):
    r = mock.Mock()
    r.status_code = status_code
    r.json = lambda: payload
    return r


@pytest.fixture
def provision_settings(settings):
    settings.PROVISION_BOT_URL = BASE
    settings.PROVISION_SHARED_SECRET = "test-secret"
    return settings


def _capture_post():
    """Patch requests.post at the client module; returns (result_of_call, mock)."""
    mp = mock.patch(
        "apps.bot_integration.services.provision_client.requests.post")
    return mp


@pytest.mark.usefixtures("provision_settings")
class TestProvisionClient:
    def test_grant_payload_url_timeout_and_headers(self):
        with _capture_post() as mp:
            mp.return_value = _resp(200, {"status": "applied"})
            res = ProvisionClient().grant(TG, CH, idempotency_key="op-1")
        assert res.ok and res.status == "applied"
        url = mp.call_args[0][0]
        assert url == BASE + "/provision/v1/access"
        kwargs = mp.call_args[1]
        assert kwargs["timeout"] == 10.0  # default PROVISION_TIMEOUT
        for h in ("X-Bot-Instance", "X-Bot-Timestamp", "X-Bot-Nonce",
                  "X-Bot-Signature", "Content-Type"):
            assert h in kwargs["headers"]
        body = json.loads(kwargs["data"].decode())
        assert body["operation"] == "grant"
        assert body["telegram_user_id"] == TG
        assert body["channel_id"] == CH
        assert body["idempotency_key"] == "op-1"
        assert "request_id" in body

    def test_default_idempotency_key_format(self):
        with _capture_post() as mp:
            mp.return_value = _resp(200, {"status": "applied"})
            ProvisionClient().grant(TG, CH)
        body = json.loads(mp.call_args[1]["data"].decode())
        assert body["idempotency_key"] == f"grant:{TG}:{CH}"

    def test_revoke_payload(self):
        with _capture_post() as mp:
            mp.return_value = _resp(200, {"status": "applied"})
            ProvisionClient().revoke(TG, CH)
        body = json.loads(mp.call_args[1]["data"].decode())
        assert body["operation"] == "revoke"
        assert body["idempotency_key"] == f"revoke:{TG}:{CH}"

    def test_signature_is_hmac_sha256_over_timestamp_nonce_body(self):
        with _capture_post() as mp:
            mp.return_value = _resp(200, {"status": "applied"})
            ProvisionClient().grant(TG, CH)
        headers = mp.call_args[1]["headers"]
        body = mp.call_args[1]["data"]
        ts, nonce = headers["X-Bot-Timestamp"], headers["X-Bot-Nonce"]
        mac = hmac.new(b"test-secret", digestmod=hashlib.sha256)
        mac.update(f"{ts}.{nonce}.".encode())
        mac.update(body)
        assert hmac.compare_digest(mac.hexdigest(), headers["X-Bot-Signature"])

    def test_transport_error_is_retryable(self):
        with _capture_post() as mp:
            mp.side_effect = requests.ConnectTimeout("boom")
            res = ProvisionClient().grant(TG, CH)
        assert not res.ok and res.retryable
        assert res.error_code == "transport_error"

    def test_already_applied_is_ok(self):
        with _capture_post() as mp:
            mp.return_value = _resp(200, {"status": "already_applied"})
            res = ProvisionClient().grant(TG, CH)
        assert res.ok and res.status == "already_applied"

    def test_4xx_honors_retryable_flag(self):
        with _capture_post() as mp:
            mp.return_value = _resp(403, {"status": "failed",
                                          "error_code": "control_channel",
                                          "retryable": False})
            res = ProvisionClient().grant(TG, CH)
        assert not res.ok and not res.retryable
        assert res.error_code == "control_channel"

    def test_5xx_defaults_retryable(self):
        with _capture_post() as mp:
            mp.return_value = _resp(500, {"status": "failed",
                                          "error_code": "internal"})
            res = ProvisionClient().grant(TG, CH)
        assert not res.ok and res.retryable

    def test_malformed_response_is_retryable(self):
        bad = mock.Mock()
        bad.status_code = 200
        bad.json = mock.Mock(side_effect=ValueError("not json"))
        with _capture_post() as mp:
            mp.return_value = bad
            res = ProvisionClient().grant(TG, CH)
        assert not res.ok and res.retryable
        assert res.error_code == "malformed_response"

    def test_bot_offline_result_when_nothing_configured(self, settings):
        settings.PROVISION_BOT_URL = ""
        res = ProvisionClient().grant(TG, CH)
        assert not res.ok and res.retryable
        assert res.error_code == "bot_offline"

    def test_bot_offline_exception_public(self, settings):
        settings.PROVISION_BOT_URL = ""
        with pytest.raises(BotOffline):
            ProvisionClient()._resolve()


@pytest.fixture
def entitled_user(db, django_user_model, provision_settings):
    user = django_user_model.objects.create_user(username="member")
    plan = Plan.objects.create(name="Pro", tier="pro", display_order=1)
    PlanChannelMapping.objects.create(plan=plan, platform="telegram",
                                      external_id="-1001")
    Subscription.objects.create(user=user, plan=plan, status="active",
                                is_active=True)
    TelegramAccount.objects.create(user=user, telegram_user_id=TG,
                                   chat_id=TG, is_active=True)
    return user


def _ok():
    return ProvisionResult(ok=True, status="applied", source="fallback")


def _client_double(grant=None, revoke=None):
    instance = mock.Mock()
    instance.grant = mock.Mock(return_value=grant or _ok())
    instance.revoke = mock.Mock(return_value=revoke or _ok())
    return mock.patch("apps.bot_integration.reconcile.ProvisionClient",
                      return_value=instance), instance


@pytest.mark.usefixtures("provision_settings")
class TestReconcileProvisionTransport:
    def test_active_subscription_grants_assignment(self, entitled_user):
        patcher, client = _client_double()
        with patcher:
            provision_client.__name__  # touch import
            from apps.bot_integration import reconcile
            reconcile.reconcile_user_access(entitled_user.id)
        assert client.grant.call_count == 1
        a = UserChannelAssignment.objects.get(
            user_id=entitled_user.id, platform="telegram", external_id="-1001")
        assert a.is_active
        assert BotAccessAudit.objects.filter(
            user_id=entitled_user.id, action="grant", status="success").exists()

    def test_inactive_subscription_revokes_assignment(self, entitled_user):
        from apps.bot_integration import reconcile
        patcher, client = _client_double()
        with patcher:
            reconcile.reconcile_user_access(entitled_user.id)
        Subscription.objects.filter(user_id=entitled_user.id).update(
            is_active=False, status="canceled")
        with patcher:
            reconcile.reconcile_user_access(entitled_user.id)
        a = UserChannelAssignment.objects.get(user_id=entitled_user.id,
                                              platform="telegram")
        assert not a.is_active and a.revoked_at is not None
        assert BotAccessAudit.objects.filter(
            user_id=entitled_user.id, action="revoke", status="success").exists()

    def test_control_channel_guard_blocks_grant(self, entitled_user,
                                                settings):
        settings.PROVISION_CONTROL_CHANNEL_ID = "-1001"
        patcher, client = _client_double()
        from apps.bot_integration import reconcile
        with patcher:
            reconcile.reconcile_user_access(entitled_user.id)
        assert client.grant.call_count == 0
        assert not UserChannelAssignment.objects.filter(
            user_id=entitled_user.id).exists()
        assert BotAccessAudit.objects.filter(
            user_id=entitled_user.id, action="grant", status="failed").exists()

    def test_failed_grant_records_audit_without_assignment(self, entitled_user):
        fail = ProvisionResult(ok=False, status="failed", retryable=False,
                               error_code="user_unreachable",
                               error_message="bot blocked")
        patcher, client = _client_double(grant=fail)
        from apps.bot_integration import reconcile
        with patcher:
            reconcile.reconcile_user_access(entitled_user.id)
        assert not UserChannelAssignment.objects.filter(
            user_id=entitled_user.id).exists()
        audit = BotAccessAudit.objects.get(user_id=entitled_user.id,
                                           action="grant")
        assert audit.status == "failed" and "user_unreachable" in audit.error_message
