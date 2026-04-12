"""
Public views for landing page and custom login/signup.
Uses geo-aware pricing from subscriptions services.
"""
import logging
from typing import Optional, Dict, Any, List

from django.conf import settings
from django.contrib.auth import views as auth_views
from allauth.account.views import SignupView
from django.views.generic import TemplateView

from apps.subscriptions.models import Plan, PlanPrice, GeoPlanPrice
from apps.subscriptions.services import (
    resolve_plan_price, 
    get_pricing_country, 
    get_geo_price_for_trial,
    format_price,
)

logger = logging.getLogger(__name__)


class LandingPageView(TemplateView):
    """
    Landing page with dynamic geo-pricing.
    Supports BOTH monthly and yearly pricing with savings calculation.
    """
    template_name = "landing/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['site_name'] = getattr(settings, 'SITE_NAME', 'TradeAdmin')

        # Get tiered plans with attached trials
        tiered_plans = self._get_tiered_plans(self.request)
        context['tiered_plans'] = tiered_plans

        # Check if any trial is available for hero/footer messaging
        any_trial = any(p.get('trial') for p in tiered_plans)
        context['trial_available'] = any_trial

        # Get trial info for display (use first found)
        trial_duration = None
        trial_price = None
        for p in tiered_plans:
            if p.get('trial'):
                trial_duration = p['trial'].trial_duration_days
                price_str = p.get('trial_price_display', '$7')
                trial_price = ''.join(c for c in price_str if c.isdigit())
                break

        context['trial_duration'] = trial_duration
        context['trial_price'] = trial_price

        return context

    def _get_tiered_plans(self, request) -> List[Dict[str, Any]]:
        """
        Get one plan per tier with monthly AND yearly pricing.
        """
        try:
            country = get_pricing_country(request)
            plans_data = []

            # Define tier order for display
            tier_order = ['free', 'basic', 'pro', 'enterprise']

            for tier in tier_order:
                # Get non-trial plans for this tier, ordered by display_order desc
                tier_plans = Plan.objects.filter(
                    tier=tier,
                    is_active=True,
                    is_trial=False
                ).order_by('-display_order')

                if not tier_plans.exists():
                    continue

                # Select top plan (highest display_order)
                selected_plan = tier_plans.first()

                # Get BOTH monthly and yearly pricing
                pricing = self._get_plan_pricing_with_intervals(selected_plan, country)
                if not pricing:
                    continue

                # Find trial for this tier
                trial = Plan.objects.filter(
                    tier=tier,
                    is_active=True,
                    is_trial=True
                ).first()

                # Get trial pricing
                trial_info = None
                trial_price_display = None
                if trial:
                    trial_price_data = self._get_trial_price(trial, country)
                    if trial_price_data:
                        trial_info = trial
                        trial_price_display = trial_price_data['display']

                # Build features
                features = self._get_features_for_tier(tier)

                plans_data.append({
                    'plan': selected_plan,
                    'tier': tier,
                    'monthly': pricing.get('monthly'),
                    'yearly': pricing.get('yearly'),
                    'yearly_savings': pricing.get('yearly_savings'),
                    'currency_symbol': pricing.get('currency_symbol', '$'),
                    'is_geo': pricing.get('is_geo', False),
                    'trial': trial_info,
                    'trial_price_display': trial_price_display,
                    'features': features,
                })

            return plans_data

        except Exception as e:
            logger.warning(f"Could not load tiered plans: {e}")
            return []

    def _get_plan_pricing_with_intervals(self, plan: Plan, country: Optional[str]) -> Optional[Dict[str, Any]]:
        """Get monthly AND yearly pricing for a plan."""
        try:
            monthly = self._get_price_for_interval(plan, country, 'monthly')
            yearly = self._get_price_for_interval(plan, country, 'yearly')

            if not monthly and not yearly:
                return None

            # Use monthly as default for currency/symbol
            default = monthly or yearly

            # Calculate yearly savings
            yearly_savings = None
            if monthly and yearly:
                monthly_total = monthly['price_cents'] * 12
                yearly_total = yearly['price_cents']
                savings_cents = monthly_total - yearly_total
                if savings_cents > 0:
                    yearly_savings = format_price(savings_cents, monthly['currency'])

            return {
                'monthly': monthly,
                'yearly': yearly,
                'yearly_savings': yearly_savings,
                'currency_symbol': self._get_currency_symbol(default['currency']),
                'is_geo': monthly.get('geo_pricing', False) if monthly else yearly.get('geo_pricing', False),
            }

        except Exception as e:
            logger.warning(f"Could not resolve pricing for {plan.name}: {e}")
            return None

    def _get_price_for_interval(self, plan: Plan, country: Optional[str], interval: str) -> Optional[Dict[str, Any]]:
        """Get price for specific interval, checking GeoPlanPrice first then PlanPrice."""
        try:
            # Try GeoPlanPrice first
            if country:
                geo_price = GeoPlanPrice.objects.filter(
                    plan=plan,
                    interval=interval,
                    country=country,
                    is_active=True
                ).first()

                if geo_price:
                    return {
                        'price_cents': geo_price.price_cents,
                        'price': int(geo_price.price_cents / 100),
                        'currency': geo_price.currency,
                        'display': format_price(geo_price.price_cents, geo_price.currency),
                        'geo_pricing': True,
                    }

            # Fallback to PlanPrice
            plan_price = PlanPrice.objects.filter(
                plan=plan,
                interval=interval,
                is_active=True
            ).first()

            if plan_price:
                return {
                    'price_cents': plan_price.price_cents,
                    'price': int(plan_price.price_cents / 100),
                    'currency': plan_price.currency,
                    'display': format_price(plan_price.price_cents, plan_price.currency),
                    'geo_pricing': False,
                }

            return None

        except Exception as e:
            logger.warning(f"Error getting {interval} price: {e}")
            return None

    def _get_trial_price(self, trial: Plan, country: Optional[str]) -> Optional[Dict[str, Any]]:
        """Get trial price (uses first available GeoPlanPrice)."""
        try:
            geo_price = GeoPlanPrice.objects.filter(
                plan=trial,
                country=country,
                is_active=True
            ).first()

            if not geo_price and not country:
                # Try global (no country)
                geo_price = GeoPlanPrice.objects.filter(
                    plan=trial,
                    country__isnull=True,
                    is_active=True
                ).first()

            if geo_price:
                return {
                    'price_cents': geo_price.price_cents,
                    'display': format_price(geo_price.price_cents, geo_price.currency),
                }

            return None
        except Exception:
            return None

    def _get_currency_symbol(self, currency: str) -> str:
        """Get currency symbol."""
        symbols = {
            'USD': '$', 'EUR': '€', 'GBP': '£', 'INR': '₹',
            'JPY': '¥', 'SGD': 'S$', 'AUD': 'A$', 'CAD': 'C$',
        }
        return symbols.get(currency, '$')

    def _get_features_for_tier(self, tier: str) -> List[Dict[str, Any]]:
        """Generate feature list based on plan tier."""
        features_map = {
            'free': [
                {'text': '3 real-time trades per week', 'disabled': False},
                {'text': 'Entry & target alerts', 'disabled': False},
                {'text': 'Email notifications', 'disabled': False},
                {'text': 'Basic risk guidance', 'disabled': True},
                {'text': 'SMS alerts', 'disabled': True},
                {'text': 'Trade history', 'disabled': True},
            ],
            'basic': [
                {'text': '5 real-time trades per week', 'disabled': False},
                {'text': 'Entry, stop & target alerts', 'disabled': False},
                {'text': 'Basic risk management', 'disabled': False},
                {'text': 'Email notifications', 'disabled': False},
                {'text': '24/7 chat support', 'disabled': False},
                {'text': 'Advanced analytics', 'disabled': True},
            ],
            'pro': [
                {'text': 'Unlimited real-time trades', 'disabled': False},
                {'text': 'Entry, updates & exit alerts', 'disabled': False},
                {'text': 'Advanced risk management', 'disabled': False},
                {'text': 'SMS + Email notifications', 'disabled': False},
                {'text': '24/7 chat support', 'disabled': False},
                {'text': 'Full trade history & review', 'disabled': False},
            ],
            'enterprise': [
                {'text': 'Everything in Pro, plus:', 'disabled': False},
                {'text': '1-on-1 monthly strategy call', 'disabled': False},
                {'text': 'Priority trade alerts (faster)', 'disabled': False},
                {'text': 'Custom risk parameters', 'disabled': False},
                {'text': 'API access', 'disabled': False},
                {'text': 'White-label exports', 'disabled': False},
            ],
        }
        return features_map.get(tier, features_map['basic'])


