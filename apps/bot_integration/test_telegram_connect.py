import json
import secrets
import time
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.bot_integration.models import (
    BotConfig, TelegramAccount, TelegramVerificationToken,
)
from apps.bot_integration.provision_auth import sign_payload
from apps.subscriptions.services import _telegram_connect_email_url


class TelegramConnectEmailTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="telegram-link-test", email="telegram-link@example.test",
            password="not-a-real-password-123")
        BotConfig.objects.update_or_create(
            pk=1, defaults={"telegram_bot_username": "inderjeetbot"})

    def test_email_link_contains_one_week_verification_token(self):
        url = _telegram_connect_email_url(self.user)
        self.assertTrue(url.startswith("https://t.me/inderjeetbot?start=verify_"))
        token = TelegramVerificationToken.objects.get(user=self.user)
        self.assertGreater(token.expires_at, timezone.now() + timedelta(days=6))

    def test_email_link_is_omitted_when_telegram_is_already_linked(self):
        TelegramAccount.objects.create(
            user=self.user, chat_id=123456789, telegram_user_id=123456789,
            is_active=True)
        self.assertEqual(_telegram_connect_email_url(self.user), "")
        self.assertFalse(TelegramVerificationToken.objects.filter(user=self.user).exists())


class TelegramGreetingHandlerTests(TestCase):
    @override_settings(PROVISION_SHARED_SECRET="test-secret")
    def test_hi_receives_connection_instructions_but_does_not_link_account(self):
        cache.clear()
        body = json.dumps({
            "update_id": 987654,
            "message": {"text": "Hi", "chat": {"id": 123456789},
                        "from": {"id": 123456789}},
        }, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(18)
        signature = sign_payload("test-secret", timestamp, nonce, body)

        with patch("apps.bot_integration.views.TelegramBotService.send_message") as send_message:
            response = self.client.post(
                reverse("bot_integration:telegram_webhook"), data=body,
                content_type="application/json",
                HTTP_X_BOT_TIMESTAMP=timestamp,
                HTTP_X_BOT_NONCE=nonce,
                HTTP_X_BOT_SIGNATURE=signature,
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(send_message.called)
        self.assertIn("Connect Telegram", send_message.call_args.args[1])
        self.assertFalse(TelegramAccount.objects.filter(chat_id=123456789).exists())


class TelegramDashboardConnectRouteTests(TestCase):
    @override_settings(TELEGRAM_BOT_USERNAME="inderjeetbot")
    def test_connect_uses_setting_when_database_username_is_blank(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="dashboard-link-test", email="dashboard-link@example.test",
            password="not-a-real-password-123")
        BotConfig.objects.update_or_create(pk=1, defaults={"telegram_bot_username": ""})
        self.client.force_login(user)

        response = self.client.get(reverse("bot_integration:telegram_connect"))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(
            "https://t.me/inderjeetbot?start=verify_"))
