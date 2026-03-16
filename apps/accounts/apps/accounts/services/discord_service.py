"""
Discord OAuth2 service for account verification.
"""
import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class DiscordOAuth2Service:
    """Service for Discord OAuth2 authentication."""

    DISCORD_API_BASE = "https://discord.com/api/v10"

    @classmethod
    def get_client_id(cls):
        client_id = getattr(settings, 'DISCORD_CLIENT_ID', None)
        if not client_id:
            raise ImproperlyConfigured("DISCORD_CLIENT_ID not set in settings")
        return client_id

    @classmethod
    def get_client_secret(cls):
        secret = getattr(settings, 'DISCORD_CLIENT_SECRET', None)
        if not secret:
            raise ImproperlyConfigured("DISCORD_CLIENT_SECRET not set in settings")
        return secret

    @classmethod
    def get_redirect_uri(cls):
        """Get redirect URI for Discord OAuth callback."""
        site_url = getattr(settings, 'SITE_URL', None)
        if site_url:
            return f"{site_url}/auth/discord/callback/"
        return "http://localhost:8000/auth/discord/callback/"

    @classmethod
    def get_authorization_url(cls, state=None):
        """Generate Discord OAuth2 authorization URL."""
        client_id = cls.get_client_id()
        redirect_uri = cls.get_redirect_uri()
        scopes = "identify email"

        url = (
            f"{cls.DISCORD_API_BASE}/oauth2/authorize"
            f"?client_id={client_id}"
            f"&redirect_uri={redirect_uri}"
            f"&response_type=code"
            f"&scope={scopes.replace(' ', '%20')}"
        )
        if state:
            url += f"&state={state}"
        return url

    @classmethod
    def exchange_code_for_token(cls, code):
        """Exchange authorization code for access token."""
        client_id = cls.get_client_id()
        client_secret = cls.get_client_secret()
        redirect_uri = cls.get_redirect_uri()

        data = {
            'client_id': client_id,
            'client_secret': client_secret,
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
        }
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}

        response = requests.post(
            f"{cls.DISCORD_API_BASE}/oauth2/token",
            data=data,
            headers=headers
        )

        if response.status_code != 200:
            raise Exception(f"Token exchange failed: {response.text}")
        return response.json()

    @classmethod
    def get_user_info(cls, access_token):
        """Get Discord user info using access token."""
        headers = {'Authorization': f'Bearer {access_token}'}
        response = requests.get(
            f"{cls.DISCORD_API_BASE}/users/@me",
            headers=headers
        )

        if response.status_code != 200:
            raise Exception(f"Failed to get user info: {response.text}")
        return response.json()
