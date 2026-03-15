"""
API authentication classes.
"""
from django.utils import timezone
from rest_framework import authentication, exceptions

from apps.api.models import APIKey


class APIKeyAuthentication(authentication.BaseAuthentication):
    """API key authentication."""

    keyword = 'ApiKey'

    def authenticate(self, request):
        """Authenticate request using API key."""
        # Check header
        auth_header = request.META.get('HTTP_X_API_KEY', '')
        if not auth_header:
            # Also check query param
            auth_header = request.GET.get('api_key', '')

        if not auth_header:
            return None

        # Get API key
        try:
            api_key = self.get_api_key(auth_header)
        except APIKey.DoesNotExist:
            raise exceptions.AuthenticationFailed('Invalid API key')

        # Check if active
        if not api_key.is_active:
            raise exceptions.AuthenticationFailed('API key is disabled')

        # Check expiration
        if api_key.expires_at and api_key.expires_at < timezone.now():
            raise exceptions.AuthenticationFailed('API key has expired')

        # Update last used
        api_key.last_used_at = timezone.now()
        api_key.save(update_fields=['last_used_at'])

        return (api_key.user, api_key)

    def get_api_key(self, key):
        """Get API key by raw key value."""
        from apps.api.models import APIKey
        key_hash = APIKey.hash_key(key)
        return APIKey.objects.get(key_hash=key_hash)
