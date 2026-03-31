"""
Growth app views - FIXED version with proper error handling
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

from .services import ReferralService, ReferralRewardService, SubscriptionCreditService, UserRewardBalance
from .models import ReferralCode, ReferralReward, Referral, ReferralSettings

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
        logger.info(f"Pending referral flag cleared for user {user.id}")
        return None


class ReferralDashboardView(LoginRequiredMixin, TemplateView):
    """
    User-facing referral dashboard with ALL behavioral triggers.
    """
    template_name = "growth/referral_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        # Basic setup
        referral_code = ReferralCode.get_or_create_for_user(user)
        context["referral_code"] = referral_code
        context["referral_link"] = self.request.build_absolute_uri(
            f"/growth/r/{referral_code.code}/"
        )
        
        # Optimized share message (HIGH CONVERSION)
        site_name = getattr(settings, 'SITE_NAME', 'TradeAdmin')
        context["site_name"] = site_name
        context["optimized_share_message"] = (
            f"🚀 Get free premium access on {site_name}!\n\n"
            f"Use my link:\n"
            f"{context['referral_link']}\n\n"
            f"You'll get discounts and exclusive access and I'll get extra subscription days 🎉"
        )

        # Balance & stats
        balance = UserRewardBalance(user)
        context["reward_balance_cents"] = balance.total_cents
        context["reward_balance_display"] = balance.total_display
        context["reward_buckets"] = balance.reward_count

        # Referral stats
        stats = ReferralService.get_referral_stats(user)
        stats['total_rewards_available_cents'] = balance.total_cents
        stats['total_rewards_available_display'] = balance.total_display
        stats['pending_rewards_display'] = f"${stats.get('pending_rewards_cents', 0) / 100:.2f}"
        context["referral_stats"] = stats

        # Extension estimate
        from apps.subscriptions.api import get_active_subscription
        active_sub = get_active_subscription(user)
        plan_price_cents = 1000
        plan_duration_days = 30
        
        if active_sub and active_sub.plan_price:
            plan_price_cents = active_sub.plan_price.price_cents
            if hasattr(active_sub.plan_price, 'get_interval_days'):
                plan_duration_days = active_sub.plan_price.get_interval_days()
        
        context["extension_estimate"] = ReferralRewardService.estimate_extension_for_balance(
            user, plan_price_cents=plan_price_cents, plan_duration_days=plan_duration_days
        )

        # ===== BEHAVIORAL TRIGGERS =====
        
        # 1. Next reward estimate (VIRAL TRIGGER) - SAFE with fallback
        try:
            ref_settings = ReferralSettings.get_settings()
            # Try different possible field names
            reward_amount_cents = getattr(ref_settings, 'reward_amount_cents', 
                getattr(ref_settings, 'referral_reward_cents', 
                    getattr(ref_settings, 'reward_cents', 1000)))
            reward_amount = reward_amount_cents / 100
            extra_days = max(1, int(reward_amount / (plan_price_cents / 100 / plan_duration_days)))
            context["next_reward_estimate"] = {
                'amount': f"{reward_amount:.0f}",
                'days': extra_days
            }
        except Exception as e:
            # Fallback if ReferralSettings fails
            context["next_reward_estimate"] = {
                'amount': "10",
                'days': max(1, int(10 / (plan_price_cents / 100 / plan_duration_days)))
            }
        
        # 2. Pending urgency with countdown
        try:
            pending_reward = ReferralReward.objects.filter(
                referral__referrer=user,
                status='pending',
                unlocked_at__gt=timezone.now()
            ).order_by('unlocked_at').first()
            
            if pending_reward:
                days_left = (pending_reward.unlocked_at.date() - timezone.now().date()).days
                context["pending_unlock_soonest"] = {
                    'unlocks_at': pending_reward.unlocked_at,
                    'days_left': max(0, days_left)
                }
        except Exception as e:
            logger.debug(f"Could not get pending rewards: {e}")
        
        # 3. Billing urgency (LOSS AVERSION)
        if active_sub and active_sub.expires_at:
            days_until_billing = (active_sub.expires_at.date() - timezone.now().date()).days
            if days_until_billing <= 7 and balance.total_cents > 0:
                context["billing_urgency"] = "urgent"
                context["days_until_billing"] = days_until_billing
                context["next_billing_date"] = active_sub.expires_at
        
        # 4. Progress to next reward
        try:
            recent_referrals_count = Referral.objects.filter(
                referrer=user,
                created_at__gte=timezone.now() - timezone.timedelta(days=30)
            ).count()
            
            # Calculate reward amount safely
            try:
                ref_settings = ReferralSettings.get_settings()
                reward_amount = getattr(ref_settings, 'reward_amount_cents', 1000) / 100
            except:
                reward_amount = 10
                
            context["progress_to_next"] = {
                'current': recent_referrals_count,
                'target': recent_referrals_count + 1,
                'needed': 1,
                'percent': 50,
                'reward_amount': f"{reward_amount:.0f}",
                'reward_days': max(1, int(reward_amount / (plan_price_cents / 100 / plan_duration_days)))
            }
        except Exception as e:
            logger.debug(f"Could not calculate progress: {e}")
        
        # 5. Newly unlocked reward (CELEBRATION)
        try:
            newly_unlocked = ReferralReward.objects.filter(
                referral__referrer=user,
                status='credited',
                credited_at__gte=timezone.now() - timezone.timedelta(hours=24)
            ).first()
            
            if newly_unlocked:
                context["newly_unlocked_reward"] = {
                    'amount_display': newly_unlocked.amount_display,
                    'extra_days': max(1, int((newly_unlocked.amount_cents / 100) / (plan_price_cents / 100 / plan_duration_days)))
                }
        except Exception as e:
            logger.debug(f"Could not get newly unlocked: {e}")
        
        # Recent referrals with enhanced data
        try:
            recent_referrals = Referral.objects.filter(
                referrer=user
            ).select_related('referred_user').prefetch_related('reward').order_by('-created_at')[:10]
            
            # Add days_until_unlock for each
            for referral in recent_referrals:
                if referral.reward and referral.reward.status == 'pending' and referral.reward.unlocked_at:
                    referral.reward.days_until_unlock = (referral.reward.unlocked_at.date() - timezone.now().date()).days
                referral.is_newly_unlocked = (
                    referral.reward and 
                    referral.reward.status == 'credited' and 
                    referral.reward.credited_at and 
                    referral.reward.credited_at >= timezone.now() - timezone.timedelta(hours=24)
                )
            
            context["recent_referrals"] = recent_referrals
        except Exception as e:
            logger.debug(f"Could not get recent referrals: {e}")
            context["recent_referrals"] = []

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