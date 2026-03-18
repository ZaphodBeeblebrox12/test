"""
Subscription API views (read-only for Phase 3A).
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Plan, Subscription
from .serializers import PlanSerializer, SubscriptionSerializer


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
