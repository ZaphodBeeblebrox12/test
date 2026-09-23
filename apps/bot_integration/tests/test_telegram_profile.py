"""Telegram profile enrichment tests (model fields + sync service)."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.bot_integration.models import TelegramAccount
from apps.bot_integration.services import profile_sync

User = get_user_model()


class TelegramProfileFieldsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="tguser", password="x")
        self.account = TelegramAccount.objects.create(
            user=self.user, chat_id=555000111, telegram_user_id=555000111)

    def test_new_fields_default_empty(self):
        self.assertEqual(self.account.username, "")
        self.assertEqual(self.account.first_name, "")
        self.assertIsNone(self.account.avatar.name if self.account.avatar else None)

    def test_display_name_fallbacks(self):
        self.assertEqual(self.account.display_name, "User 555000111")
        self.account.first_name = "Inder"
        self.assertEqual(self.account.display_name, "Inder")
        self.account.username = "inderdev"
        self.assertEqual(self.account.display_name, "inderdev")


class ProfileSyncTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="tguser2", password="x")
        self.account = TelegramAccount.objects.create(
            user=self.user, chat_id=555000222, telegram_user_id=555000222)

    def test_sync_downloads_and_caches_avatar(self):
        photos = {"ok": True, "result": {"total_count": 1,
                   "photos": [[{"file_id": "small"}, {"file_id": "big"}]]}}
        file_info = {"ok": True, "result": {"file_path": "photos/file_1.jpg"}}
        with mock.patch.object(
                profile_sync.TelegramBotService, "get_user_profile_photos",
                return_value=photos), \
             mock.patch.object(
                profile_sync.TelegramBotService, "get_file",
                return_value=file_info), \
             mock.patch.object(
                profile_sync.TelegramBotService, "_get_token",
                return_value="tok"), \
             mock.patch.object(profile_sync.requests, "get") as req:
            req.return_value.content = b"\xff\xd8fake-jpeg"
            req.return_value.raise_for_status = lambda: None
            profile_sync.sync_telegram_profile(self.account)
        self.account.refresh_from_db()
        self.assertTrue(self.account.avatar.name.startswith("telegram_avatars/"))
        self.assertIsNotNone(self.account.last_synced_at)

    def test_sync_no_photos_is_silent_noop(self):
        with mock.patch.object(
                profile_sync.TelegramBotService, "get_user_profile_photos",
                return_value={"ok": True, "result": {"total_count": 0}}):
            profile_sync.sync_telegram_profile(self.account)  # must not raise
        self.account.refresh_from_db()
        self.assertFalse(self.account.avatar)

    def test_sync_swallows_api_errors(self):
        with mock.patch.object(
                profile_sync.TelegramBotService, "get_user_profile_photos",
                side_effect=RuntimeError("boom")):
            profile_sync.sync_telegram_profile(self.account)  # must not raise

    def test_sync_for_missing_account_is_noop(self):
        profile_sync.sync_telegram_profile_for_user(999999)  # no raise
