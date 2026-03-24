"""
Subscription serializers for API responses.
"""
from rest_framework import serializers

from .models import (
    Plan, PlanPrice, Subscription, SubscriptionHistory,
    GiftSubscription, UpgradeHistory, GeoPlanPrice
)


# =============================================================================
# EXISTING SERIALIZERS (unchanged)
# =============================================================================

class PlanPriceSerializer(serializers.ModelSerializer):
    """Serializer for plan pricing."""

    price_dollars = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        read_only=True
    )

    class Meta:
        model = PlanPrice
        fields = [
            "id",
            "interval",
            "price_cents",
            "price_dollars",
            "currency",
            "is_active",
        ]
        read_only_fields = fields


class PlanSerializer(serializers.ModelSerializer):
    """Serializer for subscription plans."""

    prices = PlanPriceSerializer(many=True, read_only=True)

    class Meta:
        model = Plan
        fields = [
            "id",
            "tier",
            "name",
            "description",
            "max_projects",
            "max_storage_mb",
            "api_calls_per_day",
            "is_active",
            "display_order",
            "prices",
            "created_at",
        ]
        read_only_fields = fields


class SubscriptionSerializer(serializers.ModelSerializer):
    """Serializer for user subscriptions."""

    plan = PlanSerializer(read_only=True)
    plan_price = PlanPriceSerializer(read_only=True)

    class Meta:
        model = Subscription
        fields = [
            "id",
            "plan",
            "plan_price",
            "status",
            "is_active",
            "started_at",
            "expires_at",
            "canceled_at",
            "payment_provider",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class SubscriptionHistorySerializer(serializers.ModelSerializer):
    """Serializer for subscription history events."""

    class Meta:
        model = SubscriptionHistory
        fields = [
            "id",
            "event_type",
            "previous_plan_id",
            "new_plan_id",
            "previous_status",
            "new_status",
            "metadata",
            "notes",
            "created_at",
        ]
        read_only_fields = fields


# =============================================================================
# NEW SERIALIZERS (Phase 3B)
# =============================================================================

class GeoPlanPriceSerializer(serializers.ModelSerializer):
    """Serializer for geo plan pricing."""

    price_dollars = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        read_only=True
    )
    price_type = serializers.SerializerMethodField()

    class Meta:
        model = GeoPlanPrice
        fields = [
            "id",
            "interval",
            "price_cents",
            "price_dollars",
            "currency",
            "country",
            "region",
            "is_active",
            "price_type",
        ]
        read_only_fields = fields

    def get_price_type(self, obj: GeoPlanPrice) -> str:
        if obj.country:
            return "country"
        elif obj.region:
            return "region"
        return "global"


class ResolvedPriceSerializer(serializers.Serializer):
    """Serializer for resolved geo price."""

    id = serializers.UUIDField()
    interval = serializers.CharField()
    price_cents = serializers.IntegerField()
    price_dollars = serializers.DecimalField(max_digits=10, decimal_places=2)
    currency = serializers.CharField()
    country = serializers.CharField(allow_null=True)
    region = serializers.CharField(allow_null=True)
    is_resolved = serializers.BooleanField(default=True)


class GiftSubscriptionSerializer(serializers.ModelSerializer):
    """Serializer for gift subscriptions."""

    plan_name = serializers.SerializerMethodField()
    from_username = serializers.SerializerMethodField()
    to_username = serializers.SerializerMethodField()
    price_display = serializers.SerializerMethodField()

    class Meta:
        model = GiftSubscription
        fields = [
            "id",
            "plan",
            "plan_name",
            "plan_price",
            "from_user",
            "from_username",
            "to_user",
            "to_username",
            "message",
            "gift_code",
            "duration_days",
            "expires_at",
            "status",
            "claimed_at",
            "price_display",
            "pricing_country",
            "pricing_region",
            "created_at",
        ]
        read_only_fields = fields

    def get_plan_name(self, obj: GiftSubscription) -> str:
        return obj.plan.name if obj.plan else None

    def get_from_username(self, obj: GiftSubscription) -> str:
        return obj.from_user.username if obj.from_user else None

    def get_to_username(self, obj: GiftSubscription) -> str:
        return obj.to_user.username if obj.to_user else None

    def get_price_display(self, obj: GiftSubscription) -> str:
        if obj.plan_price:
            return f"{obj.plan_price.currency} {obj.plan_price.price_dollars:.2f}"
        return "Free"


class UpgradeHistorySerializer(serializers.ModelSerializer):
    """Serializer for upgrade history records."""

    from_plan_name = serializers.SerializerMethodField()
    to_plan_name = serializers.SerializerMethodField()
    from_price_dollars = serializers.SerializerMethodField()
    to_price_dollars = serializers.SerializerMethodField()
    prorated_credit_dollars = serializers.SerializerMethodField()
    amount_due_dollars = serializers.SerializerMethodField()

    class Meta:
        model = UpgradeHistory
        fields = [
            "id",
            "from_plan",
            "from_plan_name",
            "to_plan",
            "to_plan_name",
            "from_price_cents",
            "from_price_dollars",
            "to_price_cents",
            "to_price_dollars",
            "prorated_credit_cents",
            "prorated_credit_dollars",
            "amount_due_cents",
            "amount_due_dollars",
            "pricing_country",
            "pricing_region",
            "is_successful",
            "error_message",
            "created_at",
        ]
        read_only_fields = fields

    def get_from_plan_name(self, obj: UpgradeHistory) -> str:
        return obj.from_plan.name if obj.from_plan else None

    def get_to_plan_name(self, obj: UpgradeHistory) -> str:
        return obj.to_plan.name if obj.to_plan else None

    def get_from_price_dollars(self, obj: UpgradeHistory) -> float:
        return obj.from_price_cents / 100

    def get_to_price_dollars(self, obj: UpgradeHistory) -> float:
        return obj.to_price_cents / 100

    def get_prorated_credit_dollars(self, obj: UpgradeHistory) -> float:
        return obj.prorated_credit_cents / 100

    def get_amount_due_dollars(self, obj: UpgradeHistory) -> float:
        return obj.amount_due_cents / 100
