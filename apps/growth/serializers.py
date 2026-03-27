"""
Growth serializers for API endpoints.
"""
from rest_framework import serializers

from .models import GiftInvite, PendingGiftClaim


class GiftInviteSerializer(serializers.ModelSerializer):
    """Serializer for GiftInvite (read-only)."""

    status_display = serializers.CharField(source='get_status_display', read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    is_claimable = serializers.BooleanField(read_only=True)
    can_resend_email = serializers.BooleanField(read_only=True)

    class Meta:
        model = GiftInvite
        fields = [
            'id',
            'recipient_email',
            'status',
            'status_display',
            'is_expired',
            'is_claimable',
            'claimed_by',
            'claimed_at',
            'expires_at',
            'email_sent_at',
            'email_resend_count',
            'created_at',
            'can_resend_email',
        ]
        read_only_fields = fields


class GiftCreateSerializer(serializers.Serializer):
    """Serializer for creating a new gift."""

    recipient_email = serializers.EmailField()
    plan_id = serializers.UUIDField()
    duration_days = serializers.IntegerField(min_value=1, max_value=365, default=30)
    message = serializers.CharField(max_length=1000, required=False, allow_blank=True)


class GiftClaimResponseSerializer(serializers.Serializer):
    """Serializer for gift claim response."""

    success = serializers.BooleanField()
    subscription_id = serializers.UUIDField(required=False)
    error_code = serializers.CharField(required=False)
    error_message = serializers.CharField(required=False)
    redirect_url = serializers.CharField(required=False)


class PendingGiftClaimSerializer(serializers.ModelSerializer):
    """Serializer for PendingGiftClaim (admin/debug only)."""

    status_display = serializers.CharField(source='get_status_display', read_only=True)
    is_stale = serializers.BooleanField(read_only=True)

    class Meta:
        model = PendingGiftClaim
        fields = [
            'id',
            'claim_token_hash',
            'session_key',
            'status',
            'status_display',
            'processed_at',
            'processed_by',
            'error_message',
            'created_at',
            'is_stale',
        ]
        read_only_fields = fields



class LegacyGiftClaimSerializer(serializers.Serializer):
    """Serializer for legacy gift code claim requests."""

    gift_code = serializers.CharField(
        max_length=20,
        required=True,
        help_text="The legacy gift code (e.g., ABC123XY)"
    )

    def validate_gift_code(self, value):
        """Normalize the gift code."""
        return value.upper().strip()
