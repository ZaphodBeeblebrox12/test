"""Django side of provision contract v1: client behavior + reconcile transport."""
import hashlib
import hmac
import json
from unittest import mock

import pytest
import requests

# Subscription activation enqueues reconcile via Celery; under test there is no
# broker. Mirror the existing project convention (see apps/bot_integration/
# tests/test_reconcile.py): run reconcile synchronously in-process.
import apps.bot_integration.signals as _sig
from apps.bot_integration import reconcile as _reconcile_mod
_sig.reconcile_user_access_task = type(
    "T", (), {"delay": staticmethod(
        lambda uid: _reconcile_mod.reconcile_user_access(uid))})

from apps.bot_integration.services import provision_client
from apps.bot_integration.services.provision_client import (
    ProvisionNonRetryableError, ProvisionRetryableError)

URL = "http://bot:8000/provision/v1/access"


@pytest.fixture
def _cfg(settings):
    settings.PROVISION_BOT_URL = "http://bot:8000"
    settings.PROVISION_SHARED_SECRET = "s3cret"


def _resp(status, payload):
    r = mock.Mock()
    r.status_code = status
    r.json = lambda: payload
    r.text = json.dumps(payload)
    return r


@pytest.mark.usefixtures("_cfg")
class TestProvisionClient:
    def _post_args(self, mp):
        (call,) = mp.call_args_list
        kwargs = call.kwargs or call[1]
        return kwargs

    def test_request_construction_and_schema(self):
        with mock.patch("requests.post") as mp:
            mp.return_value = _resp(200, {"request_id": "x", "status": "applied"})
            provision_client.grant_access(123456789, "-100111222333", user_id=7)
        kwargs = self._post_args(mp)
        assert kwargs["timeout"] == (5, 15)
        headers = kwargs["headers"]
        body = json.loads(kwargs["data"].decode())
        assert body["operation"] == "grant"
        assert body["telegram_user_id"] == 123456789
        assert body["channel_id"] == "-100111222333"
        assert body["idempotency_key"] == "7:-100111222333:grant"
        assert set(body) == {"request_id", "idempotency_key", "operation",
                             "telegram_user_id", "channel_id"}
        assert "X-Request-Id" in headers and "X-Provision-Signature" in headers

    def test_hmac_signature_correct(self):
        with mock.patch("requests.post") as mp:
            mp.return_value = _resp(200, {"request_id": "x", "status": "applied"})
            provision_client.revoke_access(123, "-100999888777")
        body = json.loads(self._post_args(mp)["data"].decode())
        rid = body["request_id"]
        expected = hmac.new(b"s3cret", rid.encode() + kwargs_data(mp),
                            hashlib.sha256).hexdigest()
        assert self._post_args(mp)["headers"]["X-Provision-Signature"] == expected

    def test_timeout_is_retryable(self):
        with mock.patch("requests.post",
                        side_effect=requests.ConnectTimeout("boom")):
            with pytest.raises(ProvisionRetryableError):
                provision_client.grant_access(123, "-100111222333")

    def test_200_applied_and_already_applied(self):
        with mock.patch("requests.post") as mp:
            mp.return_value = _resp(200, {"status": "applied"})
            assert provision_client.grant_access(1, "-1001") == "applied"
            mp.return_value = _resp(200, {"status": "already_applied"})
            assert provision_client.revoke_access(1, "-1001") == "already_applied"

    def test_retryable_error(self):
        with mock.patch("requests.post") as mp:
            mp.return_value = _resp(503, {"status": "failed",
                                          "error_code": "rate_limited",
                                          "retryable": True})
            with pytest.raises(ProvisionRetryableError) as ei:
                provision_client.grant_access(1, "-1001")
            assert ei.value.error_code == "rate_limited"

    def test_non_retryable_error(self):
        with mock.patch("requests.post") as mp:
            mp.return_value = _resp(422, {"status": "failed",
                                          "error_code": "user_blocked",
                                          "retryable": False})
            with pytest.raises(ProvisionNonRetryableError) as ei:
                provision_client.revoke_access(1, "-1001")
            assert ei.value.error_code == "user_blocked"

    def test_malformed_bot_response(self):
        r = mock.Mock(); r.status_code = 200
        r.json = lambda: (_ for _ in ()).throw(ValueError("no json"))
        with mock.patch("requests.post", return_value=r):
            with pytest.raises(ProvisionNonRetryableError) as ei:
                provision_client.grant_access(1, "-1001")
            assert ei.value.error_code == "malformed_response"


