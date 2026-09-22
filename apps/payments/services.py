"""P4/P5: shared payment lifecycle service.

The single authoritative path for "verified payment -> subscription". Both
the customer-return confirm (P1) and the provider webhook (P4) call
`activate_paid_subscription` after their own verification. All mutation flows
through the atomic conditional claim, so the two entry points are idempotent
regardless of arrival order.

Billing additions (refunds / chargebacks) follow the same discipline: every
mutation is an atomic conditional claim, every transition records a durable
event + AuditLog, and entitlement changes flow through the existing
subscription services (never a parallel access-control system).
"""
import logging

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from datetime import timedelta

from apps.subscriptions.models import Subscription, SubscriptionHistory
from .models import PaymentIntent, Refund

logger = logging.getLogger(__name__)


def interval_days_for(payment_intent):
    price_source = payment_intent.plan_price or payment_intent.geo_plan_price
    interval = getattr(price_source, "interval", "monthly")
    return {"monthly": 30, "quarterly": 90, "yearly": 365}.get(interval, 30)


def _notify_payment_succeeded(payment_intent, subscription):
    """Receipt email exactly once per activated payment; must never break
    activation (webhook or browser path)."""
    try:
        from .notifications import notify_payment_succeeded
        notify_payment_succeeded(payment_intent, subscription)
    except Exception:
        logger.exception("payment success notification failed")


def activate_paid_subscription(payment_intent) -> tuple[bool, object]:
    """Atomically claim the intent and activate the subscription.

    Returns (activated: bool, subscription|None). Idempotent: a second call
    for an already-SUCCESS intent returns (False, existing_subscription).
    """
    claimed = PaymentIntent.objects.filter(
        pk=payment_intent.pk,
        status=PaymentIntent.Status.PENDING).update(
        status=PaymentIntent.Status.SUCCESS)
    if not claimed:
        existing = Subscription.objects.filter(
            user_id=payment_intent.user_id, plan=payment_intent.plan,
            is_active=True).first()
        return False, existing

    interval_days = interval_days_for(payment_intent)
    with transaction.atomic():
        # Renewal: an existing ACTIVE subscription for the same user+plan is
        # EXTENDED in place (new expiry = now + interval), not duplicated.
        # Claim first-come-first-served so concurrent activations for the
        # same subscription extend at most once.
        subscription = Subscription.objects.filter(
            user_id=payment_intent.user_id,
            plan=payment_intent.plan,
            status=Subscription.Status.ACTIVE,
            is_active=True).exclude(
            expires_at__gt=timezone.now() + timedelta(days=interval_days)
        ).order_by("expires_at").first()
        if subscription is not None:
            claimed_sub = Subscription.objects.filter(
                pk=subscription.pk, status=Subscription.Status.ACTIVE,
                is_active=True).update(
                expires_at=timezone.now() + timedelta(days=interval_days),
                price_cents=payment_intent.amount,
                price_currency=payment_intent.currency,
                payment_provider=payment_intent.provider)
            if claimed_sub:
                SubscriptionHistory.objects.create(
                    subscription=subscription, user_id=payment_intent.user_id,
                    event_type=SubscriptionHistory.EventType.RENEWED,
                    new_plan_id=payment_intent.plan_id,
                    new_status=Subscription.Status.ACTIVE)
                _notify_payment_succeeded(payment_intent, subscription)
                return True, subscription
        subscription = Subscription.objects.create(
            user_id=payment_intent.user_id, plan=payment_intent.plan,
            plan_price=payment_intent.plan_price,
            geo_plan_price=payment_intent.geo_plan_price,
            status=Subscription.Status.ACTIVE, is_active=True,
            started_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=interval_days),
            price_cents=payment_intent.amount,
            price_currency=payment_intent.currency,
            payment_provider=payment_intent.provider,
            pricing_country=payment_intent.country,
            base_price_cents=payment_intent.base_amount_cents,
            discount_cents=payment_intent.coupon_discount_cents,
            coupon_code=payment_intent.applied_coupon_code)
        SubscriptionHistory.objects.create(
            subscription=subscription, user_id=payment_intent.user_id,
            event_type=SubscriptionHistory.EventType.CREATED,
            new_plan_id=payment_intent.plan_id,
            new_status=Subscription.Status.ACTIVE)
        from apps.growth.services.referrals import ReferralService
        ReferralService.complete_referral_on_purchase(
            user=subscription.user,
            purchase_amount_cents=payment_intent.amount,
            currency=payment_intent.currency,
            triggering_subscription=subscription,
        )
        from apps.growth.services.rewards import SubscriptionCreditService
        SubscriptionCreditService.apply_credit_to_subscription(
            user=subscription.user,
            subscription=subscription,
            plan_price_cents=(
                payment_intent.base_amount_cents or payment_intent.amount),
            plan_duration_days=interval_days,
        )
    _notify_payment_succeeded(payment_intent, subscription)
    from apps.events.models import record_event
    record_event(
        "purchase.completed",
        dedupe_key=f"purchase.completed:{payment_intent.pk}",
        user_id=payment_intent.user_id,
        object_ref=f"payment:{payment_intent.pk}",
        payload={"payment_intent_id": str(payment_intent.pk),
                 "subscription_id": str(subscription.pk),
                 "amount": payment_intent.amount, "currency": payment_intent.currency},
    )
    if getattr(payment_intent, "applied_coupon_code", ""):
        from apps.promotions.models import Coupon
        from apps.promotions.services.coupons import redeem_coupon
        coupon = Coupon.objects.filter(
            code__iexact=payment_intent.applied_coupon_code).first()
        created = redeem_coupon(coupon=coupon, user=payment_intent.user,
                                payment_intent=payment_intent, subscription=subscription,
                                discount_cents=payment_intent.coupon_discount_cents,
                                base_amount_cents=payment_intent.base_amount_cents,
                                final_amount_cents=payment_intent.amount,
                                currency=payment_intent.currency)
        if created:
            from apps.promotions.models import CouponRedemption
            from django.utils import timezone as _tz
            CouponRedemption.objects.filter(
                coupon=coupon, payment_intent=payment_intent).update(
                finalized=True, finalized_at=_tz.now())

    return True, subscription


