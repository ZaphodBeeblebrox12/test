"""
Subscription API views with geo pricing support.
"""
import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response

from .models import (
    Plan, Subscription, GiftSubscription, UpgradeHistory, 
    PlanPrice, GeoPlanPrice, SubscriptionHistory
)
from .serializers import (
    PlanSerializer, SubscriptionSerializer,
    GiftSubscriptionSerializer, UpgradeHistorySerializer
)
from .services import (
    resolve_plan_price,
    get_pricing_country,
    get_region_for_country,
    create_gift_subscription,
    claim_gift_subscription,
    grant_subscription_by_admin,
    start_trial,
)

logger = logging.getLogger(__name__)


@api_view(["GET"])
def plan_list(request):
    """List all active plans with standard pricing."""
    plans = Plan.objects.filter(is_active=True).order_by("display_order")
    serializer = PlanSerializer(plans, many=True)
    return Response({"plans": serializer.data})


@api_view(["GET"])
def plan_list_geo(request):
    """List all active plans with geo-specific pricing."""
    country = get_pricing_country(request)
    region = get_region_for_country(country) if country else None

    plans = Plan.objects.filter(is_active=True).order_by("display_order")
    data = []

    for plan in plans:
        plan_data = PlanSerializer(plan).data

        try:
            price_info = resolve_plan_price(plan, country)
            plan_data["price_cents"] = price_info["price_cents"]
            plan_data["currency"] = price_info["currency"]
            plan_data["price_display"] = price_info["display"]
            plan_data["geo_pricing"] = price_info["geo_pricing"]
        except PlanPrice.DoesNotExist:
            # FIXED: Skip plans with no pricing instead of crashing
            logger.warning(f"No pricing found for plan {plan.name}, skipping")
            continue
        except Exception as e:
            # FIXED: Skip on any error, don't crash
            logger.warning(f"Could not resolve price for {plan.name}: {e}")
            continue

        data.append(plan_data)

    return Response({
        "plans": data,
        "user_country": country,
        "user_region": region,
    })


@api_view(["GET"])
def plan_detail_geo(request, plan_id):
    """Get plan details with geo-specific pricing."""
    try:
        plan = Plan.objects.get(id=plan_id, is_active=True)
    except Plan.DoesNotExist:
        return Response(
            {"error": "Plan not found"}, 
            status=status.HTTP_404_NOT_FOUND
        )

    country = get_pricing_country(request)
    plan_data = PlanSerializer(plan).data

    try:
        price_info = resolve_plan_price(plan, country)
        plan_data["price_cents"] = price_info["price_cents"]
        plan_data["currency"] = price_info["currency"]
        plan_data["price_display"] = price_info["display"]
        plan_data["geo_pricing"] = price_info["geo_pricing"]
        plan_data["price_breakdown"] = price_info.get("breakdown")
    except PlanPrice.DoesNotExist:
        # FIXED: Return 400 error with clear message instead of crashing
        return Response(
            {"error": f"No pricing configured for plan {plan.name}"},
            status=status.HTTP_400_BAD_REQUEST
        )
    except Exception as e:
        logger.warning(f"Could not resolve price for {plan.name}: {e}")
        return Response(
            {"error": "Could not resolve pricing"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    return Response({
        "plan": plan_data,
        "user_country": country,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_subscription(request):
    """Get current user's active subscription."""
    try:
        subscription = Subscription.objects.get(
            user=request.user, 
            status=Subscription.Status.ACTIVE
        )
        serializer = SubscriptionSerializer(subscription)
        return Response({"subscription": serializer.data})
    except Subscription.DoesNotExist:
        return Response(
            {"subscription": None, "message": "No active subscription"}
        )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_gift(request):
    """Create a gift subscription."""
    pass


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def claim_gift(request):
    """Claim a gift subscription."""
    pass


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_gifts(request):
    """List user's gift subscriptions."""
    gifts = GiftSubscription.objects.filter(from_user=request.user)
    serializer = GiftSubscriptionSerializer(gifts, many=True)
    return Response({"gifts": serializer.data})


@api_view(["POST"])
@permission_classes([IsAdminUser])
def admin_grant_subscription(request):
    """Admin endpoint to grant subscription."""
    pass


@api_view(["POST"])
@permission_classes([IsAdminUser])
def admin_start_trial(request):
    """Admin endpoint to start trial."""
    pass


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def subscription_history(request):
    """
    Get user's subscription history.

    FIXED: Uses actual fields from SubscriptionHistory model:
    - event_type, previous_plan_id, new_plan_id
    - previous_status, new_status, metadata, notes, created_at
    """
    history = SubscriptionHistory.objects.filter(
        user=request.user
    ).order_by("-created_at")

    data = []
    for record in history:
        # FIXED: Use actual model fields, not non-existent ones
        data.append({
            "id": str(record.id),
            "event_type": record.event_type,
            "event_type_display": record.get_event_type_display(),
            "previous_plan_id": str(record.previous_plan_id) if record.previous_plan_id else None,
            "new_plan_id": str(record.new_plan_id) if record.new_plan_id else None,
            "previous_status": record.previous_status,
            "new_status": record.new_status,
            "metadata": record.metadata,
            "notes": record.notes,
            "created_at": record.created_at.isoformat() if record.created_at else None,
        })

    return Response({"history": data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def upgrade_history_list(request):
    """Get user's upgrade history."""
    upgrades = UpgradeHistory.objects.filter(
        subscription__user=request.user
    ).order_by("-upgraded_at")
    serializer = UpgradeHistorySerializer(upgrades, many=True)
    return Response({"upgrades": serializer.data})