class CustomLoginView(auth_views.LoginView):
    """
    Custom login with dynamic trial availability check.
    """
    template_name = "account/login.html"
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['site_name'] = getattr(settings, 'SITE_NAME', 'TradeAdmin')
        context['trial_available'] = self._check_trial_available(self.request)
        return context

    def _check_trial_available(self, request) -> bool:
        try:
            country = get_pricing_country(request)
            trial_plans = Plan.objects.filter(is_trial=True, is_active=True)

            for trial in trial_plans:
                has_price = GeoPlanPrice.objects.filter(
                    plan=trial,
                    country=country,
                    is_active=True
                ).exists()
                if has_price:
                    return True
            return False
        except Exception:
            return False

    def get_success_url(self):
        from django.urls import reverse
        return reverse('dashboard')


class CustomSignupView(SignupView):
    """
    Custom signup with dynamic trial availability check.
    """
    template_name = "account/signup.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['site_name'] = getattr(settings, 'SITE_NAME', 'TradeAdmin')
        context['trial_available'] = self._check_trial_available(self.request)

        trial_info = self._get_trial_info(self.request)
        if trial_info:
            context['trial_duration'] = trial_info['duration']
            context['trial_price'] = trial_info['price']
            context['trial_price_display'] = trial_info['price_display']

        return context

    def _check_trial_available(self, request) -> bool:
        try:
            country = get_pricing_country(request)
            trial_plans = Plan.objects.filter(is_trial=True, is_active=True)

            for trial in trial_plans:
                has_price = GeoPlanPrice.objects.filter(
                    plan=trial,
                    country=country,
                    is_active=True
                ).exists()
                if has_price:
                    return True
            return False
        except Exception:
            return False

    def _get_trial_info(self, request) -> Optional[Dict[str, Any]]:
        try:
            country = get_pricing_country(request)
            trial = Plan.objects.filter(is_trial=True, is_active=True).first()

            if not trial:
                return None

            geo_price = GeoPlanPrice.objects.filter(
                plan=trial,
                country=country,
                is_active=True
            ).first()

            if not geo_price:
                return None

            return {
                'duration': trial.trial_duration_days,
                'price': geo_price.price_cents / 100,
                'price_display': format_price(geo_price.price_cents, geo_price.currency),
            }
        except Exception:
            return None
