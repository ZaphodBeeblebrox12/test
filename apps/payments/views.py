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
from apps.subscriptions.services import (
    resolve_plan_price,
    get_pricing_country,
)

from .models import PaymentIntent


def get_provider_for_country(country_code: str) -> str:
    """
    Select payment provider based on country.
    IN -> Razorpay, everything else -> Stripe
    """
    if country_code and country_code.upper() == "IN":
        return PaymentIntent.Provider.RAZORPAY
    return PaymentIntent.Provider.STRIPE


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def payment_start(request):
    """
    Start a payment flow.

    Request body:
        plan_id: UUID of plan to purchase
        interval: 'monthly' or 'yearly' (default: monthly)

    Returns:
        provider: 'stripe' or 'razorpay'
        payment_intent_id: UUID of created payment intent
        checkout_url: Dummy checkout URL for now
        amount: Amount in cents
        currency: Currency code
    """
    plan_id = request.data.get("plan_id")
    interval = request.data.get("interval", "monthly")

    if not plan_id:
        return Response(
            {"detail": "plan_id is required."},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Get plan
    try:
        plan = Plan.objects.get(id=plan_id, is_active=True)
    except Plan.DoesNotExist:
        return Response(
            {"detail": "Plan not found."},
            status=status.HTTP_404_NOT_FOUND
        )

    # Resolve geo price
    try:
        resolved_price = resolve_plan_price(plan, interval, request)
    except Exception:
        return Response(
            {"detail": "No active price found for this plan and interval."},
            status=status.HTTP_404_NOT_FOUND
        )

    # Detect country and select provider
    country = get_pricing_country(request)
    provider = get_provider_for_country(country)

    # Create payment intent
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

    # Generate dummy checkout URL
    checkout_url = f"/payments/checkout/{payment_intent.id}/"

    return Response({
        "provider": provider,
        "payment_intent_id": str(payment_intent.id),
        "checkout_url": checkout_url,
        "amount": payment_intent.amount,
        "currency": payment_intent.currency,
        "plan": {
            "id": str(plan.id),
            "name": plan.name,
            "tier": plan.tier
        }
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def payment_confirm(request):
    """
    Confirm a payment (simulate success).

    Request body:
        payment_intent_id: UUID of payment intent to confirm

    Returns:
        status: 'success'
        subscription: Active subscription details
    """
    payment_intent_id = request.data.get("payment_intent_id")

    if not payment_intent_id:
        return Response(
            {"detail": "payment_intent_id is required."},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Get payment intent
    try:
        payment_intent = PaymentIntent.objects.get(
            id=payment_intent_id,
            user=request.user
        )
    except PaymentIntent.DoesNotExist:
        return Response(
            {"detail": "Payment intent not found."},
            status=status.HTTP_404_NOT_FOUND
        )

    # Check if already processed
    if payment_intent.status == PaymentIntent.Status.SUCCESS:
        # Return existing subscription
        try:
            subscription = Subscription.objects.get(
                user=request.user,
                plan=payment_intent.plan,
                is_active=True
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
        return Response(
            {"detail": "Payment has already failed."},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Simulate payment success and activate subscription
    with transaction.atomic():
        # Mark payment as success
        payment_intent.status = PaymentIntent.Status.SUCCESS
        payment_intent.save()

        # Calculate expiration (1 month or 1 year based on interval)
        interval = getattr(payment_intent.plan_price, 'interval', 'monthly')
        if interval == 'yearly':
            expires_at = timezone.now() + timezone.timedelta(days=365)
        else:
            expires_at = timezone.now() + timezone.timedelta(days=30)

        # Create subscription using existing system
        subscription = Subscription.objects.create(
            user=request.user,
            plan=payment_intent.plan,
            plan_price=payment_intent.plan_price,
            status=Subscription.Status.ACTIVE,
            is_active=True,
            started_at=timezone.now(),
            expires_at=expires_at,
            payment_provider=payment_intent.provider,
            pricing_country=payment_intent.country
        )

        # ============================================================================
        # REFERRAL COMPLETION ON PURCHASE (Phase 2)
        # Complete referral ONLY after successful payment
        # ============================================================================
        try:
            from apps.growth.services import ReferralService
            ReferralService.complete_referral_on_purchase(request.user)
        except Exception as e:
            # Log but don't break payment flow
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to complete referral on purchase: {e}")

    return Response({
        "status": "success",
        "message": "Payment confirmed and subscription activated.",
        "subscription": {
            "id": str(subscription.id),
            "plan": {
                "id": str(subscription.plan.id),
                "name": subscription.plan.name,
                "tier": subscription.plan.tier
            },
            "status": subscription.status,
            "is_active": subscription.is_active,
            "started_at": subscription.started_at,
            "expires_at": subscription.expires_at
        }
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def payment_status(request, payment_intent_id):
    """
    Get status of a payment intent.
    """
    try:
        payment_intent = PaymentIntent.objects.get(
            id=payment_intent_id,
            user=request.user
        )
    except PaymentIntent.DoesNotExist:
        return Response(
            {"detail": "Payment intent not found."},
            status=status.HTTP_404_NOT_FOUND
        )

    return Response({
        "id": str(payment_intent.id),
        "status": payment_intent.status,
        "provider": payment_intent.provider,
        "amount": payment_intent.amount,
        "currency": payment_intent.currency,
        "plan": {
            "id": str(payment_intent.plan.id),
            "name": payment_intent.plan.name
        },
        "created_at": payment_intent.created_at,
        "updated_at": payment_intent.updated_at
    })
