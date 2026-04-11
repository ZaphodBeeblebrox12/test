"""
Subscription API views with geo pricing support.
"""
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response

from .models import Plan, Subscription, GiftSubscription, UpgradeHistory, PlanPrice, GeoPlanPrice   # added PlanPrice, GeoPlanPrice
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


# =============================================================================
# EXISTING VIEWS (unchanged from original)
# =============================================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def plan_list(request):
    """
    List all active subscription plans with their pricing.
    Returns:
        List of plans with nested prices.
    """
    plans = Plan.objects.filter(
        is_active=True
    ).prefetch_related(
        "prices"
    ).order_by("display_order", "tier")

    serializer = PlanSerializer(plans, many=True)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_subscription(request):
    """
    Get the current user's active subscription.
    Returns:
        Active subscription details or 404 if none exists.
    """
    try:
        subscription = Subscription.objects.select_related(
            "plan",
            "plan_price"
        ).prefetch_related(
            "plan__prices"
        ).get(
            user=request.user,
            is_active=True,
            status=Subscription.Status.ACTIVE
        )
        serializer = SubscriptionSerializer(subscription)
        return Response(serializer.data)
    except Subscription.DoesNotExist:
        return Response(
            {"detail": "No active subscription found."},
            status=status.HTTP_404_NOT_FOUND
        )


