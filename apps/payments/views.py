"""
Minimal payment views for simple payment flow.
"""
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.subscriptions.models import Plan, Subscription
from apps.subscriptions.services import resolve_plan_price, get_pricing_country

from .models import PaymentIntent


def get_provider_for_country(country_code: str) -> str:
    """Select payment provider based on country. IN -> Razorpay, else Stripe."""
    if country_code and country_code.upper() == "IN":
        return PaymentIntent.Provider.RAZORPAY
    return PaymentIntent.Provider.STRIPE


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def payment_start(request):
    """Start a payment flow."""
    plan_id = request.data.get("plan_id")
    interval = request.data.get("interval", "monthly")

    if not plan_id:
        return Response({"detail": "plan_id is required."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        plan = Plan.objects.get(id=plan_id, is_active=True)
    except Plan.DoesNotExist:
        return Response({"detail": "Plan not found."}, status=status.HTTP_404_NOT_FOUND)

    try:
        resolved_price = resolve_plan_price(plan, interval, request)
    except Exception:
        return Response(
            {"detail": "No active price found for this plan and interval."},
            status=status.HTTP_404_NOT_FOUND
        )

    country = get_pricing_country(request)
    provider = get_provider_for_country(country)

    with transaction.atomic():
        payment_intent = PaymentIntent.objects.create(
            user=request.user,
            plan=plan,
            plan_price=resolved_price if hasattr(resolved_price, 'plan') else None,
            amount=resolved_price.price_cents,
            currency=getattr(resolved_price, 'currency', 'USD'),
            provider=provider,
            status=PaymentIntent.Status.PENDING,
            country=country or ""
        )

    checkout_url = f"/payments/checkout/{payment_intent.id}/"

    return Response({
        "provider": provider,
        "payment_intent_id": str(payment_intent.id),
        "checkout_url": checkout_url,
        "amount": payment_intent.amount,
        "currency": payment_intent.currency,
        "plan": {"id": str(plan.id), "name": plan.name, "tier": plan.tier}
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def payment_confirm(request):
    """
    Confirm a payment and activate subscription with referral reward.

    Phase 4 (Viral Mode):
    - Referral completion only triggered for paid subscriptions
    - Gift subscriptions do NOT trigger referral completion
    - Credit application happens after referral completion
    - Uses plan model as single source of truth for pricing/duration
    """
    payment_intent_id = request.data.get("payment_intent_id")

    if not payment_intent_id:
        return Response({"detail": "payment_intent_id is required."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        payment_intent = PaymentIntent.objects.get(id=payment_intent_id, user=request.user)
    except PaymentIntent.DoesNotExist:
        return Response({"detail": "Payment intent not found."}, status=status.HTTP_404_NOT_FOUND)

    if payment_intent.status == PaymentIntent.Status.SUCCESS:
        try:
            subscription = Subscription.objects.get(
                user=request.user, plan=payment_intent.plan, is_active=True
            )
            return Response({
                "status": "success",
                "message": "Payment already processed.",
                "subscription": {
                    "id": str(subscription.id),
                    "plan": subscription.plan.name,
                    "status": subscription.status,
                    "expires_at": subscription.expires_at
                }
            })
        except Subscription.DoesNotExist:
            pass

    if payment_intent.status == PaymentIntent.Status.FAILED:
        return Response({"detail": "Payment has already failed."}, status=status.HTTP_400_BAD_REQUEST)

    # Get plan as single source of truth
    plan = payment_intent.plan

    with transaction.atomic():
        payment_intent.status = PaymentIntent.Status.SUCCESS
        payment_intent.save()

        # Use plan model as single source of truth for duration
        # plan.duration_days should be defined in your Plan model
        plan_duration_days = getattr(plan, 'duration_days', 30)  # fallback if not defined

        expires_at = timezone.now() + timezone.timedelta(days=plan_duration_days)

        subscription = Subscription.objects.create(
            user=request.user,
            plan=plan,
            plan_price=payment_intent.plan_price,
            status=Subscription.Status.ACTIVE,
            is_active=True,
            started_at=timezone.now(),
            expires_at=expires_at,
            payment_provider=payment_intent.provider,
            pricing_country=payment_intent.country
        )

        # ================================================================
        # REFERRAL COMPLETION AND CREDIT APPLICATION (Phase 4 - Viral Mode)
        # ================================================================
        # Order: 1. Create subscription, 2. Complete referral, 3. Apply credit
        # Uses plan model as single source of truth for pricing

        try:
            from apps.growth.services import ReferralService, SubscriptionCreditService

            # 2. Complete referral (creates pending reward, not available yet)
            # Only for paid subscriptions (amount > 0)
            if payment_intent.amount > 0:
                ReferralService.complete_referral_on_purchase(
                    user=request.user,
                    purchase_amount_cents=payment_intent.amount,
                    currency=payment_intent.currency,
                    triggering_subscription=subscription  # Explicit link for refund checking
                )

            # 3. Apply existing credits (from previous rewards)
            # Uses plan as single source of truth
            credit_result = SubscriptionCreditService.apply_credit_to_subscription(
                user=request.user,
                subscription=subscription,
                plan_price_cents=plan.price_cents,  # Plan is source of truth
                plan_duration_days=plan_duration_days  # From plan model
            )

            if credit_result:
                # Update expires_at if credit was applied
                subscription.refresh_from_db()

        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to process referral/credit: {e}")
            # Don't fail the payment if referral/credit fails

    return Response({
        "status": "success",
        "message": "Payment confirmed and subscription activated.",
        "subscription": {
            "id": str(subscription.id),
            "plan": {"id": str(subscription.plan.id), "name": subscription.plan.name, "tier": subscription.plan.tier},
            "status": subscription.status,
            "is_active": subscription.is_active,
            "started_at": subscription.started_at,
            "expires_at": subscription.expires_at
        }
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def payment_status(request, payment_intent_id):
    """Get status of a payment intent."""
    try:
        payment_intent = PaymentIntent.objects.get(id=payment_intent_id, user=request.user)
    except PaymentIntent.DoesNotExist:
        return Response({"detail": "Payment intent not found."}, status=status.HTTP_404_NOT_FOUND)

    return Response({
        "id": str(payment_intent.id),
        "status": payment_intent.status,
        "provider": payment_intent.provider,
        "amount": payment_intent.amount,
        "currency": payment_intent.currency,
        "plan": {"id": str(payment_intent.plan.id), "name": payment_intent.plan.name},
        "created_at": payment_intent.created_at,
        "updated_at": payment_intent.updated_at
    })
