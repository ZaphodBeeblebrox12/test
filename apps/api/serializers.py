"""Serializers for the API app."""
from rest_framework import serializers

from .models import APIKey


class APIKeySerializer(serializers.ModelSerializer):
    """Serializer for API keys (raw key only returned on create, via the view)."""

    class Meta:
        model = APIKey
        fields = ["id", "name", "key_prefix", "is_active", "rate_limit",
                  "last_used_at", "expires_at", "created_at"]
        read_only_fields = ["id", "key_prefix", "is_active", "last_used_at",
                            "expires_at", "created_at"]
