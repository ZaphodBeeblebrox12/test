"""
Account views for community platform.
"""
import hashlib
import hmac
import json
from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework import generics, permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User, UserPreference
from apps.accounts.serializers import (
    UserSerializer, UserProfileSerializer, UserPreferenceSerializer
)
from apps.audit.models import AuditLog
from django.views.generic import TemplateView
from apps.accounts.referral_dashboard_mixin import ReferralDashboardContextMixin
from apps.growth.models import ReferralSettings

# ===== NEW IMPORTS FOR GEO PRICING =====
from apps.subscriptions.services import (
    get_pricing_country,
    resolve_plan_price,
    format_price,
)
from apps.subscriptions.models import GeoPlanPrice


def check_banned(view_func):
    """Decorator to check if user is banned."""
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated and request.user.is_banned:
            return render(request, "accounts/banned.html", {
                "ban_reason": user.ban_reason
            })
        return view_func(request, *args, **kwargs)
    return wrapper


@method_decorator(login_required, name="dispatch")
@method_decorator(check_banned, name="dispatch")
@method_decorator(login_required, name='dispatch')
class DashboardView(View):
    """User dashboard view."""

    def get(self, request):
        from apps.subscriptions.models import Subscription, Plan
        user = request.user

        # Get recent activity
        recent_activity = AuditLog.objects.filter(
            user=user
        ).order_by("-created_at")[:10]

        # Get recent notifications
        from apps.notifications.models import Notification
        recent_notifications = Notification.objects.filter(
            user=user
        ).order_by("-created_at")[:5]

        # Get unread notification count
        unread_count = Notification.objects.filter(
            user=user, is_read=False
        ).count()

        # Get subscription data
        # Multi-subscription safe: a user may hold one ACTIVE subscription
        # PER PRODUCT, so never .get() here — take the most recent one as
        # the "primary" for legacy context (referral estimate etc.). The
        # product-aware cards below render ALL active subs.
        subscription = (Subscription.objects
                        .select_related('plan', 'plan_price')
                        .filter(user=user, is_active=True,
                                status=Subscription.Status.ACTIVE)
                        .first())
        current_plan = subscription.plan if subscription is not None else None

        # Plan features for the subscription card: DB-driven (PlanFeature,
        # ordered by position) with the hardcoded tier list as fallback.
        current_plan_features = []
        if current_plan is not None:
            current_plan_features = (
                [{'text': f.text, 'disabled': False}
                 for f in current_plan.features.order_by('position', 'id')]
            )

        # ===== REPLACED: Plan & Billing section (redesign v3, product-aware) =====
        from django.utils import timezone as _tz
        from apps.subscriptions.models import Product
        from apps.subscriptions.services import get_geo_price_for_trial
        country = get_pricing_country(request)

        def _billing_snapshot(subscription):
            """Immutable-snapshot billing facts for one subscription."""
            if (subscription.price_cents is not None
                    and subscription.price_currency):
                price_display = format_price(
                    subscription.price_cents, subscription.price_currency)
            elif subscription.plan_price is not None:
                price_display = format_price(
                    subscription.plan_price.price_cents,
                    subscription.plan_price.currency)
            else:
                price_display = None
            ref_price = subscription.plan_price or subscription.geo_plan_price
            interval_display = (
                ref_price.get_interval_display() if ref_price is not None else '')
            days_remaining = None
            period_percent = None
            if subscription.expires_at is not None:
                days_remaining = (
                    subscription.expires_at.date() - _tz.now().date()).days
                if subscription.started_at is not None:
                    total = (subscription.expires_at - subscription.started_at).total_seconds()
                    elapsed = (_tz.now() - subscription.started_at).total_seconds()
                    if total > 0:
                        period_percent = max(0, min(100, int(elapsed / total * 100)))
            is_expired = days_remaining is not None and days_remaining < 0
            is_complimentary = bool(subscription.is_admin_grant
                                    or subscription.is_gift)
            return {
                'price_display': price_display,
                'interval_display': interval_display,
                'period_end': subscription.expires_at,
                'days_remaining': days_remaining,
                'period_percent': period_percent,
                'is_expired': is_expired,
                'is_expiring': (not is_expired and days_remaining is not None
                                and days_remaining <= 7),
                'is_recurring': (not is_complimentary and ref_price is not None),
                'is_trial': bool(subscription.is_trial),
                'is_complimentary': is_complimentary,
                'status': subscription.status,
            }

        def _interval_display(plan, interval):
            """Display price for one interval; never raises (display-only)."""
            try:
                price_obj = resolve_plan_price(plan, interval, request)
                if price_obj is not None:
                    return format_price(price_obj.price_cents, price_obj.currency)
            except Exception:
                pass
            base = plan.prices.filter(interval=interval, is_active=True).first()
            if base is not None:
                return format_price(base.price_cents, base.currency)
            return None

        def _plan_card(plan):
            interval_displays = {}
            is_geo = False
            currency = None
            price_cents = None
            try:
                price_obj = resolve_plan_price(plan, 'monthly', request)
                if price_obj is not None:
                    interval_displays['monthly'] = format_price(
                        price_obj.price_cents, price_obj.currency)
                    is_geo = isinstance(price_obj, GeoPlanPrice)
                    currency = price_obj.currency
                    price_cents = price_obj.price_cents
            except Exception:
                base_price = plan.prices.filter(interval='monthly', is_active=True).first()
                if base_price:
                    interval_displays['monthly'] = format_price(base_price.price_cents, base_price.currency)
                    is_geo = False
                    currency = base_price.currency
                    price_cents = base_price.price_cents
            for iv in ('quarterly', 'yearly'):
                iv_display = _interval_display(plan, iv)
                if iv_display:
                    interval_displays[iv] = iv_display
            primary = None
            for iv, iv_label in (('monthly', 'Month'), ('quarterly', 'Quarter'), ('yearly', 'Year')):
                if iv in interval_displays:
                    primary = (iv, iv_label, interval_displays[iv])
                    break
            features_qs = plan.features.order_by('position', 'id')
            return {
                'id': plan.id,
                'name': plan.name,
                'tier': plan.tier,
                'product': plan.product,
                'description': plan.description,
                'price_display': primary[2] if primary else None,
                'display_interval': primary[0] if primary else None,
                'display_interval_label': primary[1] if primary else None,
                'interval_prices': [
                    {'interval': iv, 'interval_display': lbl,
                     'price_display': interval_displays[iv]}
                    for iv, lbl in (('monthly', 'Monthly'), ('quarterly', 'Quarterly'), ('yearly', 'Yearly'))
                    if iv in interval_displays and primary is not None and iv != primary[0]
                ],
                'is_geo': is_geo,
                'currency': currency,
                'price_cents': price_cents,
                'is_current': (current_plan is not None and plan.id == current_plan.id),
                'features': [{'text': f.text, 'disabled': False} for f in features_qs[:6]],
                'feature_count': features_qs.count(),
                'description_html': plan.description_html,
            }

        # All active subscriptions, one card per product held.
        active_subs = list(Subscription.objects.filter(
            user=user, is_active=True, status=Subscription.Status.ACTIVE,
        ).select_related('plan', 'plan__product', 'plan_price', 'geo_plan_price'))
        active_subs.sort(key=lambda s: (
            0 if s.plan.product_id is None else 1,
            s.plan.product.display_order if s.plan.product_id else 0,
            s.plan.display_order))

        subscribed_product_ids = {s.plan.product_id for s in active_subs}

        subscription_cards = []
        for sub in active_subs:
            candidates = (Plan.objects
                          .filter(is_active=True, is_trial=False, is_hidden=False,
                                  display_order__gt=sub.plan.display_order)
                          .order_by('display_order'))
            if sub.plan.product_id is not None:
                candidates = candidates.filter(product_id=sub.plan.product_id)
            else:
                candidates = candidates.filter(product__isnull=True)
            subscription_cards.append({
                'subscription': sub,
                'product': sub.plan.product,
                'plan': sub.plan,
                'billing': _billing_snapshot(sub),
                'features': [{'text': f.text, 'disabled': False}
                             for f in sub.plan.features.order_by('position', 'id')][:8],
                'upgrade_candidates': [_plan_card(p) for p in candidates],
            })

        # Explore: products the user does NOT hold, with visible plans.
        explore_products = []
        for product in Product.objects.filter(is_active=True).order_by('display_order', 'name'):
            if product.id in subscribed_product_ids:
                continue
            plans = (Plan.objects
                     .filter(product=product, is_active=True, is_trial=False,
                             is_hidden=False)
                     .order_by('display_order'))
            cards = [_plan_card(p) for p in plans]
            if cards:
                explore_products.append({'product': product, 'plans': cards})

        # Ungrouped (product=None) plans: same upgrade-only rule as before —
        # a user holding an ungrouped sub sees only higher ungrouped plans;
        # a user with no ungrouped sub sees all of them.
        ungrouped_cards = []
        if None not in subscribed_product_ids:
            qs = (Plan.objects
                  .filter(product__isnull=True, is_active=True, is_trial=False,
                          is_hidden=False)
                  .order_by('display_order'))
        else:
            primary = next(s for s in active_subs if s.plan.product_id is None)
            qs = (Plan.objects
                  .filter(product__isnull=True, is_active=True, is_trial=False,
                          is_hidden=False,
                          display_order__gt=primary.plan.display_order)
                  .order_by('display_order'))
        ungrouped_cards = [_plan_card(p) for p in qs]

        # Per-product trial offers — only where the user has no active sub.
        trial_offers = []
        for tp in Plan.objects.filter(is_active=True, is_trial=True,
                                      is_hidden=False).order_by('display_order'):
            if tp.product_id in subscribed_product_ids:
                continue
            tprice = get_geo_price_for_trial(tp, country)
            if tprice is None:
                tprice = (GeoPlanPrice.objects
                          .filter(plan=tp, is_active=True).first())
            if tprice is not None:
                trial_offers.append({
                    'plan': tp,
                    'product': tp.product,
                    'price_display': format_price(tprice.price_cents, tprice.currency),
                    'duration_days': tp.trial_duration_days,
                })

        # ---- legacy single-subscription keys (backward compatibility) ----
        subscription = active_subs[0] if active_subs else None
        current_plan = subscription.plan if subscription is not None else None
        current_billing = (_billing_snapshot(subscription)
                           if subscription is not None else None)
        available_plans = qs  # ungrouped set above (upgrade-only rule)
        plans_with_pricing = ungrouped_cards
        can_upgrade = (subscription is not None and len(ungrouped_cards) > 0)
        trial_offer = trial_offers[0] if trial_offers else None

        ended_subscription = None
        if subscription is None:
            ended = (Subscription.objects
                     .filter(user=user)
                     .exclude(status=Subscription.Status.ACTIVE)
                     .select_related('plan')
                     .order_by('-expires_at')
                     .first())
            if ended is not None:
                ended_subscription = {
                    'plan_name': ended.plan.name,
                    'ended_at': ended.expires_at or ended.canceled_at,
                }

        # ===== END OF REPLACED SECTION =====

        # Trial offer (region-locked, mirrors the landing page's display
        # chain): country-specific GeoPlanPrice first, then a global
        # (country__isnull) GeoPlanPrice. purchase_plan() enforces the
        # country-specific row at buy time; on localhost (no CF header /
        # MaxMind) load the dashboard as /dashboard/?test_country=IN (DEBUG)
        # so both display and purchase resolve the same country.
        trial_offer = None
        from apps.subscriptions.services import get_geo_price_for_trial
        trial_plan = Plan.objects.filter(
            is_active=True, is_trial=True, is_hidden=False
        ).first()
        if trial_plan is not None:
            trial_price = get_geo_price_for_trial(trial_plan, country)
            if trial_price is None:
                trial_price = GeoPlanPrice.objects.filter(
                    plan=trial_plan, country__isnull=True, is_active=True
                ).first()
            if trial_price is not None:
                trial_offer = {
                    'plan': trial_plan,
                    'price_display': format_price(
                        trial_price.price_cents, trial_price.currency),
                    'duration_days': trial_plan.trial_duration_days,
                }

        # Telegram: channel display data (free = observed, paid = entitlement).
        from apps.bot_integration.services.channel_sync import (
            build_channel_display, get_telegram_access_state)
        telegram_channels = build_channel_display(user)
        telegram_access_state = get_telegram_access_state(user)
        telegram_channel_count = sum(
            1 for c in telegram_channels["free"] if c["is_member"])

        # Billing: recent payment history for the dashboard card. Read-only;
        # PaymentIntent is the source of truth (no shadow table).
        from apps.payments.models import PaymentIntent as _PaymentIntent
        recent_payments = []
        for _p in (_PaymentIntent.objects.filter(user=user)
                   .select_related("plan")
                   .prefetch_related("refunds")
                   .order_by("-created_at")[:10]):
            if _p.is_fully_refunded:
                _refund_label = "Fully refunded"
            elif _p.is_partially_refunded:
                _refund_label = "Partially refunded"
            else:
                _refund_label = ""
            if _p.chargeback_confirmed:
                _chargeback_label = "Chargeback confirmed"
            elif _p.chargeback:
                _chargeback_label = "Disputed"
            else:
                _chargeback_label = ""
            recent_payments.append({
                "pk": _p.pk,
                "date": _p.created_at,
                "plan_name": _p.plan.name,
                "amount_display": f"{_p.currency} {_p.amount_dollars:.2f}",
                "status": _p.get_status_display(),
                "status_key": _p.status,
                "coupon": _p.applied_coupon_code or "",
                "coupon_discount_display": (
                    f"{_p.currency} {_p.coupon_discount_cents / 100:.2f}"
                    if _p.coupon_discount_cents else ""),
                "refund_label": _refund_label,
                "chargeback_label": _chargeback_label,
            })

        context = {
            "user": user,
            "telegram_connected": bool(
                getattr(user, "telegram_account", None) and user.telegram_account.is_active),
            "show_connect_banner": telegram_access_state["state"] in ("not_linked", "needs_action"),
            "telegram_access_state": telegram_access_state,
            "telegram_account": getattr(user, "telegram_account", None),
            "recent_activity": recent_activity,
            "recent_notifications": recent_notifications,
            "unread_count": unread_count,
            "subscription": subscription,
            "current_plan": current_plan,
            'current_plan_features': current_plan_features,
            'current_plan_description_html': (current_plan.description_html if current_plan is not None else ''),
            # Old variable kept for backward compatibility (list of Plan objects)
            "available_plans": available_plans,
            # New geo‑aware variables
            "available_plans_geo": plans_with_pricing,
            "current_billing": current_billing,
            "can_upgrade": can_upgrade,
            "ended_subscription": ended_subscription,
            "trial_offer": trial_offer,
            # product-aware presentation (v3)
            "subscription_cards": subscription_cards,
            "explore_products": explore_products,
            "ungrouped_cards": ungrouped_cards,
            "trial_offers": trial_offers,
            "user_country": country,
            "recent_payments": recent_payments,
            "telegram_channel_count": telegram_channel_count,
            "telegram_channels": telegram_channels,
        }

        # ===== REFERRAL SYSTEM INTEGRATION =====
        from apps.growth.services.rewards import ReferralRewardService, UserRewardBalance
        from apps.growth.services.referrals import ReferralService
        from apps.growth.models import ReferralCode, ReferralReward, ReferralSettings
        from django.utils import timezone

        # Basic referral data
        referral_code = ReferralCode.get_or_create_for_user(user)
        context['referral_link'] = self.request.build_absolute_uri(
            f"/growth/r/{referral_code.code}/"
        )

        # OPTIMIZED SHARE MESSAGE (High Conversion)
        site_name = getattr(settings, 'SITE_NAME', 'TradeAdmin')
        context['optimized_share_message'] = f"""Get free premium access on {site_name}!

Use my link:
{context['referral_link']}

You'll get discounts and exclusive access, and I'll get extra subscription days 🚀"""

        # Also set old variable name for backward compatibility
        context['referral_share_text'] = context['optimized_share_message']

        # Balance & stats
        balance = UserRewardBalance(user)
        stats = ReferralService.get_referral_stats(user)
        stats['total_rewards_available_cents'] = balance.total_cents
        stats['total_rewards_available_display'] = balance.total_display
        stats['pending_rewards_display'] = f"${stats.get('pending_rewards_cents', 0) / 100:.2f}"
        context['referral_stats'] = stats

        # Referral settings (DYNAMIC - not hardcoded!)
        referral_settings = ReferralSettings.get_settings()
        context['referral_settings'] = referral_settings

        if referral_settings:
            # Calculate example reward: e.g., 20% of $50 = $10
            example_price = 5000  # $50 in cents
            reward_cents = int(example_price * float(referral_settings.default_reward_percentage) / 100)
            context['estimated_reward_example'] = f"{reward_cents / 100:.0f}"
            context['example_plan_price'] = "50"

            # Backward compatibility
            context['next_reward_estimate'] = {
                "amount": f"{reward_cents / 100:.0f}",
                "days": "6"
            }

        # Extension estimate
        plan_price_cents, plan_duration_days = 1000, 30
        if subscription and subscription.plan_price:
            plan_price_cents = subscription.plan_price.price_cents

        context['extension_estimate'] = ReferralRewardService.estimate_extension_for_balance(
            user, plan_price_cents=plan_price_cents, plan_duration_days=plan_duration_days
        )

        # LOSS AVERSION: Next billing date
        if subscription and subscription.expires_at:
            context['next_billing_date'] = subscription.expires_at
            days_until = (subscription.expires_at.date() - timezone.now().date()).days
            context['billing_urgency'] = 'urgent' if days_until <= 3 else 'normal'

        # PENDING URGENCY: Soonest unlock
        pending = ReferralReward.objects.filter(
            referral__referrer=user,
            status='pending',
            unlocked_at__gt=timezone.now()
        ).order_by('unlocked_at').first()

        if pending:
            context['pending_unlock_soonest'] = {
                "unlocks_at": pending.unlocked_at,
                "days_left": max(0, (pending.unlocked_at.date() - timezone.now().date()).days)
            }

        # CELEBRATION: Newly unlocked reward
        newly_unlocked = ReferralReward.objects.filter(
            referral__referrer=user,
            status='credited',
            created_at__gte=timezone.now() - timezone.timedelta(hours=24)
        ).first()

        if newly_unlocked:
            extra_days = max(1, int((newly_unlocked.amount_cents / 100) / (plan_price_cents / 100 / plan_duration_days)))
            context['newly_unlocked_reward'] = {
                "amount_display": newly_unlocked.amount_display,
                "extra_days": extra_days
            }
        # ===== END REFERRAL INTEGRATION =====

        return render(request, "accounts/dashboard.html", context)


