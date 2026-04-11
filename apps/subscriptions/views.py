"""
Subscription API views with geo pricing and trial support.
"""
import logging

from django.utils import timezone
from django.core.exceptions import PermissionDenied
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response

from .models import (
    Plan, Subscription, GiftSubscription, UpgradeHistory,
    PlanPrice, GeoPlanPrice, SubscriptionHistory, UserTrialUsage
)
from .serializers import (
    PlanSerializer, SubscriptionSerializer,
    GiftSubscriptionSerializer, UpgradeHistorySerializer
)
from .services import (
    resolve_plan_price,
    get_pricing_country,
    get_region_for_country,
    purchase_plan,
    has_user_used_trial,
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
    """List all active plans with geo-specific pricing and trial flags."""
    country = get_pricing_country(request)
    region = get_region_for_country(country) if country else None
    user = request.user if request.user.is_authenticated else None

    plans = Plan.objects.filter(is_active=True).order_by("display_order")
    data = []

    for plan in plans:
        plan_data = PlanSerializer(plan).data
        plan_data["is_trial"] = plan.is_trial
        if plan.is_trial:
            plan_data["trial_duration_days"] = plan.trial_duration_days
            plan_data["already_used"] = has_user_used_trial(user, plan) if user else False
        else:
            plan_data["already_used"] = False

        try:
            price_info = resolve_plan_price(plan, country)
            plan_data["price_cents"] = price_info["price_cents"]
            plan_data["currency"] = price_info["currency"]
            plan_data["price_display"] = price_info["display"]
            plan_data["geo_pricing"] = price_info["geo_pricing"]
        except PlanPrice.DoesNotExist:
            logger.warning(f"No pricing found for plan {plan.name}, skipping")
            continue
        except Exception as e:
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
    """Get plan details with geo-specific pricing and trial status."""
    try:
        plan = Plan.objects.get(id=plan_id, is_active=True)
    except Plan.DoesNotExist:
        return Response(
            {"error": "Plan not found"},
            status=status.HTTP_404_NOT_FOUND
        )

    country = get_pricing_country(request)
    plan_data = PlanSerializer(plan).data
    plan_data["is_trial"] = plan.is_trial

    if plan.is_trial:
        plan_data["trial_duration_days"] = plan.trial_duration_days
        plan_data["already_used"] = has_user_used_trial(request.user, plan) if request.user.is_authenticated else False
    else:
        plan_data["already_used"] = False

    try:
        price_info = resolve_plan_price(plan, country)
        plan_data["price_cents"] = price_info["price_cents"]
        plan_data["currency"] = price_info["currency"]
        plan_data["price_display"] = price_info["display"]
        plan_data["geo_pricing"] = price_info["geo_pricing"]
        plan_data["price_breakdown"] = price_info.get("breakdown")
    except PlanPrice.DoesNotExist:
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
def purchase_plan_view(request):
    """
    Purchase a plan (regular or trial).
    Request body: {"plan_id": "uuid-string"}
    """
    plan_id = request.data.get("plan_id")

    if not plan_id:
        return Response(
            {"error": "plan_id is required"},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        plan = Plan.objects.get(id=plan_id, is_active=True)
    except Plan.DoesNotExist:
        return Response(
            {"error": "Plan not found"},
            status=status.HTTP_404_NOT_FOUND
        )

    try:
        subscription = purchase_plan(request.user, plan, request)
        serializer = SubscriptionSerializer(subscription)
        return Response({
            "subscription": serializer.data,
            "message": f"Successfully purchased {plan.name}",
        })
    except PermissionDenied as e:
        return Response(
            {"error": str(e)},
            status=status.HTTP_403_FORBIDDEN
        )
    except PlanPrice.DoesNotExist as e:
        return Response(
            {"error": str(e)},
            status=status.HTTP_400_BAD_REQUEST
        )
    except Exception as e:
        logger.exception("Error purchasing plan")
        return Response(
            {"error": "Failed to purchase plan"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
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
    """Get user's subscription history."""
    history = SubscriptionHistory.objects.filter(
        user=request.user
    ).order_by("-created_at")

    data = []
    for record in history:
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


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_trial_usage(request):
    """Get current user's trial usage status for all trial plans."""
    trial_plans = Plan.objects.filter(is_trial=True, is_active=True)
    usage_data = []

    for plan in trial_plans:
        has_used = has_user_used_trial(request.user, plan)
        usage_record = UserTrialUsage.objects.filter(
            user=request.user, 
            plan=plan
        ).first()

        usage_data.append({
            "plan_id": str(plan.id),
            "plan_name": plan.name,
            "trial_duration_days": plan.trial_duration_days,
            "already_used": has_used,
            "used_at": usage_record.used_at.isoformat() if usage_record else None,
            "expires_at": usage_record.expires_at.isoformat() if usage_record else None,
            "is_expired": usage_record.is_expired if usage_record else None,
        })

    return Response({
        "trial_usage": usage_data,
        "total_trials_available": trial_plans.count(),
        "trials_used": sum(1 for u in usage_data if u["already_used"]),
    })
