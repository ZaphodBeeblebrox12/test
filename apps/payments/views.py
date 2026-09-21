import datetime
from datetime import timedelta

"""
Minimal payment views for simple payment flow.
"""
import logging
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.subscriptions.models import Plan, Subscription, SubscriptionHistory
from apps.subscriptions.services import resolve_plan_price, get_pricing_country, split_resolved_price

from . import providers
from .models import PaymentIntent

from apps.growth.services.referrals import ReferralService
from apps.growth.models import Referral

logger = logging.getLogger(__name__)


def get_provider_for_country(country_code: str) -> str:
    """Select payment provider based on country. IN -> Razorpay, else Stripe."""
    if country_code and country_code.upper() == "IN":
        return PaymentIntent.Provider.RAZORPAY
    return PaymentIntent.Provider.STRIPE


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def payment_start(request):
    """Start a payment flow.

    Accepts POST (API clients) or GET (the landing-page CTA redirect after
    signup/login, where plan_id and interval arrive as query params).
    """
    if request.method == "GET":
        plan_id = request.GET.get("plan_id")
        interval = request.GET.get("interval", "monthly")
    else:
        plan_id = request.data.get("plan_id")
        interval = request.data.get("interval", "monthly")
    if not plan_id:
        return Response({"detail": "plan_id required"},
                        status=status.HTTP_400_BAD_REQUEST)

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

    # G4: capture the immutable base catalog price before any discount.
    base_amount = resolved_price.price_cents
    # ========== NEW: Apply referral discount ==========
    discount_info = ReferralService.get_checkout_discount(request.user, base_amount)
    final_amount = discount_info["final_amount_cents"]
    applied_referral = discount_info.get("referral")

    # ========== G4: Apply coupon (after referral discount) ==========
    coupon_code = request.data.get("coupon_code") or request.GET.get("coupon_code", "")
    if coupon_code:
        from apps.promotions.services.coupons import apply_coupon, CouponError
        try:
            final_amount, applied_coupon, coupon_discount = apply_coupon(
                user=request.user, plan=plan, base_amount_cents=final_amount,
                code=coupon_code, has_referral_discount=applied_referral is not None)
        except CouponError as exc:
            return Response({"detail": f"coupon: {exc.reason}"},
                            status=status.HTTP_400_BAD_REQUEST)
    else:
        applied_coupon, coupon_discount = None, 0

    with transaction.atomic():
        fk = split_resolved_price(resolved_price)
        payment_intent = PaymentIntent.objects.create(
            user=request.user,
            plan=plan,
            plan_price=fk["plan_price"],
            geo_plan_price=fk["geo_plan_price"],
            amount=final_amount,
            currency=fk["price_currency"],
            provider=provider,
            status=PaymentIntent.Status.PENDING,
            country=country or "",
            applied_referral_discount=applied_referral,
            applied_coupon_code=(applied_coupon.code if applied_coupon else ""),
            coupon_discount_cents=coupon_discount,
            base_amount_cents=base_amount,  # pre-discount catalog price
        )

    success_url = f"{settings.SITE_BASE_URL}/confirm-page/{payment_intent.id}/"
    cancel_url = f"{settings.SITE_BASE_URL}/checkout/{payment_intent.id}/?canceled=1"
    try:
        checkout = providers.create_hosted_checkout(
            payment_intent, success_url=success_url, cancel_url=cancel_url)
    except providers.ProviderError as exc:
        payment_intent.delete()
        return Response({"detail": f"payment provider error: {exc}"},
                        status=status.HTTP_502_BAD_GATEWAY)
    payment_intent.provider_reference = checkout.provider_reference
    payment_intent.save(update_fields=["provider_reference"])

    checkout_url = f"/checkout/{payment_intent.id}/"

    return Response({
        "provider": provider,
        "payment_intent_id": str(payment_intent.id),
        "checkout_url": checkout_url,
        "amount": payment_intent.amount,
        "original_amount": resolved_price.price_cents,
        "discount_applied": applied_referral is not None,
        "discount_percent": discount_info["discount_percent"] if discount_info["has_discount"] else 0,
        "currency": payment_intent.currency,
        "plan": {"id": str(plan.id), "name": plan.name, "tier": plan.tier}
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def payment_confirm(request):
    """Verify-first confirmation; the intent is claimed only after the
    provider reports the payment paid. Never trust the browser alone."""
    payment_intent_id = request.data.get("payment_intent_id")
    if not payment_intent_id:
        return Response({"detail": "payment_intent_id required"},
                        status=status.HTTP_400_BAD_REQUEST)
    payment_intent = get_object_or_404(
        PaymentIntent, id=payment_intent_id, user=request.user)

    if payment_intent.status == PaymentIntent.Status.SUCCESS:
        return Response(_existing_result(request.user, payment_intent))

    try:
        verified = providers.verify_provider_payment(payment_intent)
    except providers.ProviderError:
        verified = None
    if verified is None:
        return Response({"status": "pending",
                         "payment_intent_id": str(payment_intent.id)})
    if verified is False:
        PaymentIntent.objects.filter(
            pk=payment_intent.pk,
            status=PaymentIntent.Status.PENDING).update(
                status=PaymentIntent.Status.FAILED)
        from apps.events.models import record_event
        record_event(
            "payment.failed",
            dedupe_key=f"payment.failed:{payment_intent.pk}",
            user_id=payment_intent.user_id,
            object_ref=f"payment:{payment_intent.pk}",
            payload={"payment_intent_id": str(payment_intent.pk),
                     "amount": payment_intent.amount, "currency": payment_intent.currency},
        )
        return Response({"status": "failed",
                         "payment_intent_id": str(payment_intent.id)})

    claimed = PaymentIntent.objects.filter(
        pk=payment_intent.pk,
        status=PaymentIntent.Status.PENDING).update(
            status=PaymentIntent.Status.SUCCESS)
    if not claimed:
        return Response(_existing_result(request.user, payment_intent))

    with transaction.atomic():
        plan = payment_intent.plan
        price_source = payment_intent.plan_price or payment_intent.geo_plan_price
        interval = getattr(price_source, "interval", "monthly")
        interval_days = {"monthly": 30, "quarterly": 90, "yearly": 365}.get(
            interval, 30)
        if payment_intent.applied_referral_discount:
            referral = (Referral.objects.select_for_update()
                        .filter(id=payment_intent.applied_referral_discount_id)
                        .first())
            if referral and not referral.discount_used:
                referral.mark_reward(reward_user=request.user,
                                     reward_reason="referral_reward")
                SubscriptionCreditService().apply_credit_to_subscription(
                    user=request.user, plan=plan, source="referral",
                    metadata={"referral_id": str(referral.id)},
                    plan_duration_days=interval_days)
        subscription = Subscription.objects.create(
            user=request.user, plan=plan,
            plan_price=payment_intent.plan_price,
            geo_plan_price=payment_intent.geo_plan_price,
            status=Subscription.Status.ACTIVE, is_active=True,
            started_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=interval_days),
            price_cents=payment_intent.amount,
            price_currency=payment_intent.currency,
            payment_provider=payment_intent.provider,
            pricing_country=payment_intent.country)
        SubscriptionHistory.objects.create(
            subscription=subscription, user=request.user,
            event_type=SubscriptionHistory.EventType.CREATED,
            new_plan_id=plan.id, new_status=Subscription.Status.ACTIVE)

    return Response({"status": "success",
                     "subscription_id": str(subscription.id),
                     "payment_intent_id": str(payment_intent.id),
                     "expires_at": subscription.expires_at})


def _existing_result(user, payment_intent):
    subscription = Subscription.objects.filter(
        user=user, plan=payment_intent.plan, is_active=True).first()
    return {"status": "success", "already_processed": True,
            "subscription_id": str(subscription.id) if subscription else None,
            "payment_intent_id": str(payment_intent.id)}


class CheckoutPageView(APIView):
    """Bridge page: routes the customer to the provider-hosted checkout."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        return _checkout_response(request, pk)


def _checkout_response(request, pk):
    intent = get_object_or_404(PaymentIntent, pk=pk, user=request.user)
    context = {"intent": intent, "plan": intent.plan, "amount": intent.amount,
               "currency": intent.currency,
               "canceled": bool(request.GET.get("canceled"))}
    if intent.provider == "razorpay":
        context["razorpay_key_id"] = settings.RAZORPAY_KEY_ID
        context["razorpay_order_id"] = intent.provider_reference
    else:
        url = None
        if intent.provider_reference:
            try:
                url = providers.get_stripe_checkout_url(intent.provider_reference)
            except providers.ProviderError:
                url = None
        context["stripe_checkout_url"] = url
    return render(request, "payments/checkout.html", context)


class ConfirmPageView(APIView):
    """Provider return URL: verify server-side, then render the result."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        return _confirm_response(request, pk)


def _confirm_response(request, pk):
    intent = get_object_or_404(PaymentIntent, pk=pk, user=request.user)
    """Provider return URL: minimal server-side verification, then result page."""
    intent = get_object_or_404(PaymentIntent, pk=pk, user=request.user)
    result = {"status": "pending", "intent": intent}
    if intent.status == PaymentIntent.Status.SUCCESS:
        result["status"] = "success"
    else:
        try:
            verified = providers.verify_provider_payment(intent)
        except providers.ProviderError:
            verified = None
        if verified is True:
            PaymentIntent.objects.filter(
                pk=intent.pk, status=PaymentIntent.Status.PENDING).update(
                    status=PaymentIntent.Status.SUCCESS)
            intent.refresh_from_db()
            result["status"] = "success" if (
                intent.status == PaymentIntent.Status.SUCCESS) else "pending"
        elif verified is False:
            PaymentIntent.objects.filter(
                pk=intent.pk, status=PaymentIntent.Status.PENDING).update(
                    status=PaymentIntent.Status.FAILED)
            result["status"] = "failed"
    return render(request, "payments/confirm.html", result)


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