# ===== REFERRAL DASHBOARD VIEW (with forced fallbacks) =====
@method_decorator(login_required, name="dispatch")
@method_decorator(check_banned, name="dispatch")
class ReferralDashboardView(ReferralDashboardContextMixin, TemplateView):
    """
    Dedicated referral dashboard with psychology-driven UX.
    Uses ReferralDashboardContextMixin to inject all referral context.
    """
    template_name = "growth/referral_dashboard.html"

    def get_context_data(self, **kwargs):
        # Try to get context from the mixin
        try:
            context = super().get_context_data(**kwargs)
        except Exception as e:
            context = super(ReferralDashboardContextMixin, self).get_context_data(**kwargs)
            import logging
            logging.error(f"ReferralDashboardView mixin failed: {e}")

        # FORCE-SET critical variables (overwrite if missing, None, or empty)
        if not context.get("reward_percentage"):
            try:
                context["reward_percentage"] = ReferralSettings.get_settings().default_reward_percentage
            except Exception:
                context["reward_percentage"] = Decimal("20")
        if not context.get("social_framing"):
            context["social_framing"] = "You earn rewards. Your friend gets a better deal."
        if not context.get("reward_scaling_message"):
            context["reward_scaling_message"] = "Rewards scale with the plan your friend chooses"
        if not context.get("progress_message"):
            context["progress_message"] = "Refer friends to earn credits"
        if not context.get("reward_timing_note"):
            context["reward_timing_note"] = "Rewards unlock after subscription is confirmed"
        if not context.get("referral_link"):
            context["referral_link"] = "#"
        if not context.get("site_name"):
            context["site_name"] = getattr(settings, 'SITE_NAME', 'TradeAdmin')
        if not context.get("currency_symbol"):
            context["currency_symbol"] = "$"
        if not context.get("optimized_share_message"):
            context["optimized_share_message"] = f"Join me on {context['site_name']}!"

        # Force-set referral_stats structure
        if not context.get("referral_stats"):
            context["referral_stats"] = {}
        stats = context["referral_stats"]
        stats.setdefault("completed", 0)
        stats.setdefault("pending", 0)
        stats.setdefault("total_referrals", 0)
        stats.setdefault("total_rewards_available_cents", 0)
        stats.setdefault("total_rewards_available_display", "$0.00")
        stats.setdefault("pending_rewards_cents", 0)
        stats.setdefault("pending_rewards_display", "$0.00")

        # Other optional structures
        context.setdefault("extension_estimate", {"extra_days": 0})
        context.setdefault("recent_referrals", [])
        context.setdefault("reward_buckets", 0)
        context.setdefault("next_reward_estimate", None)
        context.setdefault("pending_unlock_soonest", None)
        context.setdefault("newly_unlocked_reward", None)
        context.setdefault("next_billing_date", None)
        context.setdefault("days_until_billing", None)
        context.setdefault("billing_urgency", "normal")
        context.setdefault("referral_error", False)

        return context


