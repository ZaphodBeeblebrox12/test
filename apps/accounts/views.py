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
                "ban_reason": request.user.ban_reason
            })
        return view_func(request, *args, **kwargs)
    return wrapper


@method_decorator(login_required, name="dispatch")
@method_decorator(check_banned, name="dispatch")
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
        try:
            subscription = Subscription.objects.select_related(
                'plan', 'plan_price'
            ).get(user=user, is_active=True, status=Subscription.Status.ACTIVE)
            current_plan = subscription.plan
        except Subscription.DoesNotExist:
            subscription = None
            current_plan = None

        # ===== REPLACED: Get available plans with geo‑pricing =====
        country = get_pricing_country(request)
        available_plans = Plan.objects.filter(is_active=True, is_trial=False).order_by('display_order')
        plans_with_pricing = []

        for plan in available_plans:
            try:
                price_obj = resolve_plan_price(plan, 'monthly', request)
                price_display = format_price(price_obj.price_cents, price_obj.currency)
                is_geo = isinstance(price_obj, GeoPlanPrice)
                currency = price_obj.currency
                price_cents = price_obj.price_cents
            except Exception:
                # Fallback to base price
                base_price = plan.prices.filter(interval='monthly', is_active=True).first()
                if not base_price:
                    continue
                price_display = format_price(base_price.price_cents, base_price.currency)
                is_geo = False
                currency = base_price.currency
                price_cents = base_price.price_cents

            plans_with_pricing.append({
                'id': plan.id,
                'name': plan.name,
                'tier': plan.tier,
                'description': plan.description,
                'price_display': price_display,
                'is_geo': is_geo,
                'currency': currency,
                'price_cents': price_cents,
                # Optionally include features for display
                'features': _get_plan_features(plan.tier),
            })

        # ===== END OF REPLACED SECTION =====

        context = {
            "user": user,
            "telegram_connected": bool(user.telegram_id and user.telegram_verified),
            "recent_activity": recent_activity,
            "recent_notifications": recent_notifications,
            "unread_count": unread_count,
            "subscription": subscription,
            "current_plan": current_plan,
            # Old variable kept for backward compatibility (list of Plan objects)
            "available_plans": available_plans,
            # New geo‑aware variables
            "available_plans_geo": plans_with_pricing,
            "user_country": country,
        }
        
        # ===== REFERRAL SYSTEM INTEGRATION =====
        from apps.growth.services import ReferralService, ReferralRewardService, UserRewardBalance
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
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        activities = AuditLog.objects.filter(
            user=request.user
        ).order_by("-created_at")[:50]

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
def _get_plan_features(tier):
    """Return feature list for a given tier."""
    features_map = {
        'free': ['3 real-time trades/week', 'Entry alerts', 'Email support'],
        'basic': ['5 trades/week', 'Stop & target alerts', 'Basic risk', 'Email', 'Chat'],
        'pro': ['Unlimited trades', 'Advanced risk', 'SMS alerts', '24/7 support'],
        'enterprise': ['All Pro features', '1-on-1 calls', 'API access', 'Custom'],
    }
    return features_map.get(tier, features_map['basic'])