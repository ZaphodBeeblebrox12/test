"""
Growth services for gift invites and claiming.
"""
import logging
from typing import Optional, Tuple
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.conf import settings

from apps.accounts.models import User

from apps.subscriptions.api import (
    create_gift_subscription as api_create_gift_subscription,
    extend_subscription_with_gift,
    create_subscription_from_gift,
    GiftAttribution,
    get_active_subscription,
    has_active_subscription,
    get_gift_by_code,
    get_gift_by_id,
)

from .models import GiftInvite, PendingGiftClaim

logger = logging.getLogger(__name__)


class GiftServiceError(Exception):
    """Base exception for gift service errors."""
    pass


class GiftAlreadyClaimedError(GiftServiceError):
    """Raised when attempting to claim an already claimed gift."""
    pass


class GiftExpiredError(GiftServiceError):
    """Raised when attempting to claim an expired gift."""
    pass


class GiftEmailMismatchError(GiftServiceError):
    """Raised when claimer's email doesn't match gift recipient."""
    pass


class SelfGiftError(GiftServiceError):
    """Raised when user tries to gift themselves."""
    pass


class InvalidGiftCodeError(GiftServiceError):
    """Raised when legacy gift code is invalid."""
    pass


class AttributionRequiredError(GiftServiceError):
    """Raised when attribution is missing (should never happen)."""
    pass


class GiftService:
    """Service for creating and managing gift invites."""

    DEFAULT_INVITE_EXPIRY_DAYS = 30
    DEFAULT_GIFT_DURATION_DAYS = 30

    @classmethod
    @transaction.atomic
    def create_gift(
        cls,
        from_user: User,
        recipient_email: str,
        plan,
        duration_days: int = None,
        message: str = "",
        request=None,
    ) -> Tuple[object, GiftInvite]:
        """Create a complete gift with both GiftSubscription and GiftInvite."""
        recipient_email = recipient_email.lower().strip()

        if from_user.email and from_user.email.lower() == recipient_email:
            raise SelfGiftError("Cannot gift to your own email address")

        duration_days = duration_days or cls.DEFAULT_GIFT_DURATION_DAYS

        gift_sub = api_create_gift_subscription(
            from_user=from_user,
            plan=plan,
            duration_days=duration_days,
            message=message,
            request=request,
        )

        claim_token = GiftInvite.generate_token()
        token_hash = GiftInvite.hash_token(claim_token)
        expires_at = timezone.now() + timedelta(days=cls.DEFAULT_INVITE_EXPIRY_DAYS)

        gift_invite = GiftInvite.objects.create(
            gift_subscription=gift_sub,
            recipient_email=recipient_email,
            claim_token=claim_token,
            claim_token_hash=token_hash,
            expires_at=expires_at,
            status=GiftInvite.Status.PENDING,
        )

        logger.info(
            f"Created gift invite {gift_invite.id} for {recipient_email} "
            f"from user {from_user.id}"
        )

        return gift_sub, gift_invite

    @classmethod
    def get_gift_by_token(cls, token: str) -> Optional[GiftInvite]:
        """Look up a gift invite by its claim token."""
        token_hash = GiftInvite.hash_token(token)
        try:
            return GiftInvite.objects.select_related(
                'gift_subscription',
                'gift_subscription__plan',
                'gift_subscription__from_user',
            ).get(claim_token_hash=token_hash)
        except GiftInvite.DoesNotExist:
            return None

    @classmethod
    def can_resend_email(cls, gift_invite: GiftInvite) -> bool:
        """Check if email can be resent for this invite."""
        return gift_invite.can_resend_email

    @classmethod
    @transaction.atomic
    def record_email_sent(cls, gift_invite: GiftInvite) -> None:
        """Record that the gift email was sent."""
        now = timezone.now()
        if gift_invite.email_sent_at is None:
            gift_invite.email_sent_at = now
        gift_invite.last_email_sent_at = now
        gift_invite.email_resend_count += 1
        gift_invite.save(update_fields=[
            'email_sent_at',
            'last_email_sent_at',
            'email_resend_count'
        ])


