"""
Subscriptions API - Clean boundary for external app integration.

This module is the ONLY interface that other apps should use to interact
with subscription lifecycle operations. It provides explicit contracts
and ensures proper data integrity.

ARCHITECTURE CONTRACT:
- This module NEVER imports from growth app
- All subscription operations go through these functions
- Attribution data is explicitly passed, not inferred
"""
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User

# These are the ONLY models we expose directly - they're the core subscription models
# that external apps need to reference for ForeignKey relationships
from .models import Plan, Subscription, GiftSubscription, SubscriptionHistory


@dataclass
class GiftAttribution:
    """
    Attribution data for gift-based subscriptions.

    This ensures gift attribution is explicitly passed and queryable.
    """
    source: str = "gift"  # Always "gift" for gift subscriptions
    gift_id: str = ""  # ID of the GiftSubscription
    claimed_from_email: str = ""  # Email of the gift sender

    def to_metadata(self) -> Dict[str, Any]:
        """Convert to metadata dict for storage."""
        return {
            "source": self.source,
            "gift_id": self.gift_id,
            "claimed_from_email": self.claimed_from_email,
        }


def create_gift_subscription(
    from_user: User,
    plan: Plan,
    duration_days: int = 30,
    message: str = "",
    request=None,
) -> GiftSubscription:
    """
    Create a GiftSubscription (legacy model for backward compatibility).

    This is called by growth.services.GiftService to create the underlying
    GiftSubscription that GiftInvite links to.

    Args:
        from_user: User giving the gift
        plan: Plan to gift
        duration_days: How many days the gift lasts
        message: Optional message for recipient
        request: Optional request for geo detection

    Returns:
        The created GiftSubscription
    """
    from .services import create_gift_subscription as _create_gift

    # Use the existing service function
    return _create_gift(
        from_user=from_user,
        plan=plan,
        duration_days=duration_days,
        message=message,
        request=request,
    )


def get_gift_by_code(gift_code: str) -> Optional[GiftSubscription]:
    """
    Look up a legacy gift by its gift_code.

    This is used by growth app for legacy gift code claims.
    Returns the gift if found and claimable, None otherwise.

    Args:
        gift_code: The legacy gift code (e.g., "ABC123XY")

    Returns:
        GiftSubscription if found and claimable, None otherwise
    """
    try:
        return GiftSubscription.objects.select_related(
            'plan', 'from_user'
        ).get(
            gift_code=gift_code.upper().strip(),
            status=GiftSubscription.Status.PENDING
        )
    except GiftSubscription.DoesNotExist:
        return None


def get_gift_by_id(gift_id: str) -> Optional[GiftSubscription]:
    """
    Look up a gift by its ID.

    Used by growth app when it only has the ID (e.g., from GiftInvite).

    Args:
        gift_id: The GiftSubscription UUID

    Returns:
        GiftSubscription if found, None otherwise
    """
    try:
        return GiftSubscription.objects.select_related(
            'plan', 'from_user'
        ).get(id=gift_id)
    except (GiftSubscription.DoesNotExist, ValueError):
        return None


@transaction.atomic
def create_subscription_from_gift(
    user: User,
    gift_plan: Plan,
    duration_days: int,
    attribution: GiftAttribution,
    request=None,
) -> Subscription:
    """
    Create a new subscription from a gift claim.

    Called when a user with NO active subscription claims a gift.
    Creates a fresh subscription with proper gift attribution.

    Args:
        user: User claiming the gift
        gift_plan: Plan being gifted
        duration_days: Duration of the gift
        attribution: GiftAttribution with source, gift_id, claimed_from_email
        request: Optional request for geo/pricing info

    Returns:
        The newly created Subscription
    """
    from .services import get_pricing_country, get_region_for_country

    expires_at = timezone.now() + timedelta(days=duration_days)

    # Get pricing country if request provided
    pricing_country = None
    pricing_region = None
    if request:
        pricing_country = get_pricing_country(request)
        pricing_region = get_region_for_country(pricing_country)

    # Create the subscription with gift attribution
    subscription = Subscription.objects.create(
        user=user,
        plan=gift_plan,
        status=Subscription.Status.ACTIVE,
        is_active=True,
        started_at=timezone.now(),
        expires_at=expires_at,
        is_gift=True,
        gift_from=None,  # We don't have a user reference here, use metadata
        gift_message="",  # Use metadata for full message
        pricing_country=pricing_country,
        pricing_region=pricing_region,
    )

    # Store attribution in metadata via history
    metadata = attribution.to_metadata()
    metadata.update({
        "subscription_created_via": "gift_claim",
        "duration_days": duration_days,
    })

    # Create history record with attribution
    SubscriptionHistory.objects.create(
        subscription=subscription,
        user=user,
        event_type=SubscriptionHistory.EventType.GIFT_RECEIVED,
        new_plan_id=gift_plan.id,
        new_status=subscription.status,
        metadata=metadata,
        notes=f"Gift claimed from {attribution.claimed_from_email}"
    )

    return subscription


