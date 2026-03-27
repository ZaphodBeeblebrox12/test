"""
Growth API endpoints for gift operations.

This module provides REST API endpoints for:
- Claiming gifts by token (new flow)
- Claiming gifts by code (legacy flow)
"""
import logging

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.throttling import AnonRateThrottle

from apps.subscriptions.api import get_active_subscription

from .services import (
    GiftService,
    GiftClaimService,
    LegacyGiftService,
    GiftAlreadyClaimedError,
    GiftExpiredError,
    GiftEmailMismatchError,
    SelfGiftError,
    InvalidGiftCodeError,
    AttributionRequiredError,
)

logger = logging.getLogger(__name__)


class ClaimCodeThrottle(AnonRateThrottle):
    """Rate limiting for gift code claims."""
    rate = '10/minute'


class ClaimGiftCodeView(APIView):
    """
    POST /api/growth/gifts/claim-code/

    Legacy gift code claim endpoint.

    Accepts a gift_code (e.g., "ABC123XY") and claims the gift for the authenticated user.
    This is the legacy flow that works with GiftSubscription.gift_code.

    Request:
        {
            "gift_code": "ABC123XY"
        }

    Response:
        {
            "success": true,
            "subscription_id": "uuid",
            "plan_name": "Pro",
            "duration_days": 30,
            "expires_at": "2024-12-31T23:59:59Z",
            "extended": false  // true if existing subscription was extended
        }

    Errors:
        400: Invalid code, already claimed, expired, self-gifting
        401: Not authenticated
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [ClaimCodeThrottle]

    def post(self, request):
        """Handle gift code claim."""
        gift_code = request.data.get('gift_code', '').strip().upper()

        if not gift_code:
            return Response(
                {
                    'success': False,
                    'error': 'gift_code_required',
                    'message': 'Gift code is required.'
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        user = request.user

        # Check if user has existing subscription (for response)
        had_existing = get_active_subscription(user) is not None

        try:
            # Claim via legacy service
            gift, subscription = LegacyGiftService.claim_legacy_gift(
                gift_code=gift_code,
                user=user,
                request=request
            )

            # Determine if we extended or created
            extended = had_existing

            logger.info(
                f"Legacy gift claimed: code={gift_code}, user={user.id}, "
                f"subscription={subscription.id}, extended={extended}"
            )

            return Response({
                'success': True,
                'subscription_id': str(subscription.id),
                'plan_name': gift.plan.name,
                'duration_days': gift.duration_days,
                'expires_at': subscription.expires_at.isoformat() if subscription.expires_at else None,
                'extended': extended,
                'message': f'Successfully claimed {gift.plan.name} subscription for {gift.duration_days} days.'
            })

        except InvalidGiftCodeError:
            return Response(
                {
                    'success': False,
                    'error': 'invalid_code',
                    'message': 'Invalid gift code. Please check and try again.'
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        except GiftAlreadyClaimedError:
            return Response(
                {
                    'success': False,
                    'error': 'already_claimed',
                    'message': 'This gift has already been claimed.'
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        except GiftExpiredError:
            return Response(
                {
                    'success': False,
                    'error': 'expired',
                    'message': 'This gift has expired.'
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        except SelfGiftError:
            return Response(
                {
                    'success': False,
                    'error': 'self_gift',
                    'message': 'You cannot claim your own gift.'
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        except AttributionRequiredError as e:
            logger.error(f"Attribution error in claim: {e}")
            return Response(
                {
                    'success': False,
                    'error': 'attribution_error',
                    'message': 'System error: attribution failed.'
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        except Exception as e:
            logger.exception(f"Unexpected error claiming legacy gift: {e}")
            return Response(
                {
                    'success': False,
                    'error': 'system_error',
                    'message': 'An unexpected error occurred. Please try again later.'
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ClaimTokenView(APIView):
    """
    POST /api/growth/gifts/claim-token/

    Token-based gift claim endpoint (for API clients).

    This is separate from the web view at /growth/claim/<token>/
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Handle token claim."""
        token = request.data.get('token', '').strip()

        if not token:
            return Response(
                {
                    'success': False,
                    'error': 'token_required',
                    'message': 'Token is required.'
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        user = request.user
        had_existing = get_active_subscription(user) is not None

        try:
            subscription = GiftClaimService.claim_gift(
                token=token,
                user=user,
                request=request
            )

            logger.info(
                f"Token gift claimed: user={user.id}, subscription={subscription.id}"
            )

            return Response({
                'success': True,
                'subscription_id': str(subscription.id),
                'expires_at': subscription.expires_at.isoformat() if subscription.expires_at else None,
                'extended': had_existing
            })

        except GiftAlreadyClaimedError:
            return Response(
                {
                    'success': False,
                    'error': 'already_claimed',
                    'message': 'This gift has already been claimed.'
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        except GiftExpiredError:
            return Response(
                {
                    'success': False,
                    'error': 'expired',
                    'message': 'This gift has expired.'
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        except GiftEmailMismatchError:
            return Response(
                {
                    'success': False,
                    'error': 'email_mismatch',
                    'message': 'This gift was sent to a different email address.'
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        except SelfGiftError:
            return Response(
                {
                    'success': False,
                    'error': 'self_gift',
                    'message': 'You cannot claim your own gift.'
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        except Exception as e:
            logger.exception(f"Error claiming token gift: {e}")
            return Response(
                {
                    'success': False,
                    'error': 'system_error',
                    'message': 'An unexpected error occurred.'
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
