"""
Public views for landing page with improved pricing display.
Supports Monthly, Quarterly, and Yearly with tab-style selection.
"""
import logging
from typing import Optional, Dict, Any, List

from django.conf import settings
from django.views.generic import TemplateView

from apps.subscriptions.models import Plan, PlanPrice, GeoPlanPrice
from apps.subscriptions.services import (
    get_pricing_country,
    format_price,
)

logger = logging.getLogger(__name__)


class LandingPageView(TemplateView):
    """
    Landing page with improved pricing display.
    Shows Monthly/Quarterly/Yearly as toggle options.
    """
    template_name = "landing/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['site_name'] = getattr(settings, 'SITE_NAME', 'TradeAdmin')

        tiered_plans = self._get_tiered_plans(self.request)
        context['tiered_plans'] = tiered_plans

        any_trial = any(p.get('trial') for p in tiered_plans)
        context['trial_available'] = any_trial

        trial_duration = None
        trial_price = None
        for p in tiered_plans:
            if p.get('trial'):
                trial_duration = p['trial'].trial_duration_days
                price_str = p.get('trial_price_display', '$7')
                trial_price = ''.join(c for c in price_str if c.isdigit() and c != ',')
                break

        context['trial_duration'] = trial_duration
        context['trial_price'] = trial_price

        return context

    def _get_tiered_plans(self, request) -> List[Dict[str, Any]]:
        """Get plans with all billing intervals."""
        try:
            country = get_pricing_country(request)
            plans_data = []
            tier_order = ['free', 'basic', 'pro', 'enterprise']

            for tier in tier_order:
                tier_plans = Plan.objects.filter(
                    tier=tier,
                    is_active=True,
                    is_trial=False
                ).order_by('-display_order')

                if not tier_plans.exists():
                    continue

                selected_plan = tier_plans.first()

                pricing = self._get_all_interval_pricing(selected_plan, country)
                if not pricing:
                    continue

                trial = Plan.objects.filter(
                    tier=tier,
                    is_active=True,
                    is_trial=True
                ).first()

                trial_info = None
                trial_price_display = None
                if trial:
                    trial_price_data = self._get_trial_price(trial, country)
                    if trial_price_data:
                        trial_info = trial
                        trial_price_display = trial_price_data['display']

                features = self._get_features_for_tier(tier)

                plans_data.append({
                    'plan': selected_plan,
                    'tier': tier,
                    'pricing': pricing,
                    'currency_symbol': pricing.get('currency_symbol', '₹'),
                    'is_geo': pricing.get('is_geo', False),
                    'trial': trial_info,
                    'trial_price_display': trial_price_display,
                    'features': features,
                })

            return plans_data

        except Exception as e:
            logger.warning(f"Could not load tiered plans: {e}")
            return []

    def _get_all_interval_pricing(self, plan: Plan, country: Optional[str]) -> Optional[Dict[str, Any]]:
        """Get monthly, quarterly, and yearly pricing with proper savings calculation."""
        try:
            monthly = self._get_price_for_interval(plan, country, 'monthly')
            quarterly = self._get_price_for_interval(plan, country, 'quarterly')
            yearly = self._get_price_for_interval(plan, country, 'yearly')

            if not any([monthly, quarterly, yearly]):
                return None

            default = monthly or quarterly or yearly

            # Calculate savings properly
            savings = {}
            if monthly and quarterly:
                monthly_cost_3mo = monthly['price_cents'] * 3
                quarterly_cost = quarterly['price_cents']
                quarterly_save = monthly_cost_3mo - quarterly_cost
                if quarterly_save > 0:
                    savings['quarterly'] = {
                        'amount': format_price(quarterly_save, monthly['currency']),
                        'percent': int((quarterly_save / monthly_cost_3mo) * 100)
                    }

            if monthly and yearly:
                monthly_cost_12mo = monthly['price_cents'] * 12
                yearly_cost = yearly['price_cents']
                yearly_save = monthly_cost_12mo - yearly_cost
                if yearly_save > 0:
                    savings['yearly'] = {
                        'amount': format_price(yearly_save, monthly['currency']),
                        'percent': int((yearly_save / monthly_cost_12mo) * 100)
                    }

            # Determine best value (yearly if saves 20%+, else quarterly if saves 10%+)
            best_value = None
            if yearly and savings.get('yearly', {}).get('percent', 0) >= 20:
                best_value = 'yearly'
            elif quarterly and savings.get('quarterly', {}).get('percent', 0) >= 10:
                best_value = 'quarterly'

            return {
                'monthly': monthly,
                'quarterly': quarterly,
                'yearly': yearly,
                'savings': savings,
                'best_value': best_value,
                'currency_symbol': self._get_currency_symbol(default['currency']),
                'is_geo': monthly.get('geo_pricing', False) if monthly else False,
            }

        except Exception as e:
            logger.warning(f"Could not resolve pricing for {plan.name}: {e}")
            return None

    def _get_price_for_interval(self, plan: Plan, country: Optional[str], interval: str) -> Optional[Dict[str, Any]]:
        """Get price for specific interval."""
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
                    price_cents = geo_price.price_cents
                    # Calculate monthly equivalent
                    if interval == 'yearly':
                        monthly_equiv = int(price_cents / 12)
                    elif interval == 'quarterly':
                        monthly_equiv = int(price_cents / 3)
                    else:
                        monthly_equiv = price_cents

                    return {
                        'price_cents': price_cents,
                        'price_monthly': int(monthly_equiv / 100),
                        'price_total': int(price_cents / 100),
                        'currency': geo_price.currency,
                        'display': format_price(price_cents, geo_price.currency),
                        'display_monthly': format_price(monthly_equiv, geo_price.currency),
                        'geo_pricing': True,
                    }

            # Fallback to PlanPrice
            plan_price = PlanPrice.objects.filter(
                plan=plan,
                interval=interval,
                is_active=True
            ).first()

            if plan_price:
                price_cents = plan_price.price_cents
                if interval == 'yearly':
                    monthly_equiv = int(price_cents / 12)
                elif interval == 'quarterly':
                    monthly_equiv = int(price_cents / 3)
                else:
                    monthly_equiv = price_cents

                return {
                    'price_cents': price_cents,
                    'price_monthly': int(monthly_equiv / 100),
                    'price_total': int(price_cents / 100),
                    'currency': plan_price.currency,
                    'display': format_price(price_cents, plan_price.currency),
                    'display_monthly': format_price(monthly_equiv, plan_price.currency),
                    'geo_pricing': False,
                }

            return None

        except Exception as e:
            logger.warning(f"Error getting {interval} price: {e}")
            return None

    def _get_trial_price(self, trial: Plan, country: Optional[str]) -> Optional[Dict[str, Any]]:
        """Get trial price."""
        try:
            geo_price = GeoPlanPrice.objects.filter(
                plan=trial,
                country=country,
                is_active=True
            ).first()

            if not geo_price and not country:
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
        symbols = {'USD': '$', 'EUR': '€', 'GBP': '£', 'INR': '₹', 'JPY': '¥'}
        return symbols.get(currency, '₹')

    def _get_features_for_tier(self, tier: str) -> List[Dict[str, Any]]:
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


# Removed CustomLoginView and CustomSignupView – use allauth's views instead.