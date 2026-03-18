"""
Subscription services for Phase 3B.
"""
from decimal import Decimal
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import (
    Subscription, Plan, PlanPrice, PlanDiscount, 
    GiftSubscription, SubscriptionHistory, SubscriptionEvent
)


class SubscriptionService:
    """Service class for subscription operations."""

    @staticmethod
    def check_banned(user):
        """Check if user is banned."""
        if user.is_banned:
            raise ValidationError("Banned users cannot perform subscription actions")

    @classmethod
    def create_subscription(cls, user, plan, plan_price, discount=None, 
                           payment_provider="", provider_id=""):
        """Create a new subscription."""
        cls.check_banned(user)

        with transaction.atomic():
            subscription = Subscription.objects.create(
                user=user,
                plan=plan,
                plan_price=plan_price,
                applied_discount=discount,
                status=Subscription.Status.ACTIVE,
                is_active=True,
                started_at=timezone.now(),
                expires_at=timezone.now() + cls._get_interval_delta(plan_price.interval),
                payment_provider=payment_provider,
                provider_subscription_id=provider_id
            )

            if discount:
                discount.increment_use()

            # Log history
            SubscriptionHistory.objects.create(
                subscription=subscription,
                user=user,
                event_type=SubscriptionHistory.EventType.CREATED,
                new_plan_id=plan.id,
                new_status=subscription.status
            )

            # Log event
            SubscriptionEvent.log_event(
                event_type=SubscriptionEvent.EventType.SUBSCRIPTION_CREATED,
                user=user,
                subscription=subscription,
                data={"plan_id": str(plan.id), "price_cents": plan_price.price_cents}
            )

            return subscription

    @classmethod
    def upgrade_subscription(cls, user, target_plan, target_price, discount=None):
        """Upgrade user to a higher tier plan with prorated credit."""
        cls.check_banned(user)

        with transaction.atomic():
            # Get current active subscription
            try:
                current_sub = Subscription.objects.get(
                    user=user,
                    is_active=True,
                    status=Subscription.Status.ACTIVE
                )
            except Subscription.DoesNotExist:
                raise ValidationError("No active subscription to upgrade")

            # Validate upgrade
            if not current_sub.can_upgrade(target_plan):
                raise ValidationError("Cannot upgrade to this plan (downgrades not allowed)")

            # Calculate prorated credit
            prorated_credit = current_sub.calculate_prorated_credit()

            # Cancel current subscription
            current_sub.cancel("Upgraded to new plan")

            # Calculate new price with discount
            new_price_cents = target_price.price_cents
            if discount and discount.is_valid():
                new_price_cents = discount.apply_discount(new_price_cents)

            # Apply prorated credit
            final_price_cents = max(0, new_price_cents - prorated_credit)

            # Create new subscription
            new_sub = Subscription.objects.create(
                user=user,
                plan=target_plan,
                plan_price=target_price,
                applied_discount=discount,
                status=Subscription.Status.ACTIVE,
                is_active=True,
                started_at=timezone.now(),
                expires_at=timezone.now() + cls._get_interval_delta(target_price.interval),
                prorated_credit_cents=prorated_credit
            )

            if discount:
                discount.increment_use()

            # Log upgrade history
            SubscriptionHistory.objects.create(
                subscription=new_sub,
                user=user,
                event_type=SubscriptionHistory.EventType.UPGRADED,
                previous_plan_id=current_sub.plan.id,
                new_plan_id=target_plan.id,
                previous_status=current_sub.status,
                new_status=new_sub.status,
                metadata={
                    "prorated_credit_cents": prorated_credit,
                    "original_price_cents": target_price.price_cents,
                    "final_price_cents": final_price_cents
                }
            )

            # Log event
            SubscriptionEvent.log_event(
                event_type=SubscriptionEvent.EventType.SUBSCRIPTION_UPGRADED,
                user=user,
                subscription=new_sub,
                data={
                    "previous_plan_id": str(current_sub.plan.id),
                    "new_plan_id": str(target_plan.id),
                    "prorated_credit": prorated_credit
                }
            )

            return new_sub, prorated_credit

    @classmethod
    def admin_grant_subscription(cls, admin_user, target_user, plan, duration_days=30, 
                                 reason="", override_expires=None):
        """Admin grants a subscription to a user."""
        if not admin_user.is_staff:
            raise ValidationError("Only staff can grant subscriptions")

        if target_user.is_banned:
            raise ValidationError("Cannot grant subscription to banned user")

        with transaction.atomic():
            # Cancel current subscription if exists
            Subscription.objects.filter(
                user=target_user,
                is_active=True
            ).update(
                is_active=False,
                status=Subscription.Status.CANCELED,
                canceled_at=timezone.now()
            )

            # Calculate expiration
            if override_expires:
                expires_at = override_expires
            else:
                expires_at = timezone.now() + timezone.timedelta(days=duration_days)

            # Create granted subscription
            subscription = Subscription.objects.create(
                user=target_user,
                plan=plan,
                status=Subscription.Status.ACTIVE,
                is_active=True,
                is_admin_grant=True,
                granted_by=admin_user,
                granted_reason=reason,
                started_at=timezone.now(),
                expires_at=expires_at
            )

            # Log history
            SubscriptionHistory.objects.create(
                subscription=subscription,
                user=target_user,
                event_type=SubscriptionHistory.EventType.ADMIN_GRANTED,
                new_plan_id=plan.id,
                new_status=subscription.status,
                notes=reason
            )

            # Log event
            SubscriptionEvent.log_event(
                event_type=SubscriptionEvent.EventType.ADMIN_GRANTED_PLAN,
                user=target_user,
                subscription=subscription,
                data={
                    "granted_by": str(admin_user.id),
                    "reason": reason,
                    "duration_days": duration_days
                }
            )

            return subscription

    @classmethod
    def create_gift(cls, sender, plan, duration_days=30, recipient_email="", message=""):
        """Create a gift subscription."""
        cls.check_banned(sender)

        import secrets
        code = secrets.token_urlsafe(16)[:20].upper()

        gift = GiftSubscription.objects.create(
            code=code,
            plan=plan,
            duration_days=duration_days,
            sender=sender,
            recipient_email=recipient_email,
            message=message,
            expires_at=timezone.now() + timezone.timedelta(days=365)  # Gift codes valid for 1 year
        )

        SubscriptionEvent.log_event(
            event_type=SubscriptionEvent.EventType.GIFT_CREATED,
            user=sender,
            data={
                "gift_id": str(gift.id),
                "plan_id": str(plan.id),
                "recipient_email": recipient_email
            }
        )

        return gift

    @staticmethod
    def _get_interval_delta(interval):
        """Get timedelta for interval."""
        if interval == PlanPrice.Interval.MONTHLY:
            return timezone.timedelta(days=30)
        elif interval == PlanPrice.Interval.YEARLY:
            return timezone.timedelta(days=365)
        return timezone.timedelta(days=30)


class BanEnforcementService:
    """Service to enforce bans on subscriptions."""

    @staticmethod
    def enforce_ban(user):
        """Cancel all active subscriptions for a banned user."""
        if not user.is_banned:
            return

        subscriptions = Subscription.objects.filter(
            user=user,
            is_active=True
        )

        for sub in subscriptions:
            sub.cancel("User banned")
