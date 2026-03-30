"""
Referral Dashboard Context Mixin - Drop this into your accounts/views.py

USAGE:
1. Copy this file content
2. In your accounts/views.py, add the import
3. Add ReferralDashboardMixin to your DashboardView class
4. Call super().get_context_data() to get all referral context

Example:
    class DashboardView(LoginRequiredMixin, ReferralDashboardMixin, TemplateView):
        template_name = "accounts/dashboard.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)  # This gets referral data
            # ... add your other context ...
            return context
"""
from django.utils import timezone
from django.conf import settings
from apps.growth.models import ReferralCode, ReferralSettings, ReferralReward, Referral
from apps.growth.services import UserRewardBalance, ReferralService, ReferralRewardService


class ReferralDashboardMixin:
    """
    Mixin to add referral context to any dashboard view.

    Provides:
    - referral_link
    - optimized_share_message (HIGH CONVERSION)
    - referral_stats (with available/pending display)
    - extension_estimate
    - next_reward_estimate (viral trigger)
    - next_billing_date (loss aversion)
    - pending_unlock_soonest (urgency)
    - newly_unlocked_reward (celebration)
    """

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        try:
            # ===== 1. BASIC SETUP =====
            referral_code = ReferralCode.get_or_create_for_user(user)
            context["referral_code"] = referral_code
            context["referral_link"] = self.request.build_absolute_uri(
                f"/growth/r/{referral_code.code}/"
            )

            # ===== 2. OPTIMIZED SHARE MESSAGE (HIGH CONVERSION) =====
            site_name = getattr(settings, 'SITE_NAME', 'TradeAdmin')
            referral_link = context["referral_link"]

            # Primary message - mutual benefits, recipient first
            context["optimized_share_message"] = (
                f"Get free premium access on {site_name}!\n\n"
                f"Use my link:\n"
                f"{referral_link}\n\n"
                f"You'll get discounts and exclusive access, and I'll get extra subscription days 🚀"
            )

            context["site_name"] = site_name

            # ===== 3. BALANCE & STATS =====
            balance = UserRewardBalance(user)
            stats = ReferralService.get_referral_stats(user)
            stats['total_rewards_available_cents'] = balance.total_cents
            stats['total_rewards_available_display'] = balance.total_display
            stats['pending_rewards_display'] = f"${stats.get('pending_rewards_cents', 0) / 100:.2f}"
            context["referral_stats"] = stats

            # ===== 4. EXTENSION ESTIMATE =====
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

            # ===== 5. LOSS AVERSION TRIGGERS =====
            if active_sub and active_sub.expires_at:
                context["next_billing_date"] = active_sub.expires_at
                days_until = (active_sub.expires_at.date() - timezone.now().date()).days
                context["billing_urgency"] = 'urgent' if days_until <= 3 else 'normal'

            # ===== 6. VIRAL TRIGGERS =====
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

            # ===== 7. PENDING URGENCY =====
            pending_reward = ReferralReward.objects.filter(
                referral__referrer=user,
                status='pending',
                unlocked_at__gt=timezone.now()
            ).order_by('unlocked_at').first()

            if pending_reward:
                days_left = (pending_reward.unlocked_at.date() - timezone.now().date()).days
                context["pending_unlock_soonest"] = {
                    "unlocks_at": pending_reward.unlocked_at,
                    "days_left": max(0, days_left)
                }

            # ===== 8. NEWLY UNLOCKED (CELEBRATION) =====
            newly_unlocked = ReferralReward.objects.filter(
                referral__referrer=user,
                status='credited',
                credited_at__gte=timezone.now() - timezone.timedelta(hours=24)
            ).first()

            if newly_unlocked:
                extra_days = max(1, int((newly_unlocked.amount_cents / 100) / (plan_price_cents / 100 / plan_duration_days)))
                context["newly_unlocked_reward"] = {
                    "amount_display": newly_unlocked.amount_display,
                    "extra_days": extra_days
                }

        except Exception as e:
            # Fail silently - don't break dashboard if referral system errors
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error loading referral context: {e}")
            context["referral_error"] = True

        return context
