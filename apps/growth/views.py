"""
Growth views for gift claiming and management.

Views:
- GiftClaimView: Main claim endpoint (handles both auth and anon users)
- GiftClaimSuccessView: Post-claim success page
- GiftClaimErrorView: Error display page
- LegacyGiftClaimView: Legacy gift_code claim endpoint
- LegacyGiftClaimSuccessView: Legacy claim success page
"""
import logging

from django.views import View
from django.views.generic import TemplateView
from django.shortcuts import redirect, render, get_object_or_404
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.http import HttpResponseRedirect, Http404

from .models import GiftInvite
from .services import (
    GiftService,
    GiftClaimService,
    GiftEmailService,
    LegacyGiftService,
    GiftAlreadyClaimedError,
    GiftExpiredError,
    GiftEmailMismatchError,
    SelfGiftError,
    GiftServiceError,
    InvalidGiftCodeError,
)
from .forms import LegacyGiftClaimForm

User = get_user_model()
logger = logging.getLogger(__name__)


class GiftClaimView(View):
    """
    Main gift claim endpoint.

    Handles:
    1. Authenticated users: Validate and claim immediately
    2. Anonymous users: Store pending claim, redirect to login

    URL pattern: /growth/claim/<token>/
    """

    template_name = "growth/claim.html"
    login_url = "/accounts/login/"
    signup_url = "/accounts/signup/"

    def get(self, request, token):
        """Handle GET request to claim page."""
        # Look up the gift
        gift_invite = GiftService.get_gift_by_token(token)

        if not gift_invite:
            logger.warning(f"Invalid claim token attempted: {token[:8]}...")
            return self._render_error(
                request,
                "Invalid Gift Link",
                "This gift link is invalid or has been removed."
            )

        # Check if already claimed
        if gift_invite.status == GiftInvite.Status.CLAIMED:
            return self._render_error(
                request,
                "Gift Already Claimed",
                "This gift has already been claimed by someone else.",
                status="claimed"
            )

        # Check if expired
        if gift_invite.is_expired:
            return self._render_error(
                request,
                "Gift Expired",
                f"This gift expired on {gift_invite.expires_at.strftime('%B %d, %Y')}.",
                status="expired"
            )

        # Check if revoked
        if gift_invite.status == GiftInvite.Status.REVOKED:
            return self._render_error(
                request,
                "Gift Revoked",
                "This gift has been revoked by the sender."
            )

        # If user is authenticated
        if request.user.is_authenticated:
            return self._handle_authenticated_claim(request, gift_invite, token)

        # Anonymous user - store pending claim and redirect to login
        return self._handle_anonymous_claim(request, gift_invite, token)

    def _handle_authenticated_claim(self, request, gift_invite, token):
        """Handle claim for authenticated user."""
        user = request.user

        # Validate email match
        if not user.email:
            return self._render_error(
                request,
                "Email Required",
                "Your account needs an email address to claim this gift. "
                "Please update your profile.",
                action_url=reverse("profile"),
                action_text="Update Profile"
            )

        try:
            # Attempt the claim
            subscription = GiftClaimService.claim_gift(
                token=token,
                user=user,
                request=request,
            )

            # Send confirmation email
            GiftEmailService.send_claim_confirmation_email(
                user=user,
                subscription=subscription,
                gift_invite=gift_invite,
            )

            # Redirect to success page
            return redirect(
                "growth:claim_success",
                subscription_id=subscription.id
            )

        except GiftEmailMismatchError as e:
            # User's email doesn't match gift recipient
            return self._render_error(
                request,
                "Email Mismatch",
                str(e),
                status="email_mismatch",
                gift_sender=gift_invite.gift_subscription.from_user.username,
                gift_recipient=gift_invite.recipient_email,
                user_email=user.email
            )

        except GiftAlreadyClaimedError:
            return self._render_error(
                request,
                "Already Claimed",
                "This gift was just claimed by someone else.",
                status="claimed"
            )

        except GiftExpiredError:
            return self._render_error(
                request,
                "Gift Expired",
                "This gift expired while you were claiming it.",
                status="expired"
            )

        except SelfGiftError:
            return self._render_error(
                request,
                "Cannot Claim Own Gift",
                "You cannot claim a gift that you sent to yourself."
            )

        except Exception as e:
            logger.exception(f"Unexpected error claiming gift: {e}")
            return self._render_error(
                request,
                "Claim Failed",
                "An unexpected error occurred. Please try again later."
            )

    def _handle_anonymous_claim(self, request, gift_invite, token):
        """Handle claim for anonymous user - store and redirect."""
        # Store pending claim
        session_key = request.session.session_key

        if not session_key:
            # Create session if needed
            request.session.create()
            session_key = request.session.session_key

        # Store the pending claim
        GiftClaimService.store_pending_claim(
            token=token,
            session_key=session_key,
            request=request,
        )

        # Store token in session for retrieval after login
        request.session['pending_gift_token'] = token
        request.session['pending_gift_email'] = gift_invite.recipient_email
        request.session.modified = True

        # Show intermediate page with options
        context = {
            'gift_invite': gift_invite,
            'recipient_email': gift_invite.recipient_email,
            'sender_name': gift_invite.gift_subscription.from_user.username,
            'plan_name': gift_invite.gift_subscription.plan.name,
            'duration_days': gift_invite.gift_subscription.duration_days,
            'login_url': self.login_url,
            'signup_url': self.signup_url,
        }

        return render(request, self.template_name, context)

    def _render_error(self, request, title, message, **kwargs):
        """Render error page with context."""
        context = {
            'error_title': title,
            'error_message': message,
        }
        context.update(kwargs)
        return render(request, "growth/claim_error.html", context, status=400)


