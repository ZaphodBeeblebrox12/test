"""
Growth app views - High-conversion referral UI
FIXED: Uses correct field names from ReferralReward model
"""
import logging
from decimal import Decimal

from django.views import View
from django.views.generic import TemplateView
from django.http import HttpResponseRedirect, JsonResponse
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from django.shortcuts import render
from django.conf import settings

logger = logging.getLogger(__name__)


def get_referral_config():
    """
    Get referral configuration from ReferralSettings.
    Returns dict with reward_amount_cents and hold_duration_hours.
    Uses sensible defaults if settings don't exist.
    """
    try:
        from apps.growth.models import ReferralSettings
        settings_obj = ReferralSettings.objects.first()

        if settings_obj:
            # Try to get reward amount - check common field names
            reward_cents = 1000  # Default $10
            for field_name in ['reward_amount_cents', 'reward_cents', 'amount_cents', 'referral_reward_cents']:
                if hasattr(settings_obj, field_name):
                    reward_cents = getattr(settings_obj, field_name) or 1000
                    break

            # Try to get hold duration
            hold_hours = 72  # Default 72 hours
            for field_name in ['hold_duration_hours', 'hold_hours', 'pending_hours', 'unlock_hours']:
                if hasattr(settings_obj, field_name):
                    hold_hours = getattr(settings_obj, field_name) or 72
                    break

            return {
                'reward_amount_cents': reward_cents,
                'hold_duration_hours': hold_hours,
                'from_db': True
            }
    except Exception as e:
        logger.debug(f"Could not load ReferralSettings: {e}")

    # Return defaults
    return {
        'reward_amount_cents': 1000,  # $10 default
        'hold_duration_hours': 72,     # 72 hours default
        'from_db': False
    }


class CaptureReferralCodeView(View):
    """Captures referral code from URL and stores in session."""

    def get(self, request, code):
        from apps.growth.services import ReferralService

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
        from apps.growth.services import ReferralService

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
        logger.info(f"Pending referral flag cleared for user {user.id}")
        return None


