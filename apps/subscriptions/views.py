"""
Subscription API views with geo pricing and REGION-LOCKED trial support.
Trials ONLY show if a GeoPlanPrice exists for the user's specific country/region.
"""
import logging

from django.utils import timezone
from django.core.exceptions import PermissionDenied
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
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
    cancel_subscription,
    resolve_plan_price,
    get_pricing_country,
    get_region_for_country,
    purchase_plan,
    has_user_used_trial,
    format_price,
    get_geo_price_for_trial,
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
    """
    List active plans with geo-specific pricing.
    TRIALS ARE REGION-LOCKED: Only show if GeoPlanPrice exists for user's region.
    """
    country = get_pricing_country(request)
    region = get_region_for_country(country) if country else None
    user = request.user if request.user.is_authenticated else None

    plans = Plan.objects.filter(is_active=True).order_by("display_order")
    data = []

    for plan in plans:
        # === TRIAL PLAN LOGIC (Region-Locked) ===
        if plan.is_trial:
            geo_price = get_geo_price_for_trial(plan, country)

            # SKIP: No geo price for this region - hide trial completely
            if not geo_price:
                logger.info(f"Hiding trial '{plan.name}' for {country or 'unknown'} - no geo price")
                continue

            # SHOW: Build trial data with geo-specific price
            plan_data = PlanSerializer(plan).data
            plan_data["is_trial"] = True
            plan_data["trial_duration_days"] = plan.trial_duration_days
            plan_data["already_used"] = has_user_used_trial(user, plan) if user else False
            plan_data["price_cents"] = geo_price.price_cents
            plan_data["currency"] = geo_price.currency
            plan_data["price_display"] = format_price(geo_price.price_cents, geo_price.currency)
            plan_data["geo_pricing"] = True
            data.append(plan_data)
            continue

        # === REGULAR PLAN LOGIC (non-trial) ===
        plan_data = PlanSerializer(plan).data
        plan_data["is_trial"] = False
        plan_data["already_used"] = False

        try:
            # FIXED: Pass interval (default 'monthly') to resolve_plan_price
            interval = request.GET.get("interval", "monthly")
            if interval not in [c[0] for c in PlanPrice.Interval.choices]:
                interval = "monthly"
            price_obj = resolve_plan_price(plan, interval, request)
            plan_data["price_cents"] = price_obj.price_cents
            plan_data["currency"] = price_obj.currency
            plan_data["price_display"] = format_price(price_obj.price_cents, price_obj.currency)
            plan_data["geo_pricing"] = isinstance(price_obj, GeoPlanPrice)
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
    """
    Get plan details with geo-specific pricing.
    For trials: returns 404 if no geo price exists for user's region.
    """
    try:
        plan = Plan.objects.get(id=plan_id, is_active=True)
    except Plan.DoesNotExist:
        return Response(
            {"error": "Plan not found"},
            status=status.HTTP_404_NOT_FOUND
        )

    country = get_pricing_country(request)

    # === TRIAL REGION CHECK ===
    if plan.is_trial:
        geo_price = get_geo_price_for_trial(plan, country)

        # Return 404 if trial not available in this region
        if not geo_price:
            return Response(
                {"error": "This trial is not available in your region"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Return trial with geo price
        plan_data = PlanSerializer(plan).data
        plan_data["is_trial"] = True
        plan_data["trial_duration_days"] = plan.trial_duration_days
        plan_data["already_used"] = has_user_used_trial(request.user, plan) if request.user.is_authenticated else False
        plan_data["price_cents"] = geo_price.price_cents
        plan_data["currency"] = geo_price.currency
        plan_data["price_display"] = format_price(geo_price.price_cents, geo_price.currency)
        plan_data["geo_pricing"] = True

        return Response({
            "plan": plan_data,
            "user_country": country,
        })

    # === REGULAR PLAN ===
    plan_data = PlanSerializer(plan).data
    plan_data["is_trial"] = False
    plan_data["already_used"] = False

    try:
        # FIXED: Pass interval (default 'monthly') to resolve_plan_price
        price_obj = resolve_plan_price(plan, "monthly", request)
        plan_data["price_cents"] = price_obj.price_cents
        plan_data["currency"] = price_obj.currency
        plan_data["price_display"] = format_price(price_obj.price_cents, price_obj.currency)
        plan_data["geo_pricing"] = isinstance(price_obj, GeoPlanPrice)
        # Optionally include breakdown if needed
        plan_data["price_breakdown"] = None  # or implement breakdown logic
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
    For trials: enforces region-lock (verifies geo price exists).
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

    # === REGION CHECK FOR TRIALS ===
    if plan.is_trial:
        country = get_pricing_country(request)
        geo_price = get_geo_price_for_trial(plan, country)

        if not geo_price:
            return Response(
                {"error": "This trial is not available in your region"},
                status=status.HTTP_403_FORBIDDEN
            )

    if not plan.is_trial:
        # PAID plans must go through the verified payment flow: the API
        # response carries the payment start URL; the client POSTs there to
        # create the PaymentIntent and provider checkout.  Never activate a
        # paid subscription from this endpoint.
        from django.urls import reverse
        return Response({
            "requires_payment": True,
            "plan_id": str(plan.id),
            "payment_start_url": reverse("payment-start"),
        }, status=status.HTTP_402_PAYMENT_REQUIRED)

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

class CancelSubscriptionView(APIView):
    """POST /subscriptions/cancel/ — cancel the caller's ACTIVE subscription.

    Semantics (see services.cancel_subscription): access ends IMMEDIATELY;
    repeated calls are idempotent no-ops; no active subscription -> 404.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        # Idempotent cancel: look at the user's LATEST subscription whatever
        # its status.  ACTIVE -> cancel now.  CANCELED -> already done (200,
        # no duplicate history/reconcile).  EXPIRED -> gone (409).  None -> 404.
        subscription = Subscription.objects.filter(
            user=request.user).order_by("-created_at").first()
        if subscription is None:
            return Response(
                {"detail": "No subscription to cancel."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if subscription.status == Subscription.Status.CANCELED:
            return Response({
                "status": "canceled",
                "subscription_id": str(subscription.id),
                "already_processed": True,
            })
        if subscription.status != Subscription.Status.ACTIVE:
            return Response({
                "status": subscription.status,
                "subscription_id": str(subscription.id),
                "detail": "Subscription is not active.",
            }, status=status.HTTP_409_CONFLICT)
        if cancel_subscription(subscription, actor="user"):
            subscription.refresh_from_db()
            return Response({
                "status": "canceled",
                "subscription_id": str(subscription.id),
                "canceled_at": subscription.canceled_at,
            })
        # Lost a concurrent race: another request canceled it first.
        subscription.refresh_from_db()
        return Response({
            "status": subscription.status,
            "subscription_id": str(subscription.id),
            "already_processed": True,
        })
