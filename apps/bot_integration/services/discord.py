import requests
import logging
from ..models import BotConfig

logger = logging.getLogger(__name__)


class DiscordBotService:
    @classmethod
    def _get_config(cls):
        return BotConfig.get_config()

    @classmethod
    def _get_token(cls):
        return cls._get_config().discord_bot_token

    @classmethod
    def _guild_id(cls):
        return cls._get_config().discord_guild_id

    @classmethod
    def _api_request(cls, endpoint, method="GET", data=None):
        token = cls._get_token()
        if not token:
            return {"error": "No token"}
        url = f"https://discord.com/api/v10/{endpoint.lstrip('/')}"
        headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
        try:
            resp = requests.request(method, url, headers=headers, json=data, timeout=10)
            if resp.status_code == 204:
                return {"ok": True}
            return resp.json()
        except Exception as e:
            logger.exception(f"Discord API error: {e}")
            return {"error": str(e)}

    @classmethod
    def add_role(cls, user_id, role_id):
        guild_id = cls._guild_id()
        if not guild_id:
            return False
        result = cls._api_request(f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}", method="PUT")
        return result.get("ok") is True or "id" in result

    @classmethod
    def remove_role(cls, user_id, role_id):
        guild_id = cls._guild_id()
        if not guild_id:
            return False
        result = cls._api_request(f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}", method="DELETE")
        return result.get("ok") is True or "error" not in result

    @classmethod
    def get_bot_info(cls):
        result = cls._api_request("/users/@me")
        if "id" in result:
            return result
        return None