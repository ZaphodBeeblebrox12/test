"""Batch membership-sync command tests."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from apps.bot_integration.models import TelegramAccount

User = get_user_model()


class SyncChannelMembershipsCommandTests(TestCase):
    def setUp(self):
        self.u1 = User.objects.create_user(username="batch1", password="x")
        self.u2 = User.objects.create_user(username="batch2", password="x")
        TelegramAccount.objects.create(user=self.u1, chat_id=1)
        TelegramAccount.objects.create(user=self.u2, chat_id=2)

    def test_delegates_to_batch_service(self):
        with mock.patch(
                "apps.bot_integration.management.commands."
                "sync_channel_memberships.sync_all_channel_memberships",
                return_value=2) as fn:
            call_command("sync_channel_memberships", sleep=0)
        fn.assert_called_once_with(sleep=0)
