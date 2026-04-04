import requests
import logging
import time
from datetime import timedelta
from functools import wraps
from typing import Optional, Dict, Any
from django.utils import timezone

logger = logging.getLogger(__name__)


def retry_on_flood(max_retries=3, base_delay=1):
    """Decorator to retry on Telegram flood wait (error 429)."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                result = func(*args, **kwargs)
                if not isinstance(result, dict):
                    return result
                if result.get("ok"):
                    return result
                if result.get("error_code") == 429:
                    retry_after = result.get("parameters", {}).get("retry_after", base_delay * (2 ** attempt))
                    logger.warning(f"Flood wait {retry_after}s, retry {attempt+1}/{max_retries}")
                    time.sleep(retry_after)
                    continue
                return result
            return {"ok": False, "error": "Max retries exceeded"}
        return wrapper
    return decorator


class TelegramBotService:
    @classmethod
    def _get_config(cls):
        from ..models import BotConfig
        return BotConfig.get_config()

    @classmethod
    def _get_token(cls):
        return cls._get_config().telegram_bot_token

    @classmethod
    @retry_on_flood()
    def _api_request(cls, method: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        token = cls._get_token()
        if not token:
            return {"ok": False, "error": "No bot token"}
        url = f"https://api.telegram.org/bot{token}/{method}"
        try:
            resp = requests.post(url, json=params or {}, timeout=(5, 10))
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout:
            return {"ok": False, "error": "Request timeout"}
        except requests.exceptions.RequestException as e:
            logger.exception(f"Telegram API error: {e}")
            return {"ok": False, "error": str(e)}

    @classmethod
    def send_message(cls, chat_id: int, text: str) -> bool:
        result = cls._api_request("sendMessage", {"chat_id": chat_id, "text": text})
        return result.get("ok", False)

    @classmethod
    def create_one_time_invite_link(cls, channel_id: str, expire_seconds: int = 86400) -> Optional[str]:
        expire_date = int((timezone.now() + timedelta(seconds=expire_seconds)).timestamp())
        result = cls._api_request("createChatInviteLink", {
            "chat_id": channel_id,
            "expire_date": expire_date,
            "member_limit": 1,
        })
        if result.get("ok"):
            return result["result"]["invite_link"]
        logger.error(f"Failed to create invite link for {channel_id}: {result}")
        return None

    @classmethod
    def ban_user(cls, channel_id: str, user_id: int) -> tuple[bool, str]:
        """
        Ban a user from a Telegram channel.
        Returns (success, error_message). Treats "user already not in chat" as success.
        """
        if not user_id:
            return False, "Missing telegram_user_id"
        result = cls._api_request("banChatMember", {
            "chat_id": channel_id,
            "user_id": user_id
        })
        if result.get("ok"):
            return True, ""
        error = result.get("description", "")
        if "user not found" in error.lower() or "chat member not found" in error.lower():
            logger.info(f"User {user_id} already not in channel {channel_id}, treating as banned")
            return True, ""
        return False, f"Ban failed: {error} (code {result.get('error_code')})"

    @classmethod
    def unban_user(cls, channel_id: str, user_id: int) -> tuple[bool, str]:
        if not user_id:
            return False, "Missing telegram_user_id"
        result = cls._api_request("unbanChatMember", {
            "chat_id": channel_id,
            "user_id": user_id,
            "only_if_banned": True
        })
        if result.get("ok"):
            return True, ""
        error = result.get("description", "")
        return False, f"Unban failed: {error}"

    @classmethod
    def get_bot_info(cls):
        return cls._api_request("getMe")

    @classmethod
    def set_webhook(cls, url=None):
        config = cls._get_config()
        webhook_url = url or config.telegram_webhook_url
        if not webhook_url:
            return False
        result = cls._api_request("setWebhook", {"url": webhook_url})
        return result.get("ok", False)