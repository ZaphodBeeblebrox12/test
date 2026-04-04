"""
Referral Dashboard Context Mixin - Drop-in replacement for enhanced UX

USAGE:
1. Copy this file to apps/accounts/referral_dashboard_mixin.py
2. Import in your views: from apps.accounts.referral_dashboard_mixin import ReferralDashboardContextMixin
3. Add to your DashboardView class inheritance
4. Call super().get_context_data() to get all referral context

Example:
    class DashboardView(LoginRequiredMixin, ReferralDashboardContextMixin, TemplateView):
        template_name = "accounts/dashboard.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)  # Gets referral data
            # ... add your other context ...
            return context
"""
from decimal import Decimal
from django.utils import timezone
from django.conf import settings
from apps.growth.models import ReferralCode, ReferralSettings, ReferralReward, Referral
from apps.growth.services import UserRewardBalance, ReferralService, ReferralRewardService


class ReferralDashboardContextMixin:
    """
    Enhanced mixin to add referral context with psychology-driven UX.

    Provides:
    - reward_percentage (20% default, currency-aware)
    - available_credits (currency-aware display)
    - estimated_days (only if active plan)
    - estimated_days_per_referral (tangible value)
    - tangible_example (~X days per referral)
    - progress_message ("1 referral away" trigger)
    - reward_timing_note (trust messaging)
    - social_framing (win-win messaging)
    - share_message (safe, conditional)
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

            # ===== 2. REWARD SETTINGS =====
            ref_settings = ReferralSettings.get_settings()
            reward_percentage = ref_settings.default_reward_percentage if ref_settings else Decimal("20.00")
            context["reward_percentage"] = reward_percentage

            # ===== 3. CURRENCY HANDLING =====
            from apps.subscriptions.api import get_active_subscription
            active_sub = get_active_subscription(user)

            currency = "USD"
            if active_sub and hasattr(active_sub, 'plan_price') and active_sub.plan_price:
                currency = active_sub.plan_price.currency
            context["currency"] = currency
            context["currency_symbol"] = self._get_currency_symbol(currency)

            # ===== 4. BALANCE & STATS =====
            balance = UserRewardBalance(user)
            stats = ReferralService.get_referral_stats(user)

            stats['total_rewards_available_cents'] = balance.total_cents
            stats['total_rewards_available_display'] = balance.total_display
            stats['pending_rewards_display'] = f"{context['currency_symbol']}{stats.get('pending_rewards_cents', 0) / 100:.2f}"

            context["referral_stats"] = stats

            # ===== 5. EXTENSION ESTIMATE =====
            plan_price_cents = 1000
            plan_duration_days = 30

            if active_sub and active_sub.plan_price:
                plan_price_cents = active_sub.plan_price.price_cents
                if hasattr(active_sub.plan_price, 'get_interval_days'):
                    plan_duration_days = active_sub.plan_price.get_interval_days()

            context["extension_estimate"] = ReferralRewardService.estimate_extension_for_balance(
                user, plan_price_cents=plan_price_cents, plan_duration_days=plan_duration_days
            )

            # ===== 6. TANGIBLE VALUE PER REFERRAL =====
            if plan_price_cents > 0:
                typical_reward_cents = int(plan_price_cents * float(reward_percentage) / 100)
                days_per_referral = max(1, int(typical_reward_cents / (plan_price_cents / plan_duration_days)))
                context["estimated_days_per_referral"] = days_per_referral
                context["tangible_example"] = f"~{days_per_referral} days"
            else:
                context["estimated_days_per_referral"] = 6
                context["tangible_example"] = "~6 days"

            # ===== 7. PSYCHOLOGY: PROGRESS MESSAGE =====
            completed = stats.get('completed', 0)
            pending = stats.get('pending', 0)

            if completed == 0 and pending == 0:
                context["progress_message"] = "Refer 1 friend to start earning credits"
            elif pending > 0:
                context["progress_message"] = f"{pending} referral(s) pending confirmation"
            else:
                context["progress_message"] = f"You're doing great! {completed} friend(s) joined"

            # ===== 8. PSYCHOLOGY: REWARD TIMING NOTE =====
            context["reward_timing_note"] = "Rewards unlock after subscription is confirmed (up to 3 days)"

            # ===== 9. PSYCHOLOGY: SOCIAL FRAMING =====
            context["social_framing"] = "You earn rewards. Your friend gets a better deal."

            # ===== 10. SAFE SHARE MESSAGE =====
            site_name = getattr(settings, 'SITE_NAME', 'TradeAdmin')
            referral_link = context["referral_link"]

            if active_sub:
                share_message = (
                    f"Get premium access on {site_name}!\n\n"
                    f"Use my link:\n"
                    f"{referral_link}\n\n"
                    f"You'll get exclusive access, and I'll earn credits toward my subscription 🎁"
                )
            else:
                share_message = (
                    f"Join me on {site_name}!\n\n"
                    f"Use my link:\n"
                    f"{referral_link}\n\n"
                    f"Get started with premium features 🚀"
                )
            context["optimized_share_message"] = share_message
            context["site_name"] = site_name

            # ===== 11. REWARD SCALING MESSAGE =====
            context["reward_scaling_message"] = "Rewards scale with the plan your friend chooses"

            # ===== 12. LOSS AVERSION: Billing Urgency =====
            if active_sub and active_sub.expires_at:
                context["next_billing_date"] = active_sub.expires_at
                days_until = (active_sub.expires_at.date() - timezone.now().date()).days
                context["days_until_billing"] = days_until
                context["billing_urgency"] = 'urgent' if days_until <= 3 else 'normal'

            # ===== 13. VIRAL TRIGGERS: Next Reward Estimate =====
            context["next_reward_estimate"] = None

            # ===== 14. PENDING URGENCY =====
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

            # ===== 15. NEWLY UNLOCKED (CELEBRATION) =====
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

            # ===== 16. RECENT REFERRALS =====
            recent_referrals = Referral.objects.filter(
                referrer=user
            ).select_related('referred_user', 'reward').order_by('-created_at')[:10]
            context["recent_referrals"] = recent_referrals

            # ===== 17. REWARD BUCKETS =====
            context["reward_buckets"] = stats.get('completed', 0)

        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error loading referral context: {e}")
            print(f"REFERRAL MIXIN ERROR: {e}")  # TEMP DEBUG
            context["referral_error"] = True

        return context

    def _get_currency_symbol(self, currency_code: str) -> str:
        symbols = {
            'USD': '$',
            'INR': '₹',
            'EUR': '€',
            'GBP': '£',
        }
        return symbols.get(currency_code, '$')