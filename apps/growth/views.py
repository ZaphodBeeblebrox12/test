"""
Growth app views.
"""
import logging

from django.views import View
from django.views.generic import TemplateView
from django.http import HttpResponseRedirect, JsonResponse
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils.translation import gettext_lazy as _
from django.shortcuts import render

from .services import ReferralService, ReferralRewardService, SubscriptionCreditService, UserRewardBalance
from .models import ReferralCode

logger = logging.getLogger(__name__)


class CaptureReferralCodeView(View):
    """Captures referral code from URL and stores in session."""

    def get(self, request, code):
        referral_code = ReferralService.get_code_by_string(code)

        if referral_code:
            request.session["referral_code"] = referral_code.code
            request.session.modified = True
            logger.info(f"Referral code {code} captured in session")
            messages.info(request, _("You have a referral code applied!"))
        else:
            logger.warning(f"Invalid referral code attempted: {code}")
            messages.warning(request, _("Invalid referral code."))

        return HttpResponseRedirect("/accounts/signup/")


class ReferralSignupMixin:
    """Mixin for signup views to handle referral code processing."""

    @classmethod
    def process_referral(cls, request, user):
        """Process referral code from session for a newly created user."""
        referral_code = request.session.pop("referral_code", None)

        if referral_code:
            referral = ReferralService.record_referral_signup(user, referral_code)
            if referral:
                logger.info(f"Referral recorded for user {user.id}")
                request.session["pending_referral"] = True
                request.session.modified = True
            else:
                logger.warning(f"Failed to record referral for user {user.id}")

        return referral_code


def complete_referral_on_verification(request, user):
    """Mark referral as completed after email verification."""
    if request.session.pop("pending_referral", None):
        # Note: Referral is now only completed on purchase, not on verification
        # This function is kept for backward compatibility but does not complete referral
        logger.info(f"Pending referral flag cleared for user {user.id} (completion on purchase)")
    return None


class ReferralDashboardView(LoginRequiredMixin, TemplateView):
    """User-facing referral dashboard."""
    template_name = "growth/referral_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        # Get or create referral code
        referral_code = ReferralCode.get_or_create_for_user(user)
        context["referral_code"] = referral_code
        context["referral_link"] = self.request.build_absolute_uri(
            f"/growth/r/{referral_code.code}/"
        )

        # Get reward stats using UserRewardBalance
        balance = UserRewardBalance(user)
        context["reward_balance_cents"] = balance.total_cents
        context["reward_balance_display"] = balance.total_display
        context["reward_buckets"] = balance.reward_count

        # Get referral stats
        context["referral_stats"] = ReferralService.get_referral_stats(user)

        # Estimate extension
        default_plan_price_cents = 1000
        context["extension_estimate"] = ReferralRewardService.estimate_extension_for_balance(
            user, plan_price_cents=default_plan_price_cents, plan_duration_days=30
        )

        # Recent rewards
        context["recent_rewards"] = ReferralRewardService.get_user_rewards(user)[:10]

        return context


class ReferralRewardsAPIView(LoginRequiredMixin, View):
    """API endpoint for fetching user's referral rewards data."""

    def get(self, request):
        user = request.user
        balance = UserRewardBalance(user)
        stats = ReferralService.get_referral_stats(user)
        rewards = ReferralRewardService.get_user_rewards(user)[:5]

        rewards_data = [
            {
                "id": str(r.id),
                "amount_cents": r.amount_cents,
                "amount_display": r.amount_display,
                "status": r.status,
                "available_cents": r.available_amount_cents,
                "created_at": r.created_at.isoformat(),
                "referred_user": r.referral.referred_user.username if r.referral else None,
            }
            for r in rewards
        ]

        return JsonResponse({
            "balance_cents": balance.total_cents,
            "balance_display": balance.total_display,
            "reward_buckets": balance.reward_count,
            "total_referrals": stats["total_referrals"],
            "completed_referrals": stats["completed"],
            "pending_referrals": stats["pending"],
            "pending_rewards_cents": stats.get("pending_rewards_cents", 0),
            "recent_rewards": rewards_data,
        })


class AdminReferralRewardsView(LoginRequiredMixin, TemplateView):
    """Admin view for managing referral rewards."""
    template_name = "growth/admin/referral_rewards_overview.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        from django.db.models import Sum, Count
        from .models import ReferralSettings, ReferralReward

        context["settings"] = ReferralSettings.get_settings()

        reward_stats = ReferralReward.objects.aggregate(
            total_rewards=Count("id"),
            total_issued_cents=Sum("amount_cents"),
            total_used_cents=Sum("used_amount_cents"),
        )

        context["total_rewards"] = reward_stats["total_rewards"] or 0
        context["total_issued"] = (reward_stats["total_issued_cents"] or 0) / 100
        context["total_used"] = (reward_stats["total_used_cents"] or 0) / 100
        context["total_outstanding"] = context["total_issued"] - context["total_used"]

        context["recent_rewards"] = ReferralReward.objects.select_related(
            "referrer", "referral__referred_user"
        ).order_by("-created_at")[:20]

        return context
