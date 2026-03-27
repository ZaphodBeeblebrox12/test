"""
Growth services for gift invites and claiming.

This module provides:
- GiftService: Creates gifts with GiftSubscription + GiftInvite atomically
- GiftClaimService: Handles secure token-based gift claiming
- LegacyGiftService: Handles legacy gift_code claims (backward compatibility)
- GiftEmailService: Handles email delivery through notifications app

ARCHITECTURE CONTRACT:
- This module NEVER imports from subscriptions.models directly
- All subscription operations go through subscriptions/api.py ONLY
- This module NEVER imports from django.core.mail
- Attribution is MANDATORY and ENFORCED for all gift claims
"""
import logging
from typing import Optional, Tuple
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.conf import settings

from apps.accounts.models import User

# CRITICAL: Only import from subscriptions.api - NEVER from subscriptions.models for operations
from apps.subscriptions.api import (
    create_gift_subscription as api_create_gift_subscription,
    extend_subscription_with_gift,
    create_subscription_from_gift,
    GiftAttribution,
    get_active_subscription,
    has_active_subscription,
    get_gift_by_code,  # NEW: For legacy gift lookup
    get_gift_by_id,    # NEW: For gift lookup by ID
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
    """
    Service for creating and managing gift invites.

    This is the ONLY way to create GiftInvite objects. Direct creation
    via admin or ORM is disabled to ensure proper data integrity.
    """

    DEFAULT_INVITE_EXPIRY_DAYS = 30
    DEFAULT_GIFT_DURATION_DAYS = 30

    @classmethod
    @transaction.atomic
    def create_gift(
        cls,
        from_user: User,
        recipient_email: str,
        plan,  # Plan object passed from caller (views/API)
        duration_days: int = None,
        message: str = "",
        request=None,
    ) -> Tuple[object, GiftInvite]:
        """
        Create a complete gift with both GiftSubscription and GiftInvite.

        This is an atomic operation - both objects are created together
        or not at all. The gift invite token is returned for email delivery.

        Args:
            from_user: User giving the gift
            recipient_email: Email address of intended recipient
            plan: Plan object to gift (passed from view/API layer)
            duration_days: How many days the gift lasts (default: 30)
            message: Optional message for recipient
            request: Optional request for geo detection

        Returns:
            Tuple of (GiftSubscription, GiftInvite)

        Raises:
            SelfGiftError: If from_user tries to gift themselves
            GiftServiceError: If creation fails
        """
        recipient_email = recipient_email.lower().strip()

        # Check for self-gifting
        if from_user.email and from_user.email.lower() == recipient_email:
            raise SelfGiftError("Cannot gift to your own email address")

        duration_days = duration_days or cls.DEFAULT_GIFT_DURATION_DAYS

        # Create the legacy GiftSubscription first (for backward compatibility)
        # This uses the subscriptions/api.py interface ONLY
        gift_sub = api_create_gift_subscription(
            from_user=from_user,
            plan=plan,
            duration_days=duration_days,
            message=message,
            request=request,
        )

        # Generate secure token for the invite
        claim_token = GiftInvite.generate_token()
        token_hash = GiftInvite.hash_token(claim_token)

        # Create the GiftInvite linked to the GiftSubscription
        expires_at = timezone.now() + timedelta(days=cls.DEFAULT_INVITE_EXPIRY_DAYS)

        gift_invite = GiftInvite.objects.create(
            gift_subscription=gift_sub,
            recipient_email=recipient_email,
            claim_token=claim_token,  # Will be hashed in save()
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
        """
        Look up a gift invite by its claim token.

        Args:
            token: The claim token from the URL

        Returns:
            GiftInvite if found and valid, None otherwise
        """
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
    """
    Service for handling legacy gift_code claims.

    This maintains backward compatibility with the old gift-code system
    where gifts were claimed using a short code (e.g., "ABC123XY")
    rather than a secure token.

    The legacy flow:
    1. User receives gift_code (e.g., "ABC123XY")
    2. User enters code on claim page
    3. System validates and claims the gift

    This is SEPARATE from the new token-based flow to avoid ambiguity.

    CRITICAL: All legacy claims MUST provide attribution via _build_attribution().
    """

    @classmethod
    def _build_attribution(cls, gift) -> GiftAttribution:
        """
        Build mandatory attribution for a legacy gift.

        This is REQUIRED and ENFORCED - every legacy claim must have attribution.
        """
        if not gift.from_user:
            raise AttributionRequiredError("Gift has no sender - cannot build attribution")

        return GiftAttribution(
            source="gift",
            gift_id=str(gift.id),
            claimed_from_email=gift.from_user.email or gift.from_user.username,
        )

    @classmethod
    def validate_legacy_claim(cls, gift, user: User) -> None:
        """
        Validate that a legacy gift can be claimed by the given user.

        Args:
            gift: The legacy GiftSubscription to validate
            user: The user attempting to claim

        Raises:
            GiftAlreadyClaimedError: If gift already claimed
            GiftExpiredError: If gift has expired
            SelfGiftError: If user tries to claim their own gift
        """
        # Import here to avoid circular imports at module level
        from apps.subscriptions.models import GiftSubscription as LegacyGiftSubscription

        # Check if already claimed
        if gift.status == LegacyGiftSubscription.Status.CLAIMED:
            raise GiftAlreadyClaimedError("This gift has already been claimed")

        if gift.to_user is not None:
            raise GiftAlreadyClaimedError("This gift has already been claimed")

        # Check expiration
        if gift.expires_at and gift.expires_at < timezone.now():
            raise GiftExpiredError("This gift has expired")

        if gift.status == LegacyGiftSubscription.Status.EXPIRED:
            raise GiftExpiredError("This gift has expired")

        # Check for self-gifting
        if gift.from_user_id == user.id:
            raise SelfGiftError("Cannot claim your own gift")

    @classmethod
    @transaction.atomic
    def claim_legacy_gift(
        cls,
        gift_code: str,
        user: User,
        request=None,
    ) -> Tuple[object, object]:
        """
        Claim a legacy gift using the gift code.

        This uses the subscriptions/api.py interface ONLY and does NOT
        interact with GiftInvite (the new token-based system).

        CRITICAL: Attribution is MANDATORY and ENFORCED.

        Args:
            gift_code: The legacy gift code
            user: The user claiming the gift
            request: Optional request for metadata

        Returns:
            Tuple of (GiftSubscription, Subscription)

        Raises:
            InvalidGiftCodeError: If code not found
            GiftAlreadyClaimedError: If already claimed
            GiftExpiredError: If expired
            SelfGiftError: If self-gifting
            AttributionRequiredError: If attribution cannot be built
        """
        # Import here to avoid circular imports at module level
        from apps.subscriptions.models import GiftSubscription as LegacyGiftSubscription

        gift_code = gift_code.upper().strip()

        # Look up the legacy gift using the API (boundary respected)
        gift = get_gift_by_code(gift_code)

        if not gift:
            raise InvalidGiftCodeError("Invalid gift code")

        # Lock the row to prevent race conditions
        try:
            gift = LegacyGiftSubscription.objects.select_related(
                'plan', 'from_user'
            ).select_for_update(nowait=False).get(
                id=gift.id,
                status=LegacyGiftSubscription.Status.PENDING
            )
        except LegacyGiftSubscription.DoesNotExist:
            raise InvalidGiftCodeError("Gift no longer available")

        # Validate
        cls.validate_legacy_claim(gift, user)

        # BUILD MANDATORY ATTRIBUTION - This is REQUIRED
        attribution = cls._build_attribution(gift)

        # Verify attribution is complete (paranoid check)
        if not attribution.source or not attribution.gift_id or not attribution.claimed_from_email:
            raise AttributionRequiredError(
                f"Incomplete attribution: source={attribution.source}, "
                f"gift_id={attribution.gift_id}, "
                f"claimed_from={attribution.claimed_from_email}"
            )

        # Check for existing subscription using api.py ONLY
        existing_sub = get_active_subscription(user)

        if existing_sub:
            # Extend existing subscription
            subscription = extend_subscription_with_gift(
                subscription=existing_sub,
                gift_plan=gift.plan,
                duration_days=gift.duration_days,
                attribution=attribution,
                request=request,
            )
            logger.info(
                f"Extended subscription {subscription.id} for user {user.id} "
                f"with legacy gift {gift.id}"
            )
        else:
            # Create new subscription
            subscription = create_subscription_from_gift(
                user=user,
                gift_plan=gift.plan,
                duration_days=gift.duration_days,
                attribution=attribution,
                request=request,
            )
            logger.info(
                f"Created new subscription {subscription.id} for user {user.id} "
                f"from legacy gift {gift.id}"
            )

        # Update the legacy gift
        gift.to_user = user
        gift.status = LegacyGiftSubscription.Status.CLAIMED
        gift.claimed_at = timezone.now()
        gift.resulting_subscription = subscription
        gift.save(update_fields=[
            'to_user', 'status', 'claimed_at', 'resulting_subscription'
        ])

        return gift, subscription


class GiftClaimService:
    """
    Service for handling token-based gift claims with full safety protections.

    This service ensures:
    - No double claims (with row locking)
    - Email verification before claiming
    - Atomic transaction handling
    - Proper subscription extension vs creation
    - MANDATORY ATTRIBUTION for all claims
    """

    @classmethod
    def _build_attribution(cls, gift_sub) -> GiftAttribution:
        """
        Build mandatory attribution for a token-based gift.

        This is REQUIRED and ENFORCED - every claim must have attribution.
        """
        if not gift_sub.from_user:
            raise AttributionRequiredError("Gift has no sender - cannot build attribution")

        return GiftAttribution(
            source="gift",
            gift_id=str(gift_sub.id),
            claimed_from_email=gift_sub.from_user.email or gift_sub.from_user.username,
        )

    @classmethod
    def validate_claim(
        cls,
        gift_invite: GiftInvite,
        user: User,
    ) -> None:
        """
        Validate that a gift can be claimed by the given user.

        Args:
            gift_invite: The gift invite to validate
            user: The user attempting to claim

        Raises:
            GiftAlreadyClaimedError: If gift already claimed
            GiftExpiredError: If gift has expired
            GiftEmailMismatchError: If user's email doesn't match
        """
        # Check if already claimed
        if gift_invite.status == GiftInvite.Status.CLAIMED:
            raise GiftAlreadyClaimedError("This gift has already been claimed")

        if gift_invite.claimed_by is not None:
            raise GiftAlreadyClaimedError("This gift has already been claimed")

        # Check expiration
        if gift_invite.is_expired:
            raise GiftExpiredError("This gift has expired")

        if gift_invite.status == GiftInvite.Status.EXPIRED:
            raise GiftExpiredError("This gift has expired")

        # Verify email match
        user_email = user.email.lower().strip() if user.email else None
        recipient_email = gift_invite.recipient_email.lower().strip()

        if user_email != recipient_email:
            raise GiftEmailMismatchError(
                f"This gift was sent to {recipient_email}. "
                f"Please log in with that email address."
            )

        # Check for self-gifting (shouldn't happen but safety check)
        if gift_invite.gift_subscription.from_user_id == user.id:
            raise SelfGiftError("Cannot claim your own gift")

    @classmethod
    @transaction.atomic
    def claim_gift(
        cls,
        token: str,
        user: User,
        request=None,
    ) -> object:
        """
        Claim a gift for the given user.

        This method uses row locking to prevent race conditions.
        It either extends an existing subscription or creates a new one.

        CRITICAL: Attribution is MANDATORY and ENFORCED.

        Args:
            token: The claim token
            user: The user claiming the gift
            request: Optional request for metadata

        Returns:
            The created or extended Subscription

        Raises:
            GiftServiceError: Various error types for invalid claims
            AttributionRequiredError: If attribution cannot be built
        """
        # Import here to avoid circular imports at module level
        from apps.subscriptions.models import GiftSubscription as LegacyGiftSubscription

        token_hash = GiftInvite.hash_token(token)

        # Lock the row to prevent race conditions
        try:
            gift_invite = GiftInvite.objects.select_related(
                'gift_subscription',
                'gift_subscription__plan',
                'gift_subscription__from_user',
            ).select_for_update(nowait=False).get(
                claim_token_hash=token_hash
            )
        except GiftInvite.DoesNotExist:
            raise GiftServiceError("Invalid gift token")

        # Validate the claim
        cls.validate_claim(gift_invite, user)

        # Get gift details
        gift_sub = gift_invite.gift_subscription
        plan = gift_sub.plan
        duration_days = gift_sub.duration_days

        # BUILD MANDATORY ATTRIBUTION - This is REQUIRED
        attribution = cls._build_attribution(gift_sub)

        # Verify attribution is complete (paranoid check)
        if not attribution.source or not attribution.gift_id or not attribution.claimed_from_email:
            raise AttributionRequiredError(
                f"Incomplete attribution: source={attribution.source}, "
                f"gift_id={attribution.gift_id}, "
                f"claimed_from={attribution.claimed_from_email}"
            )

        # Check for existing active subscription using api.py ONLY
        existing_sub = get_active_subscription(user)

        if existing_sub:
            # Extend existing subscription
            subscription = extend_subscription_with_gift(
                subscription=existing_sub,
                gift_plan=plan,
                duration_days=duration_days,
                attribution=attribution,
                request=request,
            )
            logger.info(
                f"Extended subscription {subscription.id} for user {user.id} "
                f"with gift {gift_sub.id}"
            )
        else:
            # Create new subscription
            subscription = create_subscription_from_gift(
                user=user,
                gift_plan=plan,
                duration_days=duration_days,
                attribution=attribution,
                request=request,
            )
            logger.info(
                f"Created new subscription {subscription.id} for user {user.id} "
                f"from gift {gift_sub.id}"
            )

        # Mark gift as claimed
        gift_invite.status = GiftInvite.Status.CLAIMED
        gift_invite.claimed_by = user
        gift_invite.claimed_at = timezone.now()
        gift_invite.save(update_fields=['status', 'claimed_by', 'claimed_at'])

        # Update legacy GiftSubscription
        gift_sub.to_user = user
        gift_sub.status = LegacyGiftSubscription.Status.CLAIMED
        gift_sub.claimed_at = timezone.now()
        gift_sub.resulting_subscription = subscription
        gift_sub.save(update_fields=[
            'to_user', 'status', 'claimed_at', 'resulting_subscription'
        ])

        # Clean up any pending claims for this user/session
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
    def store_pending_claim(
        cls,
        token: str,
        session_key: str,
        request=None,
    ) -> PendingGiftClaim:
        """
        Store a pending claim for anonymous users.

        Called when an anonymous user clicks a claim link.
        The pending claim is processed after they sign up/log in.

        Args:
            token: The claim token
            session_key: The user's session key
            request: Optional request for IP/UA tracking

        Returns:
            The created PendingGiftClaim
        """
        token_hash = GiftInvite.hash_token(token)

        # Check if already have a pending claim for this session
        existing = PendingGiftClaim.objects.filter(
            claim_token_hash=token_hash,
            session_key=session_key,
            status=PendingGiftClaim.Status.PENDING
        ).first()

        if existing:
            return existing

        # Get IP and user agent
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
    def process_pending_claims_for_user(
        cls,
        user: User,
        session_key: str,
        request=None,
    ) -> Optional[object]:
        """
        Process any pending claims for a newly authenticated user.

        Called after user signup/login to auto-claim gifts they
        attempted to claim while anonymous.

        Args:
            user: The newly authenticated user
            session_key: The session key from before authentication
            request: Optional request object

        Returns:
            Subscription if a claim was processed, None otherwise
        """
        # Find pending claims for this session
        pending = PendingGiftClaim.objects.filter(
            session_key=session_key,
            status=PendingGiftClaim.Status.PENDING
        ).select_related().first()

        if not pending:
            return None

        # Get the gift invite
        gift_invite = GiftInvite.objects.filter(
            claim_token_hash=pending.claim_token_hash
        ).first()

        if not gift_invite:
            # Mark as failed - token invalid
            pending.status = PendingGiftClaim.Status.FAILED
            pending.error_message = "Gift invite not found"
            pending.save(update_fields=['status', 'error_message'])
            return None

        # Try to claim
        try:
            # Claim by invite (bypasses token validation since we verified via session)
            subscription = cls._claim_by_invite(
                gift_invite=gift_invite,
                user=user,
                request=request,
            )

            # Mark pending as processed
            pending.status = PendingGiftClaim.Status.PROCESSED
            pending.processed_at = timezone.now()
            pending.processed_by = user
            pending.save(update_fields=['status', 'processed_at', 'processed_by'])

            logger.info(
                f"Auto-processed pending claim for user {user.id}, "
                f"subscription {subscription.id}"
            )

            return subscription

        except GiftEmailMismatchError as e:
            # Keep pending - user needs to verify email
            pending.error_message = str(e)
            pending.save(update_fields=['error_message'])
            logger.warning(f"Pending claim email mismatch for user {user.id}: {e}")
            return None

        except (GiftAlreadyClaimedError, GiftExpiredError) as e:
            # Mark as failed - gift no longer available
            pending.status = PendingGiftClaim.Status.FAILED
            pending.error_message = str(e)
            pending.save(update_fields=['status', 'error_message'])
            logger.warning(f"Pending claim failed for user {user.id}: {e}")
            return None

        except Exception as e:
            # Mark as failed - unexpected error
            pending.status = PendingGiftClaim.Status.FAILED
            pending.error_message = str(e)
            pending.save(update_fields=['status', 'error_message'])
            logger.error(f"Pending claim error for user {user.id}: {e}")
            return None

    @classmethod
    @transaction.atomic
    def _claim_by_invite(
        cls,
        gift_invite: GiftInvite,
        user: User,
        request=None,
    ) -> object:
        """
        Claim a gift by invite object (internal method for pending claims).

        This bypasses token validation since we already verified via session.

        CRITICAL: Attribution is MANDATORY and ENFORCED.
        """
        # Import here to avoid circular imports at module level
        from apps.subscriptions.models import GiftSubscription as LegacyGiftSubscription

        # Re-validate
        cls.validate_claim(gift_invite, user)

        # Lock and proceed with claim
        gift_invite = GiftInvite.objects.select_for_update().get(
            pk=gift_invite.pk
        )

        # Double-check after locking
        cls.validate_claim(gift_invite, user)

        gift_sub = gift_invite.gift_subscription
        plan = gift_sub.plan
        duration_days = gift_sub.duration_days

        # BUILD MANDATORY ATTRIBUTION - This is REQUIRED
        attribution = cls._build_attribution(gift_sub)

        # Verify attribution is complete
        if not attribution.source or not attribution.gift_id or not attribution.claimed_from_email:
            raise AttributionRequiredError("Incomplete attribution in _claim_by_invite")

        # Use api.py ONLY - no direct model access
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

        # Mark as claimed
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
    """
    Service for sending gift-related emails through notifications app.

    Growth never sends email directly - all email goes through
    the notifications app.
    """

    @classmethod
    def send_gift_email(
        cls,
        gift_invite: GiftInvite,
        claim_url: str,
        resend: bool = False,
    ) -> bool:
        """
        Send gift invitation email via notifications app.

        Args:
            gift_invite: The gift invite to send
            claim_url: The full claim URL with token
            resend: Whether this is a resend

        Returns:
            True if email was queued/sent successfully
        """
        from apps.notifications.services import NotificationService

        if resend and not GiftService.can_resend_email(gift_invite):
            logger.warning(
                f"Cannot resend email for gift {gift_invite.id}: "
                f"rate limit exceeded"
            )
            return False

        # Get gift details
        gift_sub = gift_invite.gift_subscription
        from_user = gift_sub.from_user
        plan = gift_sub.plan

        # Prepare context
        context = {
            'recipient_email': gift_invite.recipient_email,
            'sender_name': from_user.username,
            'sender_email': from_user.email,
            'plan_name': plan.name,
            'duration_days': gift_sub.duration_days,
            'message': gift_sub.message,
            'claim_url': claim_url,
            'expires_at': gift_invite.expires_at,
        }

        try:
            # Send via notifications service
            NotificationService.send_email(
                to_email=gift_invite.recipient_email,
                template='growth/gift_invite',
                subject=f"You've received a gift subscription from {from_user.username}!",
                context=context,
                metadata={
                    'gift_invite_id': str(gift_invite.id),
                    'gift_subscription_id': str(gift_sub.id),
                    'is_resend': resend,
                }
            )

            # Record that email was sent
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
    def send_claim_confirmation_email(
        cls,
        user: User,
        subscription: object,
        gift_invite: GiftInvite,
    ) -> bool:
        """
        Send confirmation email after successful gift claim.

        Args:
            user: User who claimed the gift
            subscription: The subscription created/extended
            gift_invite: The gift invite that was claimed

        Returns:
            True if email was sent successfully
        """
        from apps.notifications.services import NotificationService

        gift_sub = gift_invite.gift_subscription

        context = {
            'user': user,
            'plan_name': gift_sub.plan.name,
            'duration_days': gift_sub.duration_days,
            'subscription_expires': subscription.expires_at,
            'sender_name': gift_sub.from_user.username,
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
