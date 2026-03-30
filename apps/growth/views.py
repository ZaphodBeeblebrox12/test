"""Growth app views - Complete with behavioral triggers"""
import logging
from django.views import View
from django.views.generic import TemplateView
from django.http import HttpResponseRedirect, JsonResponse
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
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
            messages.info(request, _("Referral code applied!"))
        else:
            messages.warning(request, _("Invalid referral code."))
        return HttpResponseRedirect("/accounts/signup/")


class ReferralDashboardView(LoginRequiredMixin, TemplateView):
    """Complete referral dashboard with behavioral triggers."""
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

        # OPTIMIZED SHARE MESSAGE (HIGH CONVERSION)
        site_name = getattr(settings, 'SITE_NAME', 'TradeAdmin')
        context["optimized_share_message"] = (
            f"Get free premium access on {site_name}!\n\n"
            f"Use my link:\n"
            f"{context['referral_link']}\n\n"
            f"You'll get discounts and exclusive access, and I'll get extra subscription days"
        )
        context["site_name"] = site_name

        # Balance & stats
        balance = UserRewardBalance(user)
        stats = ReferralService.get_referral_stats(user)
        stats['total_rewards_available_cents'] = balance.total_cents
        stats['total_rewards_available_display'] = balance.total_display
        stats['pending_rewards_display'] = f"${stats.get('pending_rewards_cents', 0) / 100:.2f}"
        context["referral_stats"] = stats

        # Extension estimate
        from apps.subscriptions.api import get_active_subscription
        active_sub = get_active_subscription(user)
        plan_price_cents, plan_duration_days = 1000, 30
        if active_sub and active_sub.plan_price:
            plan_price_cents = active_sub.plan_price.price_cents

        context["extension_estimate"] = ReferralRewardService.estimate_extension_for_balance(
            user, plan_price_cents=plan_price_cents, plan_duration_days=plan_duration_days
        )

        # Loss aversion triggers
        if active_sub and active_sub.expires_at:
            context["next_billing_date"] = active_sub.expires_at
            days_until = (active_sub.expires_at.date() - timezone.now().date()).days
            context["billing_urgency"] = 'urgent' if days_until <= 3 else 'normal'

        # Viral triggers
        ref_settings = ReferralSettings.get_settings()
        if ref_settings:
            reward_amount = ref_settings.reward_amount_cents / 100
            reward_days = max(1, int(reward_amount / (plan_price_cents / 100 / plan_duration_days)))
            context["next_reward_estimate"] = {
                "amount": f"{reward_amount:.0f}", 
                "days": reward_days
            }
        else:
            context["next_reward_estimate"] = {"amount": "10", "days": 6}

        # Pending urgency
        pending = ReferralReward.objects.filter(
            referral__referrer=user, 
            status='pending', 
            unlocked_at__gt=timezone.now()
        ).order_by('unlocked_at').first()

        if pending:
            context["pending_unlock_soonest"] = {
                "unlocks_at": pending.unlocked_at,
                "days_left": max(0, (pending.unlocked_at.date() - timezone.now().date()).days)
            }

        # Newly unlocked (celebration)
        newly = ReferralReward.objects.filter(
            referral__referrer=user,
            status='credited',
            credited_at__gte=timezone.now() - timezone.timedelta(hours=24)
        ).first()

        if newly:
            extra_days = max(1, int((newly.amount_cents / 100) / (plan_price_cents / 100 / plan_duration_days)))
            context["newly_unlocked_reward"] = {
                "amount_display": newly.amount_display, 
                "extra_days": extra_days
            }

        # Recent referrals
        context["recent_referrals"] = Referral.objects.filter(
            referrer=user
        ).select_related('referred_user').order_by('-created_at')[:10]

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

        context["settings"] = ReferralSettings.get_settings()

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