class GiftClaimSuccessView(LoginRequiredMixin, TemplateView):
    """
    Success page shown after claiming a gift.

    Displays subscription details and confirmation.
    """

    template_name = "growth/claim_success.html"
    login_url = "/accounts/login/"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        from apps.subscriptions.api import get_active_subscription

        subscription_id = kwargs.get('subscription_id')
        try:
            subscription = get_active_subscription(self.request.user)
            if subscription and str(subscription.id) == subscription_id:
                context['subscription'] = subscription
                context['plan'] = subscription.plan
                context['expires_at'] = subscription.expires_at
        except Exception:
            context['subscription'] = None

        return context


class GiftClaimErrorView(TemplateView):
    """Generic error page for gift claims."""

    template_name = "growth/claim_error.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Get error details from query params or session
        context['error_title'] = self.request.GET.get('title', 'Error')
        context['error_message'] = self.request.GET.get('message', 'An error occurred.')
        context['status'] = self.request.GET.get('status', 'error')

        return context


class ProcessPendingClaimsView(View):
    """
    View to process pending claims after user authentication.

    This is called via middleware or signal after successful login/signup.
    It checks for pending claims in the session and processes them.
    """

    @staticmethod
    def process_for_request(request):
        """
        Process any pending claims for the current request.

        Should be called after user authentication.
        Returns the subscription if a claim was processed, None otherwise.
        """
        if not request.user.is_authenticated:
            return None

        session_key = request.session.session_key
        if not session_key:
            return None

        # Check if we have a pending gift token
        pending_token = request.session.get('pending_gift_token')

        subscription = GiftClaimService.process_pending_claims_for_user(
            user=request.user,
            session_key=session_key,
            request=request,
        )

        if subscription:
            # Clean up session
            if 'pending_gift_token' in request.session:
                del request.session['pending_gift_token']
            if 'pending_gift_email' in request.session:
                del request.session['pending_gift_email']
            request.session.modified = True

            # Add success message
            messages.success(
                request,
                "Your gift subscription has been automatically claimed!"
            )

        return subscription


class LegacyGiftClaimView(LoginRequiredMixin, View):
    """
    Legacy gift code claim view.

    Handles the old gift_code-based claiming flow for backward compatibility.
    Users enter a short code (e.g., "ABC123XY") to claim gifts created
    before the token-based system.

    This is SEPARATE from the new token-based flow to avoid ambiguity.

    URL: /growth/claim/code/
    """

    template_name = "growth/legacy_claim.html"
    login_url = "/accounts/login/"

    def get(self, request):
        """Show the legacy claim form."""
        # If gift_code provided in query params, try to claim immediately
        gift_code = request.GET.get('code', '').strip().upper()

        if gift_code:
            return self._attempt_claim(request, gift_code)

        # Show empty form
        return render(request, self.template_name, {
            'form': LegacyGiftClaimForm(),
        })

    def post(self, request):
        """Process the legacy claim form."""
        form = LegacyGiftClaimForm(request.POST)

        if not form.is_valid():
            return render(request, self.template_name, {
                'form': form,
                'error': "Please enter a valid gift code."
            }, status=400)

        gift_code = form.cleaned_data['gift_code'].upper().strip()
        return self._attempt_claim(request, gift_code)

    def _attempt_claim(self, request, gift_code):
        """Attempt to claim the legacy gift."""
        try:
            gift, subscription = LegacyGiftService.claim_legacy_gift(
                gift_code=gift_code,
                user=request.user,
                request=request,
            )

            # Success - redirect to success page
            messages.success(
                request,
                f"Success! You've claimed a {gift.plan.name} subscription for {gift.duration_days} days."
            )
            return redirect('growth:legacy_claim_success', subscription_id=subscription.id)

        except InvalidGiftCodeError:
            return render(request, self.template_name, {
                'form': LegacyGiftClaimForm(initial={'gift_code': gift_code}),
                'error': "Invalid gift code. Please check and try again."
            }, status=400)

        except GiftAlreadyClaimedError:
            return render(request, self.template_name, {
                'form': LegacyGiftClaimForm(initial={'gift_code': gift_code}),
                'error': "This gift has already been claimed."
            }, status=400)

        except GiftExpiredError:
            return render(request, self.template_name, {
                'form': LegacyGiftClaimForm(initial={'gift_code': gift_code}),
                'error': "This gift has expired."
            }, status=400)

        except SelfGiftError:
            return render(request, self.template_name, {
                'form': LegacyGiftClaimForm(initial={'gift_code': gift_code}),
                'error': "You cannot claim your own gift."
            }, status=400)

        except Exception as e:
            logger.exception(f"Error claiming legacy gift: {e}")
            return render(request, self.template_name, {
                'form': LegacyGiftClaimForm(initial={'gift_code': gift_code}),
                'error': "An unexpected error occurred. Please try again later."
            }, status=500)


class LegacyGiftClaimSuccessView(LoginRequiredMixin, TemplateView):
    """Success page for legacy gift code claims."""

    template_name = "growth/legacy_claim_success.html"
    login_url = "/accounts/login/"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        from apps.subscriptions.api import get_active_subscription

        subscription_id = kwargs.get('subscription_id')
        subscription = get_active_subscription(self.request.user)

        if subscription and str(subscription.id) == subscription_id:
            context['subscription'] = subscription
            context['plan'] = subscription.plan
            context['expires_at'] = subscription.expires_at

        return context
