"""Minimal payment views for simple payment flow."""
import logging
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, render
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.subscriptions.models import Plan, Subscription
from apps.subscriptions.services import resolve_plan_price, get_pricing_country, split_resolved_price

from . import providers
from .models import PaymentIntent
from .notifications import notify_payment_failed
from .services import activate_paid_subscription

from apps.growth.services.referrals import ReferralService

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

    base_amount = resolved_price.price_cents
    discount_info = ReferralService.get_checkout_discount(request.user, base_amount)
    final_amount = discount_info["final_amount_cents"]
    applied_referral = discount_info.get("referral")

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
            base_amount_cents=base_amount,
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
    """Verify-first confirmation; delegates activation to the shared service.

    The browser flow never claims the intent or creates subscription-side
    effects itself — `activate_paid_subscription` is the single authoritative
    activation path (also used by webhook processing)."""
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
        # Claim PENDING->FAILED exactly once; notify the customer once. The
        # conditional update is the idempotency guard: retries/re-polls of
        # this endpoint cannot duplicate the notification.
        marked = PaymentIntent.objects.filter(
            pk=payment_intent.pk,
            status=PaymentIntent.Status.PENDING).update(
                status=PaymentIntent.Status.FAILED)
        if marked:
            from apps.events.models import record_event
            record_event(
                "payment.failed",
                dedupe_key=f"payment.failed:{payment_intent.pk}",
                user_id=payment_intent.user_id,
                object_ref=f"payment:{payment_intent.pk}",
                payload={"payment_intent_id": str(payment_intent.pk),
                         "amount": payment_intent.amount,
                         "currency": payment_intent.currency},
            )
            transaction.on_commit(lambda: notify_payment_failed(payment_intent))
        return Response({"status": "failed",
                         "payment_intent_id": str(payment_intent.id)})

    activated, subscription = activate_paid_subscription(payment_intent)
    if subscription is None:
        return Response({"status": "pending",
                         "payment_intent_id": str(payment_intent.id)})
    result = {"status": "success",
              "subscription_id": str(subscription.id),
              "payment_intent_id": str(payment_intent.id),
              "expires_at": subscription.expires_at}
    if not activated:
        result["already_processed"] = True
    return Response(result)


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
    """Provider return URL: verify server-side, activate via the shared
    service, then render the result page.

    GET never flips a PaymentIntent to SUCCESS without the matching
    subscription: activation flows exclusively through
    `activate_paid_subscription`, which claims the intent and creates the
    subscription in the same flow."""
    intent = get_object_or_404(PaymentIntent, pk=pk, user=request.user)
    result = {"status": "pending", "intent": intent}
    if intent.status == PaymentIntent.Status.SUCCESS:
        subscription = Subscription.objects.filter(
            user=request.user, plan=intent.plan, is_active=True).first()
        if subscription is not None:
            result["status"] = "success"
    else:
        try:
            verified = providers.verify_provider_payment(intent)
        except providers.ProviderError:
            verified = None
        if verified is True:
            _activated, subscription = activate_paid_subscription(intent)
            if subscription is not None:
                result["status"] = "success"
        elif verified is False:
            # Claim PENDING->FAILED exactly once; notify the customer once.
            marked = PaymentIntent.objects.filter(
                pk=intent.pk,
                status=PaymentIntent.Status.PENDING).update(
                    status=PaymentIntent.Status.FAILED)
            if marked:
                transaction.on_commit(lambda: notify_payment_failed(intent))
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


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def payment_history(request):
    """Read-only billing history for the caller (GET /history/).

    PaymentIntent is the source of truth — no shadow table. Each entry
    carries its own refund rows and refund/chargeback state; the "resulting
    subscription" block is derived from the caller's currently active
    subscription for the same plan (safe derivation; nothing is written).
    """
    intents = (PaymentIntent.objects
               .filter(user=request.user)
               .select_related("plan", "applied_referral_discount")
               .prefetch_related("refunds")
               .order_by("-created_at")[:50])
    active_by_plan = {
        s.plan_id: s for s in Subscription.objects.filter(
            user=request.user, status=Subscription.Status.ACTIVE, is_active=True)
    }
    data = []
    for intent in intents:
        subscription = active_by_plan.get(intent.plan_id)
        data.append({
            "id": str(intent.id),
            "created_at": intent.created_at,
            "plan": {"id": str(intent.plan_id), "name": intent.plan.name},
            "base_amount_cents": intent.base_amount_cents,
            "amount": intent.amount,
            "currency": intent.currency,
            "status": intent.status,
            "provider": intent.provider,
            "applied_coupon_code": intent.applied_coupon_code,
            "coupon_discount_cents": intent.coupon_discount_cents,
            "has_referral_discount": intent.applied_referral_discount_id is not None,
            "provider_reference": intent.provider_reference,
            "provider_payment_id": intent.provider_payment_id,
            "refunds": [{
                "id": str(r.id),
                "amount_cents": r.amount_cents,
                "currency": r.currency,
                "refunded_at": r.refunded_at,
                "source": r.source,
                "provider_refund_id": r.provider_refund_id,
            } for r in intent.refunds.all()],
            "refunded_cents": intent.refunded_cents,
            "is_partially_refunded": intent.is_partially_refunded,
            "is_fully_refunded": intent.is_fully_refunded,
            "refunded_at": intent.refunded_at,
            "chargeback": intent.chargeback,
            "chargeback_confirmed": intent.chargeback_confirmed,
            "chargeback_reference": intent.chargeback_reference,
            "chargeback_at": intent.chargeback_at,
            "subscription": ({
                "id": str(subscription.id),
                "status": subscription.status,
                "expires_at": subscription.expires_at,
            } if subscription else None),
        })
    return Response({"payments": data})