@method_decorator(login_required, name="dispatch")
@method_decorator(check_banned, name="dispatch")
class ProfileView(View):
    """User profile view and edit."""

    def get(self, request):
        return render(request, "accounts/profile.html", {
            "user": request.user,
            "telegram_connected": bool(request.user.telegram_id and request.user.telegram_verified),
        })

    def post(self, request):
        user = request.user

        # Update editable fields
        user.first_name = request.POST.get("first_name", user.first_name)
        user.last_name = request.POST.get("last_name", user.last_name)
        user.email = request.POST.get("email", user.email)
        user.bio = request.POST.get("bio", user.bio)

        # Handle avatar upload
        if "avatar" in request.FILES:
            user.avatar = request.FILES["avatar"]

        user.save()

        # Update preferences
        pref, _ = UserPreference.objects.get_or_create(user=user)
        pref.timezone = request.POST.get("timezone", pref.timezone)
        pref.language = request.POST.get("language", pref.language)
        pref.save()

        # Log the update
        AuditLog.log(
            action="profile_updated",
            user=user,
            object_type="user",
            object_id=user.id,
            metadata={"fields_updated": ["first_name", "last_name", "email", "bio", "avatar", "timezone", "language"]}
        )

        return redirect("profile")


@method_decorator(login_required, name="dispatch")
@method_decorator(check_banned, name="dispatch")
class ActivityLogView(View):
    """User activity log view."""

    def get(self, request):
        activities = AuditLog.objects.filter(
            user=request.user
        ).order_by("-created_at")

        return render(request, "accounts/activity.html", {
            "activities": activities
        })


