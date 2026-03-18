"""
Subscription API views - Phase 3B.
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response

from .models import Plan, Subscription, PlanDiscount, GiftSubscription
from .serializers import (
    PlanSerializer, SubscriptionSerializer, 
    PlanDiscountSerializer, GiftSubscriptionSerializer
)
from .services import SubscriptionService, BanEnforcementService


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def plan_list(request):
    """List all active subscription plans with their pricing."""
    plans = Plan.objects.filter(
        is_active=True
    ).prefetch_related(
        "prices", "discounts"
    ).order_by("display_order", "tier")

    serializer = PlanSerializer(plans, many=True)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_subscription(request):
    """Get the current user's active subscription."""
    try:
        subscription = Subscription.objects.select_related(
            "plan", "plan_price", "applied_discount"
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


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def upgrade_subscription(request):
    """
    Upgrade to a higher tier plan.

    Request body:
    {
        "plan_id": "uuid",
        "price_id": "uuid",
        "discount_code": "optional"
    }
    """
    # Check ban
    if request.user.is_banned:
        return Response(
            {"error": "Banned users cannot upgrade subscriptions"},
            status=status.HTTP_403_FORBIDDEN
        )

    plan_id = request.data.get("plan_id")
    price_id = request.data.get("price_id")
    discount_code = request.data.get("discount_code")

    if not plan_id or not price_id:
        return Response(
            {"error": "plan_id and price_id are required"},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        target_plan = Plan.objects.get(id=plan_id, is_active=True)
        target_price = target_plan.prices.get(id=price_id, is_active=True)
    except (Plan.DoesNotExist, PlanPrice.DoesNotExist):
        return Response(
            {"error": "Invalid plan or price"},
            status=status.HTTP_404_NOT_FOUND
        )

    # Apply discount if provided
    discount = None
    if discount_code:
        try:
            discount = PlanDiscount.objects.get(code=discount_code.upper())
            if not discount.is_valid():
                return Response(
                    {"error": "Invalid or expired discount code"},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except PlanDiscount.DoesNotExist:
            return Response(
                {"error": "Invalid discount code"},
                status=status.HTTP_404_NOT_FOUND
            )

    try:
        new_sub, credit = SubscriptionService.upgrade_subscription(
            request.user, target_plan, target_price, discount
        )
        return Response({
            "subscription": SubscriptionSerializer(new_sub).data,
            "prorated_credit_cents": credit,
            "message": "Upgrade successful"
        })
    except Exception as e:
        return Response(
            {"error": str(e)},
            status=status.HTTP_400_BAD_REQUEST
        )


@api_view(["POST"])
@permission_classes([IsAdminUser])
def admin_grant_subscription(request):
    """
    Admin endpoint to grant a subscription.

    Request body:
    {
        "user_id": "uuid",
        "plan_id": "uuid",
        "duration_days": 30,
        "reason": "optional reason",
        "expires_at": "optional iso datetime"
    }
    """
    from django.utils.dateparse import parse_datetime

    user_id = request.data.get("user_id")
    plan_id = request.data.get("plan_id")
    duration_days = request.data.get("duration_days", 30)
    reason = request.data.get("reason", "")
    expires_at_str = request.data.get("expires_at")

    if not user_id or not plan_id:
        return Response(
            {"error": "user_id and plan_id are required"},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        target_user = User.objects.get(id=user_id)
        plan = Plan.objects.get(id=plan_id)
    except (User.DoesNotExist, Plan.DoesNotExist):
        return Response(
            {"error": "Invalid user or plan"},
            status=status.HTTP_404_NOT_FOUND
        )

    expires_at = parse_datetime(expires_at_str) if expires_at_str else None

    try:
        subscription = SubscriptionService.admin_grant_subscription(
            request.user, target_user, plan, 
            duration_days=duration_days,
            reason=reason,
            override_expires=expires_at
        )
        return Response({
            "subscription": SubscriptionSerializer(subscription).data,
            "message": "Subscription granted successfully"
        })
    except Exception as e:
        return Response(
            {"error": str(e)},
            status=status.HTTP_400_BAD_REQUEST
        )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_gift(request):
    """
    Create a gift subscription.

    Request body:
    {
        "plan_id": "uuid",
        "duration_days": 30,
        "recipient_email": "optional",
        "message": "optional"
    }
    """
    if request.user.is_banned:
        return Response(
            {"error": "Banned users cannot create gifts"},
            status=status.HTTP_403_FORBIDDEN
        )

    plan_id = request.data.get("plan_id")
    duration_days = request.data.get("duration_days", 30)
    recipient_email = request.data.get("recipient_email", "")
    message = request.data.get("message", "")

    if not plan_id:
        return Response(
            {"error": "plan_id is required"},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        plan = Plan.objects.get(id=plan_id, is_active=True)
    except Plan.DoesNotExist:
        return Response(
            {"error": "Invalid plan"},
            status=status.HTTP_404_NOT_FOUND
        )

    try:
        gift = SubscriptionService.create_gift(
            request.user, plan, duration_days, recipient_email, message
        )
        return Response({
            "gift": GiftSubscriptionSerializer(gift).data,
            "message": "Gift created successfully"
        })
    except Exception as e:
        return Response(
            {"error": str(e)},
            status=status.HTTP_400_BAD_REQUEST
        )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def redeem_gift(request):
    """
    Redeem a gift subscription.

    Request body:
    {
        "code": "GIFT-CODE"
    }
    """
    if request.user.is_banned:
        return Response(
            {"error": "Banned users cannot redeem gifts"},
            status=status.HTTP_403_FORBIDDEN
        )

    code = request.data.get("code", "").upper().strip()

    if not code:
        return Response(
            {"error": "code is required"},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        gift = GiftSubscription.objects.get(code=code)
        subscription = gift.redeem(request.user)
        return Response({
            "subscription": SubscriptionSerializer(subscription).data,
            "message": "Gift redeemed successfully"
        })
    except GiftSubscription.DoesNotExist:
        return Response(
            {"error": "Invalid gift code"},
            status=status.HTTP_404_NOT_FOUND
        )
    except Exception as e:
        return Response(
            {"error": str(e)},
            status=status.HTTP_400_BAD_REQUEST
        )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def validate_discount(request):
    """
    Validate a discount code.

    Query params:
    ?code=DISCOUNT-CODE&plan_id=uuid
    """
    code = request.query_params.get("code", "").upper().strip()
    plan_id = request.query_params.get("plan_id")

    if not code:
        return Response(
            {"error": "code is required"},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        discount = PlanDiscount.objects.get(code=code)

        if not discount.is_valid():
            return Response({
                "valid": False,
                "error": "Discount is not valid or has expired"
            })

        # Check if applicable to plan
        if plan_id and discount.applicable_plans.exists():
            if not discount.applicable_plans.filter(id=plan_id).exists():
                return Response({
                    "valid": False,
                    "error": "Discount not applicable to this plan"
                })

        return Response({
            "valid": True,
            "discount": PlanDiscountSerializer(discount).data
        })
    except PlanDiscount.DoesNotExist:
        return Response({
            "valid": False,
            "error": "Invalid discount code"
        })


@api_view(["GET"])
@permission_classes([IsAdminUser])
def list_unprocessed_events(request):
    """Admin endpoint to list unprocessed subscription events."""
    from .models import SubscriptionEvent

    events = SubscriptionEvent.objects.filter(
        processed=False
    ).select_related("user", "subscription")[:100]

    data = [{
        "id": str(e.id),
        "event_type": e.event_type,
        "user": e.user.username,
        "subscription_id": str(e.subscription_id) if e.subscription else None,
        "data": e.data,
        "created_at": e.created_at.isoformat()
    } for e in events]

    return Response({"events": data, "count": len(data)})
