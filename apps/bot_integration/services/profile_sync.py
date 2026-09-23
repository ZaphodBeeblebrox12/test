"""Best-effort Telegram profile enrichment for linked accounts.

Triggered AFTER the verification transaction commits (from the webhook via
transaction.on_commit) so webhook handling is never slowed. Fetches the
user's display name and avatar from the Bot API and caches the avatar in
MEDIA_ROOT (MEDIA_URL already configured). Every failure is logged and
swallowed — enrichment must never break verification or reconciliation.
"""
import logging

import requests
from django.core.files.base import ContentFile
from django.utils import timezone

from ..models import TelegramAccount
from .telegram import TelegramBotService

logger = logging.getLogger(__name__)


def sync_telegram_profile_for_user(user_id):
    """Entry point for on_commit / reconcile hooks."""
    account = TelegramAccount.objects.filter(
        user_id=user_id, is_active=True).select_related("user").first()
    if account is None:
        return
    sync_telegram_profile(account)


def sync_telegram_profile(account):
    """Refresh username/first_name (if empty) and download the avatar."""
    try:
        user_id = account.telegram_user_id or account.chat_id
        photos = TelegramBotService.get_user_profile_photos(user_id)
        if not photos.get("ok"):
            logger.info("profile photos unavailable for %s: %s",
                        user_id, photos.get("description") or photos.get("error"))
            return
        result = photos.get("result") or {}
        if not result.get("total_count"):
            return  # user has no avatar; leave whatever is stored
        sizes = result["photos"][0]
        file_id = sizes[-1]["file_id"]  # largest available size
        file_info = TelegramBotService.get_file(file_id)
        if not file_info.get("ok"):
            return
        file_path = file_info["result"]["file_path"]
        token = TelegramBotService._get_token()
        url = "https://api.telegram.org/file/bot{}/{}".format(token, file_path)
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        if account.avatar:
            account.avatar.delete(save=False)  # avoid orphaned files
        name = "tg_{}.jpg".format(account.telegram_user_id or account.chat_id)
        account.avatar.save(name, ContentFile(resp.content), save=False)
        account.last_synced_at = timezone.now()
        account.save(update_fields=["avatar", "last_synced_at"])
    except Exception:
        logger.exception("telegram profile sync failed for user %s",
                         getattr(account, "user_id", "?"))