# =============================================================================
# NEW GEO PRICING VIEWS (Phase 3B)
# =============================================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def plan_list_geo(request):
    """
    List all active subscription plans with geo-resolved pricing.
    Query params:
        interval: 'monthly' or 'yearly' (required)
        test_country: Override country for testing (DEBUG only)
    Returns:
        List of plans with resolved geo pricing.
    """
    interval = request.GET.get("interval", "monthly")

    plans = Plan.objects.filter(
        is_active=True
    ).order_by("display_order", "tier")

    country = get_pricing_country(request)
    region = get_region_for_country(country) if country else None

    data = []
    for plan in plans:
        try:
            resolved_price = resolve_plan_price(plan, interval, request)
            price_data = {
                "id": str(resolved_price.id),
                "interval": getattr(resolved_price, 'interval', interval),
                "price_cents": resolved_price.price_cents,
                "price_dollars": resolved_price.price_cents / 100,
                "currency": getattr(resolved_price, 'currency', 'USD'),
                "is_geo_price": isinstance(resolved_price, GeoPlanPrice),
            }
        except PlanPrice.DoesNotExist:
            price_data = None

        data.append({
            "id": str(plan.id),
            "tier": plan.tier,
            "name": plan.name,
            "description": plan.description,
            "resolved_price": price_data,
        })

    return Response({
        "plans": data,
        "geo": {
            "country": country,
            "region": region,
            "interval": interval,
            "is_test_override": bool(request.GET.get('test_country')) and getattr(request, 'user', None) and request.user.is_staff
        }
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def plan_detail_geo(request, plan_id):
    """
    Get plan details with resolved geo pricing.
    Query params:
        interval: 'monthly' or 'yearly' (required)
    """
    interval = request.GET.get("interval", "monthly")

    try:
        plan = Plan.objects.get(id=plan_id, is_active=True)
    except Plan.DoesNotExist:
        return Response(
            {"detail": "Plan not found."},
            status=status.HTTP_404_NOT_FOUND
        )

    try:
        resolved_price = resolve_plan_price(plan, interval, request)
        price_data = {
            "id": str(resolved_price.id),
            "interval": getattr(resolved_price, 'interval', interval),
            "price_cents": resolved_price.price_cents,
            "price_dollars": resolved_price.price_cents / 100,
            "currency": getattr(resolved_price, 'currency', 'USD'),
            "country": getattr(resolved_price, 'country', None),
            "region": getattr(resolved_price, 'region', None),
        }
    except PlanPrice.DoesNotExist:
        price_data = None

    serializer = PlanSerializer(plan)

    return Response({
        "plan": serializer.data,
        "resolved_price": price_data,
        "geo": {
            "country": get_pricing_country(request),
            "region": get_region_for_country(get_pricing_country(request))
        }
    })


# =============================================================================
# GIFT SUBSCRIPTION VIEWS (Phase 3B)
# =============================================================================

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_gift(request):
    """
    Create a gift subscription.
    Request body:
        plan_id: UUID of plan to gift
        duration_days: Days the gift lasts (default 30)
        message: Optional message
    """
    plan_id = request.data.get("plan_id")
    duration_days = request.data.get("duration_days", 30)
    message = request.data.get("message", "")

    if not plan_id:
        return Response(
            {"detail": "plan_id is required."},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        plan = Plan.objects.get(id=plan_id, is_active=True)
    except Plan.DoesNotExist:
        return Response(
            {"detail": "Plan not found."},
            status=status.HTTP_404_NOT_FOUND
        )

    gift = create_gift_subscription(
        from_user=request.user,
        plan=plan,
        duration_days=duration_days,
        message=message,
        request=request
    )

    return Response({
        "gift": {
            "id": str(gift.id),
            "gift_code": gift.gift_code,
            "plan": plan.name,
            "duration_days": gift.duration_days,
            "expires_at": gift.expires_at,
            "message": gift.message
        }
    }, status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def claim_gift(request):
    """
    Claim a gift subscription.
    Request body:
        gift_code: The gift code to claim
    """
    gift_code = request.data.get("gift_code")

    if not gift_code:
        return Response(
            {"detail": "gift_code is required."},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        subscription = claim_gift_subscription(
            gift_code=gift_code,
            to_user=request.user,
            request=request
        )

        return Response({
            "subscription": SubscriptionSerializer(subscription).data,
            "message": "Gift subscription claimed successfully!"
        })
    except GiftSubscription.DoesNotExist:
        return Response(
            {"detail": "Invalid gift code."},
            status=status.HTTP_404_NOT_FOUND
        )
    except ValueError as e:
        return Response(
            {"detail": str(e)},
            status=status.HTTP_400_BAD_REQUEST
        )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_gifts(request):
    """List gifts created by the current user."""
    gifts = GiftSubscription.objects.filter(
        from_user=request.user
    ).order_by("-created_at")

    serializer = GiftSubscriptionSerializer(gifts, many=True)
    return Response({"gifts": serializer.data})


# =============================================================================
# ADMIN VIEWS (Phase 3B)
# =============================================================================

@api_view(["POST"])
@permission_classes([IsAdminUser])
def admin_grant_subscription(request):
    """
    Admin endpoint to grant a subscription to a user.
    Request body:
        user_id: UUID of user to grant subscription
        plan_id: UUID of plan
        duration_days: Days the subscription lasts
        reason: Reason for grant
    """
    user_id = request.data.get("user_id")
    plan_id = request.data.get("plan_id")
    duration_days = request.data.get("duration_days", 30)
    reason = request.data.get("reason", "")

    if not user_id or not plan_id:
        return Response(
            {"detail": "user_id and plan_id are required."},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        from apps.accounts.models import User
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response(
            {"detail": "User not found."},
            status=status.HTTP_404_NOT_FOUND
        )

    try:
        plan = Plan.objects.get(id=plan_id, is_active=True)
    except Plan.DoesNotExist:
        return Response(
            {"detail": "Plan not found."},
            status=status.HTTP_404_NOT_FOUND
        )

    subscription = grant_subscription_by_admin(
        user=user,
        plan=plan,
        granted_by=request.user,
        duration_days=duration_days,
        reason=reason,
        request=request
    )

    return Response({
        "subscription": SubscriptionSerializer(subscription).data,
        "message": f"Subscription granted to {user.username}"
    }, status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def admin_start_trial(request):
    """
    Admin endpoint to start a trial for a user.
    Request body:
        user_id: UUID of user
        plan_id: UUID of plan
        days: Trial duration (default 14)
    """
    user_id = request.data.get("user_id")
    plan_id = request.data.get("plan_id")
    days = request.data.get("days", 14)

    if not user_id or not plan_id:
        return Response(
            {"detail": "user_id and plan_id are required."},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        from apps.accounts.models import User
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response(
            {"detail": "User not found."},
            status=status.HTTP_404_NOT_FOUND
        )

    try:
        plan = Plan.objects.get(id=plan_id, is_active=True)
    except Plan.DoesNotExist:
        return Response(
            {"detail": "Plan not found."},
            status=status.HTTP_404_NOT_FOUND
        )

    subscription = start_trial(
        user=user,
        plan=plan,
        days=days,
        request=request
    )

    return Response({
        "subscription": SubscriptionSerializer(subscription).data,
        "message": f"Trial started for {user.username}"
    }, status=status.HTTP_201_CREATED)


# =============================================================================
# HISTORY VIEWS (Phase 3B)
# =============================================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def subscription_history(request):
    """Get subscription history for current user."""
    history = SubscriptionHistory.objects.filter(
        user=request.user
    ).select_related('subscription').order_by("-created_at")[:50]

    return Response({
        "history": [
            {
                "id": str(h.id),
                "event_type": h.event_type,
                "subscription_id": str(h.subscription_id) if h.subscription else None,
                "previous_plan_id": str(h.previous_plan_id) if h.previous_plan_id else None,
                "new_plan_id": str(h.new_plan_id) if h.new_plan_id else None,
                "previous_status": h.previous_status,
                "new_status": h.new_status,
                "notes": h.notes,
                "created_at": h.created_at
            }
            for h in history
        ]
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def upgrade_history_list(request):
    """Get upgrade history for current user."""
    history = UpgradeHistory.objects.filter(
        user=request.user
    ).select_related('from_plan', 'to_plan').order_by("-created_at")[:50]

    serializer = UpgradeHistorySerializer(history, many=True)
    return Response({"upgrades": serializer.data})