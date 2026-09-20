"""Regression tests: Telegram account-linking flow (deep link + webhook).

Root cause fixed: BotConfig.telegram_bot_username could be stored with a
leading '@' (e.g. '@inderjeetbot'), producing the invalid deep link
https://t.me/@inderjeetbot?start=... which Telegram resolves to telegram.org.

Canonical form (username WITHOUT '@') is enforced at the data-entry
boundary: BotConfig.clean()/save() strip any '@' and whitespace, and the
admin shows help text. The deep-link construction itself is unchanged.
"""

import json
import time
import uuid
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.bot_integration.models import (
    BotConfig, TelegramAccount, TelegramVerificationToken)
from apps.bot_integration.provision_auth import sign_payload

# Mirrors PROVISION_SHARED_SECRET in config.settings.test_provision
TEST_SECRET = "test-secret"


def signed_headers(body: bytes, instance: str = "telegram-bot"):
    """Authenticate exactly like the bot's verify_forwarder/provision client."""
    ts = str(int(time.time()))
    nonce = uuid.uuid4().hex
    return {
        "X-Bot-Instance": instance,
        "X-Bot-Timestamp": ts,
        "X-Bot-Nonce": nonce,
        "X-Bot-Signature": sign_payload(TEST_SECRET, ts, nonce, body),
    }


def make_user(name, superuser=False):
    if superuser:
        return User.objects.create_superuser(
            username=name, email=f"{name}@x.com", password="pw12345678")
    return User.objects.create_user(username=name, email=f"{name}@x.com",
                                    password="pw12345678")


def make_config(username):
    cfg = BotConfig()
    cfg.telegram_bot_username = username
    cfg.save()
    return cfg


class BotConfigUsernameNormalizationTests(TestCase):
    """Model boundary: every save produces the canonical bare username."""

    def test_strips_at_sign_on_save(self):
        cfg = make_config("@inderjeetbot")
        self.assertEqual(cfg.telegram_bot_username, "inderjeetbot")
        cfg.refresh_from_db()
        self.assertEqual(cfg.telegram_bot_username, "inderjeetbot")

    def test_strips_whitespace_and_at(self):
        cfg = make_config("   @somebot  ")
        self.assertEqual(cfg.telegram_bot_username, "somebot")

    def test_bare_username_unchanged(self):
        cfg = make_config("inderjeetbot")
        self.assertEqual(cfg.telegram_bot_username, "inderjeetbot")

    def test_empty_username_allowed(self):
        cfg = make_config("")
        self.assertEqual(cfg.telegram_bot_username, "")

    def test_get_config_returns_canonical(self):
        make_config("@inderjeetbot")
        self.assertEqual(BotConfig.get_config().telegram_bot_username,
                         "inderjeetbot")


class TelegramConnectDeepLinkTests(TestCase):
    """GET /bot/telegram/connect/ must 302 to a VALID t.me deep link."""

    def assert_valid_deep_link(self, response):
        self.assertEqual(response.status_code, 302)
        url = response["Location"]
        self.assertTrue(url.startswith("https://t.me/inderjeetbot?start=verify_"),
                        f"bad deep link: {url}")
        self.assertNotIn("https://t.me/@", url, f"'@' must not appear: {url}")
        token_str = url.rsplit("verify_", 1)[1]
        self.assertTrue(
            TelegramVerificationToken.objects.filter(token=token_str).exists())
        return url

    def test_config_saved_with_at_sign(self):
        make_config("@inderjeetbot")
        user = make_user("normal1")   # NORMAL user: proves no superuser special-casing
        c = Client()
        c.force_login(user)
        self.assert_valid_deep_link(c.get("/bot/telegram/connect/"))

    def test_config_saved_without_at_sign(self):
        make_config("inderjeetbot")
        user = make_user("normal2")
        c = Client()
        c.force_login(user)
        self.assert_valid_deep_link(c.get("/bot/telegram/connect/"))

    def test_legacy_row_normalized_on_resave(self):
        """A legacy '@name' row is repaired the moment an admin re-saves it
        (admin ModelForm runs clean(); save() also normalizes)."""
        cfg = BotConfig()
        cfg.telegram_bot_username = "x"
        cfg.save()
        # Simulate legacy value without triggering model save normalization
        BotConfig.objects.filter(pk=cfg.pk).update(telegram_bot_username="@inderjeetbot")
        # Admin opens the record and hits Save -> form full_clean + save fix it
        cfg.refresh_from_db()
        cfg.telegram_bot_username = cfg.telegram_bot_username  # "no-op edit"
        cfg.save()
        self.assertEqual(BotConfig.get_config().telegram_bot_username,
                         "inderjeetbot")

    def test_superuser_flow_identical_to_normal_user(self):
        make_config("@inderjeetbot")
        user = make_user("admin1", superuser=True)
        c = Client()
        c.force_login(user)
        self.assert_valid_deep_link(c.get("/bot/telegram/connect/"))

    def test_login_required_for_anonymous(self):
        make_config("inderjeetbot")
        c = Client()
        r = c.get("/bot/telegram/connect/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/accounts/login/", r["Location"])