class ReferralDashboardView(LoginRequiredMixin, TemplateView):
    """
    User-facing referral dashboard with high-conversion design.
    FIXED: Uses correct field names from model
    """
    template_name = "growth/referral_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        # Get referral configuration (from DB or defaults)
        config = get_referral_config()
        reward_amount_cents = config['reward_amount_cents']
        reward_amount_dollars = reward_amount_cents / 100

        # Get or create referral code
        from apps.growth.models import ReferralCode
        referral_code = ReferralCode.get_or_create_for_user(user)
        context["referral_code"] = referral_code
        context["referral_link"] = self.request.build_absolute_uri(
            f"/growth/r/{referral_code.code}/"
        )

        # OPTIMIZED SHARE MESSAGE (high conversion)
        site_name = getattr(settings, 'SITE_NAME', 'TradeAdmin')
        context["site_name"] = site_name
        context["optimized_share_message"] = (
            f"Get free premium access on {site_name}!\n\n"
            f"Use my link:\n"
            f"{context['referral_link']}\n\n"
            f"You'll get discounts and exclusive access, and I'll get extra subscription days"
        )

        # Get reward stats
        from apps.growth.services import UserRewardBalance, ReferralService, ReferralRewardService

        balance = UserRewardBalance(user)
        stats = ReferralService.get_referral_stats(user)

        # Add display-formatted amounts
        stats['total_rewards_available_cents'] = balance.total_cents
        stats['total_rewards_available_display'] = balance.total_display
        stats['pending_rewards_display'] = f"${stats.get('pending_rewards_cents', 0) / 100:.2f}"
        context["referral_stats"] = stats
        context["reward_balance_cents"] = balance.total_cents
        context["reward_balance_display"] = balance.total_display
        context["reward_buckets"] = balance.reward_count

        # Get user's current plan for accurate calculations
        from apps.subscriptions.api import get_active_subscription
        active_sub = get_active_subscription(user)

        if active_sub and active_sub.plan_price:
            plan_price_cents = active_sub.plan_price.price_cents
            plan_price_dollars = plan_price_cents / 100
            # Calculate days based on plan interval
            if active_sub.plan_price.interval == 'yearly':
                plan_duration_days = 365
            else:
                plan_duration_days = 30
        else:
            # Defaults
            plan_price_cents = 1000
            plan_price_dollars = 10.00
            plan_duration_days = 30

        context["extension_estimate"] = ReferralRewardService.estimate_extension_for_balance(
            user, 
            plan_price_cents=plan_price_cents, 
            plan_duration_days=plan_duration_days
        )

        # ===== BEHAVIORAL TRIGGERS =====

        # 1. VIRAL TRIGGER: Next reward estimate
        if plan_price_dollars > 0:
            days_per_reward = max(1, int((reward_amount_dollars / plan_price_dollars) * plan_duration_days))
        else:
            days_per_reward = 3  # Fallback

        context['next_reward_estimate'] = {
            'amount': f"{reward_amount_dollars:.0f}",
            'days': days_per_reward,
            'config_source': 'database' if config['from_db'] else 'default'
        }

        # 2. PENDING URGENCY: Soonest unlock
        from apps.growth.models import ReferralReward
        pending_reward = ReferralReward.objects.filter(
            referral__referrer=user,
            status='pending',
            unlocked_at__gt=timezone.now()
        ).order_by('unlocked_at').first()

        if pending_reward:
            days_left = (pending_reward.unlocked_at.date() - timezone.now().date()).days
            context['pending_unlock_soonest'] = {
                'days_left': max(0, days_left),
                'unlocks_at': pending_reward.unlocked_at
            }

        # 3. LOSS AVERSION: Next billing date
        if active_sub and active_sub.expires_at:
            days_until_billing = (active_sub.expires_at.date() - timezone.now().date()).days
            context['next_billing_date'] = active_sub.expires_at
            context['billing_urgency'] = 'urgent' if days_until_billing <= 7 else 'normal'
            context['days_until_billing'] = days_until_billing

        # 4. NEWLY UNLOCKED: Celebration trigger
        # FIXED: Use 'created_at' instead of 'credited_at'
        # Status 'credited' means it's available (unlocked)
        newly_unlocked = ReferralReward.objects.filter(
            referral__referrer=user,
            status='credited',
            created_at__gte=timezone.now() - timezone.timedelta(hours=24)
        ).first()

        if newly_unlocked:
            # Calculate extra days based on actual reward amount and plan price
            reward_value = newly_unlocked.amount_cents / 100
            if plan_price_dollars > 0:
                extra_days = max(1, int((reward_value / plan_price_dollars) * plan_duration_days))
            else:
                extra_days = 3

            context['newly_unlocked_reward'] = {
                'amount_display': newly_unlocked.amount_display,
                'extra_days': extra_days
            }

        # Recent rewards
        recent_rewards = ReferralRewardService.get_user_rewards(user)[:10]
        context["recent_rewards"] = recent_rewards

        # Recent referrals
        from apps.growth.models import Referral
        recent_referrals = Referral.objects.filter(
            referrer=user
        ).select_related('referred_user').order_by('-created_at')[:10]
        context["recent_referrals"] = recent_referrals

        # Pass config for debugging (optional)
        context['referral_config'] = config

        return context


class ReferralRewardsAPIView(LoginRequiredMixin, View):
    """API endpoint for fetching user's referral rewards data."""

    def get(self, request):
        from apps.growth.services import UserRewardBalance, ReferralService, ReferralRewardService

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
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "referred_user": r.referral.referred_user.username if r.referral and r.referral.referred_user else None,
            }
            for r in rewards
        ]

        return JsonResponse({
            "balance_cents": balance.total_cents,
            "balance_display": balance.total_display,
            "reward_buckets": balance.reward_count,
            "total_referrals": stats.get("total_referrals", 0),
            "completed_referrals": stats.get("completed", 0),
            "pending_referrals": stats.get("pending", 0),
            "pending_rewards_cents": stats.get("pending_rewards_cents", 0),
            "recent_rewards": rewards_data,
        })


class AdminReferralRewardsView(LoginRequiredMixin, TemplateView):
    """Admin view for managing referral rewards."""
    template_name = "growth/admin/referral_rewards_overview.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        from django.db.models import Sum, Count
        from apps.growth.models import ReferralReward

        reward_stats = ReferralReward.objects.aggregate(
            total_rewards=Count("id"),
            total_issued_cents=Sum("amount_cents"),
            total_used_cents=Sum("used_amount_cents"),
        )

        context["total_rewards"] = reward_stats.get("total_rewards") or 0
        context["total_issued"] = (reward_stats.get("total_issued_cents") or 0) / 100
        context["total_used"] = (reward_stats.get("total_used_cents") or 0) / 100
        context["total_outstanding"] = context["total_issued"] - context["total_used"]

        context["recent_rewards"] = ReferralReward.objects.select_related(
            "referrer", "referral__referred_user"
        ).order_by("-created_at")[:20]

        return context