class LegacyGiftService:
    """Service for handling legacy gift_code claims."""

    @classmethod
    def _build_attribution(cls, gift) -> GiftAttribution:
        """Build mandatory attribution for a legacy gift."""
        if not gift.from_user:
            raise AttributionRequiredError("Gift has no sender - cannot build attribution")
        return GiftAttribution(
            source="gift",
            gift_id=str(gift.id),
            claimed_from_email=gift.from_user.email or gift_from_user.username,
        )

    @classmethod
    def validate_legacy_claim(cls, gift, user: User) -> None:
        """Validate that a legacy gift can be claimed."""
        from apps.subscriptions.models import GiftSubscription as LegacyGiftSubscription

        if gift.status == LegacyGiftSubscription.Status.CLAIMED:
            raise GiftAlreadyClaimedError("This gift has already been claimed")
        if gift.to_user is not None:
            raise GiftAlreadyClaimedError("This gift has already been claimed")
        if gift.expires_at and gift.expires_at < timezone.now():
            raise GiftExpiredError("This gift has expired")
        if gift.status == LegacyGiftSubscription.Status.EXPIRED:
            raise GiftExpiredError("This gift has expired")
        if gift.from_user_id == user.id:
            raise SelfGiftError("Cannot claim your own gift")

    @classmethod
    @transaction.atomic
    def claim_legacy_gift(cls, gift_code: str, user: User, request=None) -> Tuple[object, object]:
        """Claim a legacy gift using the gift code."""
        from apps.subscriptions.models import GiftSubscription as LegacyGiftSubscription

        gift_code = gift_code.upper().strip()
        gift = get_gift_by_code(gift_code)

        if not gift:
            raise InvalidGiftCodeError("Invalid gift code")

        try:
            gift = LegacyGiftSubscription.objects.select_related(
                'plan', 'from_user'
            ).select_for_update(nowait=False).get(
                id=gift.id,
                status=LegacyGiftSubscription.Status.PENDING
            )
        except LegacyGiftSubscription.DoesNotExist:
            raise InvalidGiftCodeError("Gift no longer available")

        cls.validate_legacy_claim(gift, user)
        attribution = cls._build_attribution(gift)

        existing_sub = get_active_subscription(user)

        if existing_sub:
            subscription = extend_subscription_with_gift(
                subscription=existing_sub,
                gift_plan=gift.plan,
                duration_days=gift.duration_days,
                attribution=attribution,
                request=request,
            )
        else:
            subscription = create_subscription_from_gift(
                user=user,
                gift_plan=gift.plan,
                duration_days=gift.duration_days,
                attribution=attribution,
                request=request,
            )

        gift.to_user = user
        gift.status = LegacyGiftSubscription.Status.CLAIMED
        gift.claimed_at = timezone.now()
        gift.resulting_subscription = subscription
        gift.save(update_fields=[
            'to_user', 'status', 'claimed_at', 'resulting_subscription'
        ])

        return gift, subscription


