"""
Subscription serializers for API responses.
"""
from rest_framework import serializers

from .models import Plan, PlanPrice, Subscription, SubscriptionHistory


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