# ─────────────────────────── refunds ───────────────────────────────────────

def note_provider_payment_id(payment_intent, provider_payment_id):
    """Persist the provider's payment/charge id (pi_.. / pay_..) once.

    Set-if-empty: refund/dispute webhooks carry the payment id, not the
    checkout session/order id we store in provider_reference. Keeping the
    first id we see lets those webhooks link back to the intent.
    """
    if not provider_payment_id:
        return
    PaymentIntent.objects.filter(
        pk=payment_intent.pk, provider_payment_id="").update(
        provider_payment_id=provider_payment_id)


def record_refund(payment_intent, *, provider, provider_refund_id,
                  amount_cents, currency, refunded_at=None,
                  source=Refund.Source.WEBHOOK, metadata=None):
    """Record one refund. Idempotent per (provider, provider_refund_id):
    retries of the same provider refund store exactly one row.

    Returns (refund, created). created=False means this provider refund was
    already recorded — counters and notifications must NOT be repeated.
    """
    from apps.events.models import record_event
    refunded_at = refunded_at or timezone.now()
    with transaction.atomic():
        refund, created = Refund.objects.get_or_create(
            provider=provider, provider_refund_id=provider_refund_id,
            defaults={"payment_intent": payment_intent,
                      "amount_cents": amount_cents, "currency": currency,
                      "source": source, "refunded_at": refunded_at,
                      "metadata": metadata or {}})
        if created:
            PaymentIntent.objects.filter(pk=payment_intent.pk).update(
                refunded_cents=F("refunded_cents") + amount_cents,
                refunded_at=refunded_at)
            record_event(
                "payment.refunded",
                dedupe_key=f"payment.refunded:{refund.pk}",
                user_id=payment_intent.user_id,
                object_ref=f"payment:{payment_intent.pk}",
                payload={"refund_id": str(refund.pk),
                         "amount_cents": amount_cents, "currency": currency,
                         "source": source,
                         "provider_refund_id": provider_refund_id},
            )
    return refund, created


def apply_refund_policy(payment_intent):
    """Refund -> entitlement rule (smallest sensible policy, documented):

      * FULL refund (cumulative refunded >= charged): the customer got all
        their money back, so cancel the affected paid subscription and revoke
        access through the existing cancel flow.
      * PARTIAL refund: entitlement is unchanged. An admin can still cancel
        manually via the existing subscription admin/services if a specific
        case warrants it.

    Returns the canceled Subscription, or None.
    """
    from apps.subscriptions.services import cancel_subscription
    payment_intent.refresh_from_db()
    if payment_intent.amount <= 0 or payment_intent.refunded_cents < payment_intent.amount:
        return None
    subscription = Subscription.objects.filter(
        user_id=payment_intent.user_id, plan_id=payment_intent.plan_id,
        status=Subscription.Status.ACTIVE, is_active=True).first()
    if subscription is None:
        return None
    cancel_subscription(subscription, actor="refund")
    return subscription