@method_decorator(login_required, name="dispatch")
@method_decorator(check_banned, name="dispatch")
class NotificationsView(View):
    """User notifications view."""

    def get(self, request):
        from apps.notifications.models import Notification
        notifications = Notification.objects.filter(
            user=request.user
        ).order_by("-created_at")

        return render(request, "accounts/notifications.html", {
            "notifications": notifications
        })


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def telegram_connect(request):
    """
    Connect Telegram account to user profile.
    Verifies Telegram widget hash and stores telegram_id.
    """
    user = request.user

    data = request.data

    # Required fields from Telegram widget
    check_hash = data.get("hash")
    telegram_id = data.get("id")
    username = data.get("username", "")

    if not check_hash or not telegram_id:
        return Response(
            {"error": "Missing required fields"},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Verify Telegram hash
    bot_token = settings.TELEGRAM_BOT_TOKEN
    if not bot_token:
        return Response(
            {"error": "Telegram bot not configured"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    # Create data_check_string
    data_fields = []
    for key in ["auth_date", "first_name", "id", "last_name", "photo_url", "username"]:
        if key in data and data[key]:
            data_fields.append(f"{key}={data[key]}")
    data_fields.sort()
    data_check_string = chr(10).join(data_fields)

    # Calculate secret key
    secret_key = hashlib.sha256(bot_token.encode()).digest()

    # Calculate hash
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256
    ).hexdigest()

    if calculated_hash != check_hash:
        return Response(
            {"error": "Invalid Telegram hash"},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Check if telegram_id is already connected to another user
    existing_user = User.objects.filter(
        telegram_id=telegram_id
    ).exclude(id=user.id).first()

    if existing_user:
        return Response(
            {"error": "This Telegram account is already connected to another user"},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Store Telegram info
    user.telegram_id = telegram_id
    user.telegram_username = username
    user.telegram_verified = True
    user.save()

    # Log the connection
    AuditLog.log(
        action="telegram_connected",
        user=user,
        object_type="user",
        object_id=user.id,
        metadata={"telegram_id": telegram_id, "telegram_username": username}
    )

    return Response({
        "success": True,
        "telegram_id": telegram_id,
        "telegram_username": username,
        "telegram_verified": True
    })


class UserMeAPIView(APIView):
    """Get current user info."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)


class UserProfileAPIView(APIView):
    """Get or update user profile."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = UserProfileSerializer(request.user)
        return Response(serializer.data)

    def patch(self, request):
        user = request.user

        # Update user fields
        allowed_fields = ["first_name", "last_name", "email", "bio"]
        for field in allowed_fields:
            if field in request.data:
                setattr(user, field, request.data[field])

        # Handle avatar
        if "avatar" in request.FILES:
            user.avatar = request.FILES["avatar"]

        user.save()

        # Update preferences
        pref_fields = ["timezone", "language", "notifications_enabled"]
        pref, _ = UserPreference.objects.get_or_create(user=user)
        for field in pref_fields:
            if field in request.data:
                setattr(pref, field, request.data[field])
        pref.save()

        # Log update
        AuditLog.log(
            action="profile_updated",
            user=user,
            object_type="user",
            object_id=user.id,
            metadata={"source": "api"}
        )

        serializer = UserProfileSerializer(user)
        return Response(serializer.data)


class UserActivityAPIView(APIView):
    """Get user activity log."""

    def get(self, request):
        activities = AuditLog.objects.filter(
            user=request.user
        ).order_by("-created_at", "-pk")[:50]

        data = [{
            "id": str(a.id),
            "action": a.action,
            "object_type": a.object_type,
            "object_id": a.object_id,
            "metadata": a.metadata,
            "created_at": a.created_at.isoformat(),
        } for a in activities]

        return Response(data)


# ===== HELPER FUNCTION FOR PLAN FEATURES =====
