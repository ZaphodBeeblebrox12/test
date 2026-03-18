"""
Subscription serializers for API responses - Phase 3B.
"""
from rest_framework import serializers

from .models import (
    Plan, PlanPrice, Subscription, SubscriptionHistory,
    PlanDiscount, GiftSubscription
)


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


class PlanDiscountSerializer(serializers.ModelSerializer):
    """Serializer for plan discounts."""

    class Meta:
        model = PlanDiscount
        fields = [
            "id",
            "code",
            "description",
            "discount_type",
            "discount_value",
            "valid_from",
            "valid_until",
            "is_active",
        ]
        read_only_fields = fields


class PlanSerializer(serializers.ModelSerializer):
    """Serializer for subscription plans."""

    prices = PlanPriceSerializer(many=True, read_only=True)
    discounts = PlanDiscountSerializer(many=True, read_only=True)

    class Meta:
        model = Plan
        fields = [
            "id",
            "tier",
            "name",
            "description",
            "upgrade_priority",
            "max_projects",
            "max_storage_mb",
            "api_calls_per_day",
            "is_active",
            "display_order",
            "prices",
            "discounts",
            "created_at",
        ]
        read_only_fields = fields


class SubscriptionSerializer(serializers.ModelSerializer):
    """Serializer for user subscriptions."""

    plan = PlanSerializer(read_only=True)
    plan_price = PlanPriceSerializer(read_only=True)
    applied_discount = PlanDiscountSerializer(read_only=True)

    class Meta:
        model = Subscription
        fields = [
            "id",
            "plan",
            "plan_price",
            "applied_discount",
            "is_admin_grant",
            "granted_by",
            "granted_reason",
            "is_trial",
            "trial_days",
            "status",
            "is_active",
            "started_at",
            "expires_at",
            "canceled_at",
            "prorated_credit_cents",
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


class GiftSubscriptionSerializer(serializers.ModelSerializer):
    """Serializer for gift subscriptions."""

    plan_name = serializers.CharField(source="plan.name", read_only=True)
    sender_username = serializers.CharField(source="sender.username", read_only=True)
    redeemed_by_username = serializers.CharField(
        source="redeemed_by.username", 
        read_only=True
    )

    class Meta:
        model = GiftSubscription
        fields = [
            "id",
            "code",
            "plan",
            "plan_name",
            "duration_days",
            "sender",
            "sender_username",
            "recipient_email",
            "message",
            "status",
            "redeemed_by",
            "redeemed_by_username",
            "redeemed_at",
            "expires_at",
            "created_at",
        ]
        read_only_fields = [
            "id", "code", "sender", "sender_username",
            "redeemed_by", "redeemed_by_username", "redeemed_at",
            "created_at"
        ]