# ─────────────────────── chargebacks / disputes ────────────────────────────

def record_dispute_opened(payment_intent, *, dispute_reference="", source="webhook",
                          actor=None):
    """A dispute was opened (NOT yet confirmed). Flags the payment and
    records audit/event, but does NOT touch entitlement: a dispute can still
    be won. Idempotent value-setting; events dedupe per dispute reference.
    """
    from apps.audit.models import AuditLog
    from apps.events.models import record_event
    updates = {"chargeback": True}
    if not payment_intent.chargeback_at:
        updates["chargeback_at"] = timezone.now()
    if dispute_reference and not payment_intent.chargeback_reference:
        updates["chargeback_reference"] = dispute_reference
    PaymentIntent.objects.filter(pk=payment_intent.pk).update(**updates)
    AuditLog.log(
        action="payment.disputed",
        user=actor or payment_intent.user,
        object_type="payment_intent", object_id=payment_intent.pk,
        metadata={"dispute_reference": dispute_reference, "source": source,
                  "affected_user_id": str(payment_intent.user_id)})
    record_event(
        "payment.disputed",
        dedupe_key=f"payment.disputed:{payment_intent.pk}:{dispute_reference or 'na'}",
        user_id=payment_intent.user_id,
        object_ref=f"payment:{payment_intent.pk}",
        payload={"dispute_reference": dispute_reference, "source": source},
    )


def confirm_chargeback(payment_intent, *, dispute_reference="", confirmed_at=None,
                       source="webhook", actor=None):
    """CONFIRMED chargeback (funds withdrawn / dispute lost).

    Single atomic claim (chargeback_confirmed False->True): exactly one
    caller performs the confirmation. That caller:
      * marks the payment (flags + provider dispute reference),
      * writes AuditLog + durable payment.chargeback event,
      * cancels the affected paid subscription IMMEDIATELY through the
        existing cancel flow (history + reconcile enqueue -> Telegram/Discord
        access revocation),
      * (caller) notifies the customer once.

    Returns True iff THIS call performed the confirmation; False when the
    chargeback was already confirmed (retries are no-ops).
    """
    from apps.audit.models import AuditLog
    from apps.events.models import record_event
    from apps.subscriptions.services import cancel_subscription
    confirmed_at = confirmed_at or timezone.now()
    with transaction.atomic():
        claimed = PaymentIntent.objects.filter(
            pk=payment_intent.pk, chargeback_confirmed=False).update(
            chargeback=True, chargeback_confirmed=True,
            chargeback_reference=dispute_reference or payment_intent.chargeback_reference,
            chargeback_at=confirmed_at)
        if not claimed:
            return False
        AuditLog.log(
            action="payment.chargeback",
            user=actor or payment_intent.user,
            object_type="payment_intent", object_id=payment_intent.pk,
            metadata={"dispute_reference": dispute_reference, "source": source,
                      "affected_user_id": str(payment_intent.user_id),
                      "admin_id": str(actor.pk) if actor else ""})
        record_event(
            "payment.chargeback",
            dedupe_key=f"payment.chargeback:{payment_intent.pk}",
            user_id=payment_intent.user_id,
            object_ref=f"payment:{payment_intent.pk}",
            payload={"dispute_reference": dispute_reference, "source": source,
                     "amount": payment_intent.amount,
                     "currency": payment_intent.currency})
        subscription = Subscription.objects.filter(
            user_id=payment_intent.user_id, plan_id=payment_intent.plan_id,
            status=Subscription.Status.ACTIVE, is_active=True).first()
        if subscription is not None:
            cancel_subscription(subscription, actor="chargeback")
    return True


def record_dispute_won(payment_intent, *, dispute_reference="", source="webhook",
                       actor=None):
    """Dispute closed in our favor: clear the flags (entitlement was never
    revoked on an opened dispute, so nothing to restore)."""
    from apps.audit.models import AuditLog
    from apps.events.models import record_event
    PaymentIntent.objects.filter(pk=payment_intent.pk).update(
        chargeback=False, chargeback_confirmed=False)
    AuditLog.log(
        action="payment.dispute_won",
        user=actor or payment_intent.user,
        object_type="payment_intent", object_id=payment_intent.pk,
        metadata={"dispute_reference": dispute_reference, "source": source})
    record_event(
        "payment.dispute_won",
        dedupe_key=f"payment.dispute_won:{payment_intent.pk}:{dispute_reference or 'na'}",
        user_id=payment_intent.user_id,
        object_ref=f"payment:{payment_intent.pk}",
        payload={"dispute_reference": dispute_reference, "source": source},
    )