class TelegramWebhookVerifyFlowTests(TestCase):
    """/start verify_<token> -> TelegramAccount -> reconcile (E2E-ish, mocked)."""

    def setUp(self):
        make_config("inderjeetbot")
        self.user = make_user("hook1")
        self.token = TelegramVerificationToken.create_token(self.user)

    def _post_start(self, text, sign=True):
        body = json.dumps({
            "update_id": 1,
            "message": {
                "message_id": 1,
                "from": {"id": 555000111, "is_bot": False, "first_name": "T"},
                "chat": {"id": 555000111, "type": "private"},
                "text": text,
            },
        }).encode()
        headers = signed_headers(body) if sign else {}
        return Client().post("/bot/telegram/webhook/", data=body,
                             content_type="application/json", headers=headers)

    def test_unsigned_request_rejected(self):
        r = self._post_start(f"/start verify_{self.token.token}", sign=False)
        self.assertEqual(r.status_code, 401)
        self.assertFalse(TelegramAccount.objects.exists())

    def test_bad_signature_rejected(self):
        body = json.dumps({"update_id": 1}).encode()
        hdrs = signed_headers(body)
        hdrs["X-Bot-Signature"] = "0" * 64
        r = Client().post("/bot/telegram/webhook/", data=body,
                          content_type="application/json", headers=hdrs)
        self.assertEqual(r.status_code, 401)
        self.assertFalse(TelegramAccount.objects.exists())

    @mock.patch("apps.jobs.enqueue.enqueue_reconcile")
    @mock.patch("apps.bot_integration.views.TelegramBotService")
    def test_valid_token_creates_account_and_reconciles(self, svc, task):
        r = self._post_start(f"/start verify_{self.token.token}")
        self.assertEqual(r.status_code, 200)
        acct = TelegramAccount.objects.get(user=self.user)
        self.assertEqual(acct.telegram_user_id, 555000111)
        self.assertEqual(acct.chat_id, 555000111)
        self.assertFalse(TelegramVerificationToken.objects.filter(
            token=self.token.token).exists())   # token consumed
        task.assert_called_once_with(self.user.id, reason="telegram_link")
        svc.send_message.assert_called_once()    # confirmation DM sent

    @mock.patch("apps.bot_integration.views.TelegramBotService")
    def test_invalid_token_rejected_no_account(self, svc):
        r = self._post_start("/start verify_doesnotexist")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": False})
        self.assertFalse(TelegramAccount.objects.exists())
        svc.send_message.assert_called_once()   # user is told the code is invalid

    @mock.patch("apps.bot_integration.views.TelegramBotService")
    def test_expired_token_rejected_no_account(self, svc):
        from django.utils import timezone as tz
        TelegramVerificationToken.objects.filter(pk=self.token.pk).update(
            expires_at=tz.now() - tz.timedelta(seconds=10))
        r = self._post_start(f"/start verify_{self.token.token}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": False})
        self.assertFalse(TelegramAccount.objects.exists())

    @mock.patch("apps.bot_integration.views.TelegramBotService")
    def test_non_verify_message_ignored(self, svc):
        r = self._post_start("/start")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(TelegramAccount.objects.exists())