def kwargs_data(mp):
    (call,) = mp.call_args_list
    return call.kwargs.get("data") or call[1]["data"]


@pytest.mark.usefixtures("_cfg")
@pytest.mark.django_db
class TestReconcileProvisionTransport:
    """reconcile routes through the contract client when configured."""

    def _setup(self):
        from django.contrib.auth import get_user_model
        from apps.accounts.models import UserPreference
        from apps.bot_integration.models import (
            BotConfig, TelegramAccount, PlanChannelMapping, BotAccessAudit,
            UserChannelAssignment)
        from apps.subscriptions.models import Plan, PlanPrice
        U = get_user_model()
        u = U.objects.create(username="pv", email="pv@x.com")
        UserPreference.objects.get_or_create(user=u)
        BotConfig.objects.create(telegram_bot_token="t",
                                 telegram_control_channel_id="-100CTRL")
        plan = Plan.objects.create(name="PV", display_order=1, tier="pro")
        PlanPrice.objects.create(plan=plan, interval="monthly", price_cents=100)
        PlanChannelMapping.objects.create(plan=plan, platform="telegram",
                                          external_id="-100TARGET")
        TelegramAccount.objects.create(user=u, telegram_user_id=555,
                                       chat_id=555, is_active=True)
        return u, plan

    def test_grant_via_provision_client(self):
        from apps.subscriptions.models import Subscription
        from apps.bot_integration.models import BotAccessAudit, UserChannelAssignment
        u, plan = self._setup()
        with mock.patch("apps.bot_integration.reconcile.provision_client.grant_access",
                        return_value="applied") as g:
            Subscription.objects.create(user=u, plan=plan, status="active",
                                        is_active=True)
            g.assert_called_once()
            assert g.call_args.kwargs.get("user_id") == u.id or                 g.call_args[1].get("user_id") == u.id
        assert UserChannelAssignment.objects.filter(
            user=u, external_id="-100TARGET", is_active=True).exists()
        assert BotAccessAudit.objects.filter(user=u, action="grant",
                                             status="success").exists()

    def test_revoke_via_provision_client(self):
        from apps.subscriptions.models import Subscription
        from apps.bot_integration.models import BotAccessAudit, UserChannelAssignment
        u, plan = self._setup()
        with mock.patch("apps.bot_integration.reconcile.provision_client.grant_access",
                        return_value="applied"):
            sub = Subscription.objects.create(user=u, plan=plan, status="active",
                                              is_active=True)
        with mock.patch("apps.bot_integration.reconcile.provision_client.revoke_access",
                        return_value="applied") as rv:
            sub.status = "expired"; sub.is_active = False; sub.save()
            rv.assert_called_once()
        assert not UserChannelAssignment.objects.filter(
            user=u, external_id="-100TARGET", is_active=True).exists()
        assert BotAccessAudit.objects.filter(user=u, action="revoke",
                                             status="success").exists()

    def test_control_channel_mapping_rejected(self):
        from django.core.exceptions import ValidationError
        from apps.bot_integration.models import BotConfig, PlanChannelMapping
        from apps.subscriptions.models import Plan
        plan = Plan.objects.create(name="CC", display_order=2, tier="pro")
        BotConfig.objects.create(telegram_bot_token="t",
                                 telegram_control_channel_id="-100CTRL")
        pcm = PlanChannelMapping(plan=plan, platform="telegram",
                                 external_id="-100CTRL")
        with pytest.raises(ValidationError):
            pcm.full_clean()