@transaction.atomic
def extend_subscription_with_gift(
    subscription: Subscription,
    gift_plan: Plan,
    duration_days: int,
    attribution: GiftAttribution,
    request=None,
) -> Subscription:
    """
    Extend an existing subscription with a gift.

    Called when a user WITH an active subscription claims a gift.
    EXTENDS the existing subscription (does NOT cancel it).

    The extension adds the gift duration to the current expiry date
    if the subscription is active, or from now if expired.

    Args:
        subscription: Existing subscription to extend
        gift_plan: Plan being gifted (may be same or different tier)
        duration_days: Days to add
        attribution: GiftAttribution with source, gift_id, claimed_from_email
        request: Optional request for metadata

    Returns:
        The extended Subscription (same object, updated)
    """
    # Calculate new expiry
    if subscription.is_active and subscription.expires_at:
        # Add to existing expiry
        new_expires_at = subscription.expires_at + timedelta(days=duration_days)
    else:
        # Start from now
        new_expires_at = timezone.now() + timedelta(days=duration_days)

    # Store old values for history
    old_expires_at = subscription.expires_at
    old_plan_id = subscription.plan_id

    # Update subscription
    subscription.expires_at = new_expires_at

    # If gift plan is different tier, we could upgrade here
    # For now, we keep existing plan but note the gift in metadata
    if gift_plan.id != subscription.plan_id:
        # Plan upgrade scenario - could be implemented here
        # For now, just extend duration on current plan
        pass

    subscription.save(update_fields=['expires_at'])

    # Build metadata with attribution
    metadata = attribution.to_metadata()
    metadata.update({
        "extension_days": duration_days,
        "previous_expiry": old_expires_at.isoformat() if old_expires_at else None,
        "new_expiry": new_expires_at.isoformat(),
        "gift_plan_id": str(gift_plan.id),
        "current_plan_id": str(subscription.plan_id),
    })

    # Determine event type
    if old_plan_id != gift_plan.id:
        event_type = SubscriptionHistory.EventType.UPGRADED
    else:
        event_type = SubscriptionHistory.EventType.RENEWED

    # Create history record
    SubscriptionHistory.objects.create(
        subscription=subscription,
        user=subscription.user,
        event_type=event_type,
        previous_plan_id=old_plan_id,
        new_plan_id=subscription.plan_id,
        previous_status=subscription.status,
        new_status=subscription.status,
        metadata=metadata,
        notes=f"Subscription extended by {duration_days} days via gift from {attribution.claimed_from_email}"
    )

    return subscription


def get_active_subscription(user: User) -> Optional[Subscription]:
    """
    Get the user's active subscription if any.

    Args:
        user: User to check

    Returns:
        Active Subscription or None
    """
    return Subscription.objects.filter(
        user=user,
        is_active=True,
        status=Subscription.Status.ACTIVE
    ).first()


def has_active_subscription(user: User) -> bool:
    """
    Check if user has an active subscription.

    Args:
        user: User to check

    Returns:
        True if user has active subscription
    """
    return Subscription.objects.filter(
        user=user,
        is_active=True,
        status=Subscription.Status.ACTIVE
    ).exists()


def get_pricing_country(request) -> Optional[str]:
    """
    Get the country code for pricing based on request.

    This is a wrapper around the service function to maintain the API boundary.

    Args:
        request: HTTP request object

    Returns:
        Country code or None
    """
    from .services import get_pricing_country as _get_pricing_country
    return _get_pricing_country(request)


def get_region_for_country(country_code: str) -> Optional[str]:
    """
    Get region for a country code.

    This is a wrapper around the service function to maintain the API boundary.

    Args:
        country_code: Two-letter country code

    Returns:
        Region code or None
    """
    from .services import get_region_for_country as _get_region
    return _get_region(country_code)