class GiftClaimService:
    """Service for handling token-based gift claims."""

    @classmethod
    def _build_attribution(cls, gift_sub) -> GiftAttribution:
        """Build mandatory attribution for a token-based gift."""
        if not gift_sub.from_user:
            raise AttributionRequiredError("Gift has no sender - cannot build attribution")
        return GiftAttribution(
            source="gift",
            gift_id=str(gift_sub.id),
            claimed_from_email=gift_sub.from_user.email or gift_sub.from_user.username,
        )

    @classmethod
    def validate_claim(cls, gift_invite: GiftInvite, user: User) -> None:
        """Validate that a gift can be claimed."""
        if gift_invite.status == GiftInvite.Status.CLAIMED:
            raise GiftAlreadyClaimedError("This gift has already been claimed")
        if gift_invite.claimed_by is not None:
            raise GiftAlreadyClaimedError("This gift has already been claimed")
        if gift_invite.is_expired:
            raise GiftExpiredError("This gift has expired")
        if gift_invite.status == GiftInvite.Status.EXPIRED:
            raise GiftExpiredError("This gift has expired")

        user_email = user.email.lower().strip() if user.email else None
        recipient_email = gift_invite.recipient_email.lower().strip()

        if user_email != recipient_email:
            raise GiftEmailMismatchError(
                f"This gift was sent to {recipient_email}. "
                f"Please log in with that email address."
            )

        if gift_invite.gift_subscription.from_user_id == user.id:
            raise SelfGiftError("Cannot claim your own gift")

    @classmethod
    @transaction.atomic
    def claim_gift(cls, token: str, user: User, request=None) -> object:
        """Claim a gift for the given user."""
        from apps.subscriptions.models import GiftSubscription as LegacyGiftSubscription

        token_hash = GiftInvite.hash_token(token)
        try:
            gift_invite = GiftInvite.objects.select_related(
                'gift_subscription',
                'gift_subscription__plan',
                'gift_subscription__from_user',
            ).select_for_update(nowait=False).get(claim_token_hash=token_hash)
        except GiftInvite.DoesNotExist:
            raise GiftServiceError("Invalid gift token")

        cls.validate_claim(gift_invite, user)

        gift_sub = gift_invite.gift_subscription
        plan = gift_sub.plan
        duration_days = gift_sub.duration_days

        attribution = cls._build_attribution(gift_sub)
        existing_sub = get_active_subscription(user)

        if existing_sub:
            subscription = extend_subscription_with_gift(
                subscription=existing_sub,
                gift_plan=plan,
                duration_days=duration_days,
                attribution=attribution,
                request=request,
            )
        else:
            subscription = create_subscription_from_gift(
                user=user,
                gift_plan=plan,
                duration_days=duration_days,
                attribution=attribution,
                request=request,
            )

        gift_invite.status = GiftInvite.Status.CLAIMED
        gift_invite.claimed_by = user
        gift_invite.claimed_at = timezone.now()
        gift_invite.save(update_fields=['status', 'claimed_by', 'claimed_at'])

        gift_sub.to_user = user
        gift_sub.status = LegacyGiftSubscription.Status.CLAIMED
        gift_sub.claimed_at = timezone.now()
        gift_sub.resulting_subscription = subscription
        gift_sub.save(update_fields=[
            'to_user', 'status', 'claimed_at', 'resulting_subscription'
        ])

        cls._cleanup_pending_claims(token_hash, user)
        return subscription

    @classmethod
    def _cleanup_pending_claims(cls, token_hash: str, user: User) -> None:
        """Clean up pending claims after successful claim."""
        PendingGiftClaim.objects.filter(
            claim_token_hash=token_hash,
            status=PendingGiftClaim.Status.PENDING
        ).update(
            status=PendingGiftClaim.Status.PROCESSED,
            processed_at=timezone.now(),
            processed_by=user
        )

    @classmethod
    def store_pending_claim(cls, token: str, session_key: str, request=None) -> PendingGiftClaim:
        """Store a pending claim for anonymous users."""
        token_hash = GiftInvite.hash_token(token)
        existing = PendingGiftClaim.objects.filter(
            claim_token_hash=token_hash,
            session_key=session_key,
            status=PendingGiftClaim.Status.PENDING
        ).first()

        if existing:
            return existing

        ip_address = None
        user_agent = ""
        if request:
            ip_address = cls._get_client_ip(request)
            user_agent = request.META.get('HTTP_USER_AGENT', '')[:500]

        pending = PendingGiftClaim.objects.create(
            claim_token_hash=token_hash,
            session_key=session_key,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        logger.info(f"Stored pending claim {pending.id} for token {token_hash[:8]}...")
        return pending

    @classmethod
    def process_pending_claims_for_user(cls, user: User, session_key: str, request=None) -> Optional[object]:
        """Process any pending claims for a newly authenticated user."""
        pending = PendingGiftClaim.objects.filter(
            session_key=session_key,
            status=PendingGiftClaim.Status.PENDING
        ).select_related().first()

        if not pending:
            return None

        gift_invite = GiftInvite.objects.filter(
            claim_token_hash=pending.claim_token_hash
        ).first()

        if not gift_invite:
            pending.status = PendingGiftClaim.Status.FAILED
            pending.error_message = "Gift invite not found"
            pending.save(update_fields=['status', 'error_message'])
            return None

        try:
            subscription = cls._claim_by_invite(
                gift_invite=gift_invite,
                user=user,
                request=request,
            )
            pending.status = PendingGiftClaim.Status.PROCESSED
            pending.processed_at = timezone.now()
            pending.processed_by = user
            pending.save(update_fields=['status', 'processed_at', 'processed_by'])
            return subscription

        except GiftEmailMismatchError as e:
            pending.error_message = str(e)
            pending.save(update_fields=['error_message'])
            return None
        except (GiftAlreadyClaimedError, GiftExpiredError) as e:
            pending.status = PendingGiftClaim.Status.FAILED
            pending.error_message = str(e)
            pending.save(update_fields=['status', 'error_message'])
            return None
        except Exception as e:
            pending.status = PendingGiftClaim.Status.FAILED
            pending.error_message = str(e)
            pending.save(update_fields=['status', 'error_message'])
            logger.error(f"Pending claim error for user {user.id}: {e}")
            return None

    @classmethod
    @transaction.atomic
    def _claim_by_invite(cls, gift_invite: GiftInvite, user: User, request=None) -> object:
        """Claim a gift by invite object (internal method for pending claims)."""
        from apps.subscriptions.models import GiftSubscription as LegacyGiftSubscription

        cls.validate_claim(gift_invite, user)
        gift_invite = GiftInvite.objects.select_for_update().get(pk=gift_invite.pk)
        cls.validate_claim(gift_invite, user)

        gift_sub = gift_invite.gift_subscription
        plan = gift_sub.plan
        duration_days = gift_sub.duration_days

        attribution = cls._build_attribution(gift_sub)
        existing_sub = get_active_subscription(user)

        if existing_sub:
            subscription = extend_subscription_with_gift(
                subscription=existing_sub,
                gift_plan=plan,
                duration_days=duration_days,
                attribution=attribution,
                request=request,
            )
        else:
            subscription = create_subscription_from_gift(
                user=user,
                gift_plan=plan,
                duration_days=duration_days,
                attribution=attribution,
                request=request,
            )

        gift_invite.status = GiftInvite.Status.CLAIMED
        gift_invite.claimed_by = user
        gift_invite.claimed_at = timezone.now()
        gift_invite.save(update_fields=['status', 'claimed_by', 'claimed_at'])

        gift_sub.to_user = user
        gift_sub.status = LegacyGiftSubscription.Status.CLAIMED
        gift_sub.claimed_at = timezone.now()
        gift_sub.resulting_subscription = subscription
        gift_sub.save(update_fields=[
            'to_user', 'status', 'claimed_at', 'resulting_subscription'
        ])

        return subscription

    @staticmethod
    def _get_client_ip(request) -> Optional[str]:
        """Extract client IP from request."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip


class GiftEmailService:
    """Service for sending gift-related emails."""

    @classmethod
    def send_gift_email(cls, gift_invite: GiftInvite, claim_url: str, resend: bool = False) -> bool:
        """Send gift invitation email."""
        from apps.notifications.services import NotificationService

        if resend and not GiftService.can_resend_email(gift_invite):
            logger.warning(f"Cannot resend email for gift {gift_invite.id}: rate limit exceeded")
            return False

        gift_sub = gift_invite.gift_subscription
        from_user = gift_sub.from_user
        plan = gift_sub.plan

        # Get display name: nickname > first_name > username
        sender_display_name = cls._get_user_display_name(from_user)

        context = {
            'recipient_email': gift_invite.recipient_email,
            'sender_name': sender_display_name,
            'sender_email': from_user.email,
            'plan_name': plan.name,
            'duration_days': gift_sub.duration_days,
            'message': gift_sub.message,
            'claim_url': claim_url,
            'expires_at': gift_invite.expires_at,
        }

        try:
            NotificationService.send_email(
                to_email=gift_invite.recipient_email,
                template='growth/gift_invite',
                subject=f"You've received a gift subscription from {sender_display_name}!",
                context=context,
                metadata={
                    'gift_invite_id': str(gift_invite.id),
                    'gift_subscription_id': str(gift_sub.id),
                    'is_resend': resend,
                }
            )

            GiftService.record_email_sent(gift_invite)

            logger.info(
                f"{'Resent' if resend else 'Sent'} gift email for {gift_invite.id} "
                f"to {gift_invite.recipient_email}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to send gift email for {gift_invite.id}: {e}")
            return False

    @classmethod
    def send_claim_confirmation_email(cls, user: User, subscription: object, gift_invite: GiftInvite) -> bool:
        """Send confirmation email after successful gift claim."""
        from apps.notifications.services import NotificationService

        gift_sub = gift_invite.gift_subscription
        sender_display_name = cls._get_user_display_name(gift_sub.from_user)

        context = {
            'user': user,
            'plan_name': gift_sub.plan.name,
            'duration_days': gift_sub.duration_days,
            'subscription_expires': subscription.expires_at,
            'sender_name': sender_display_name,
        }

        try:
            NotificationService.send_email(
                to_email=user.email,
                template='growth/gift_claimed',
                subject="Your gift subscription has been activated!",
                context=context,
                metadata={
                    'gift_invite_id': str(gift_invite.id),
                    'subscription_id': str(subscription.id),
                }
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send claim confirmation: {e}")
            return False

    @classmethod
    def _get_user_display_name(cls, user: User) -> str:
        """
        Get the display name for a user.
        Priority: nickname > first_name > username
        """
        # Check for custom nickname first
        if hasattr(user, 'nickname') and user.nickname:
            return user.nickname

        # Fall back to first_name
        if user.first_name:
            return user.first_name

        # Final fallback to username
        return user.username
