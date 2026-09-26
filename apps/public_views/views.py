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
    get_region_for_country,
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
                    is_trial=False,
                    is_hidden=False
                ).order_by('-display_order')

                if not tier_plans.exists():
                    continue

                # EVERY plan in the tier gets its own card (ordered by
                # display_order). The tier's trial offer attaches to the
                # first card of the tier that actually renders.
                trial = Plan.objects.filter(
                    tier=tier,
                    is_active=True,
                    is_trial=True,
                    is_hidden=False
                ).first()

                trial_info = None
                trial_price_display = None
                if trial:
                    trial_price_data = self._get_trial_price(trial, country)
                    if trial_price_data:
                        trial_info = trial
                        trial_price_display = trial_price_data['display']

                trial_attached = False
                for selected_plan in tier_plans:
                    pricing = self._get_all_interval_pricing(selected_plan, country)
                    # A paid plan needs at least one resolvable interval price
                    # to render its card. The FREE tier has no prices by
                    # design — its card renders with pricing=None (template
                    # guards handle the missing tabs/price blocks; the CTA is
                    # "Get Started Free").
                    if not pricing and tier != 'free':
                        continue

                    # Feature bullets come from PlanFeature rows only
                    # (admin-controlled); no hardcoded fallbacks.
                    features = self._get_plan_features(selected_plan)

                    plans_data.append({
                        'plan': selected_plan,
                        'tier': tier,
                        'pricing': pricing,
                        'currency_symbol': (pricing.get('currency_symbol', '₹')
                                            if pricing else '₹'),
                        'is_geo': (pricing.get('is_geo', False) if pricing else False),
                        'trial': trial_info if not trial_attached else None,
                        'trial_price_display': (trial_price_display
                                                if not trial_attached else None),
                        'features': features,
                    })
                    trial_attached = True

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

    def _price_dict(self, price_cents: int, currency: str,
                    geo_pricing: bool, interval: str) -> Dict[str, Any]:
        """Shared shape for a resolved interval price."""
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
            'currency': currency,
            'display': format_price(price_cents, currency),
            'display_monthly': format_price(monthly_equiv, currency),
            'geo_pricing': geo_pricing,
        }

    def _get_price_for_interval(self, plan: Plan, country: Optional[str], interval: str) -> Optional[Dict[str, Any]]:
        """Get price for specific interval.

        Resolution chain mirrors subscriptions.services.resolve_plan_price
        (the purchase path) exactly — the landing page must never resolve a
        different price than the one the user will actually be charged:
          1. country-specific GeoPlanPrice
          2. region-level GeoPlanPrice (country__isnull=True)  [was missing]
          3. global base PlanPrice
        Previously step 2 was absent, so region-priced plans resolved no
        price at all and their tier card was silently dropped from the page.
        """
        try:
            if country:
                geo_price = GeoPlanPrice.objects.filter(
                    plan=plan,
                    interval=interval,
                    country=country,
                    is_active=True
                ).first()
                if geo_price:
                    return self._price_dict(geo_price.price_cents,
                                            geo_price.currency, True, interval)

                region = get_region_for_country(country)
                if region:
                    geo_price = GeoPlanPrice.objects.filter(
                        plan=plan,
                        interval=interval,
                        region=region,
                        country__isnull=True,
                        is_active=True
                    ).first()
                    if geo_price:
                        return self._price_dict(geo_price.price_cents,
                                                geo_price.currency, True, interval)

            plan_price = PlanPrice.objects.filter(
                plan=plan,
                interval=interval,
                is_active=True
            ).first()

            if plan_price:
                return self._price_dict(plan_price.price_cents,
                                        plan_price.currency, False, interval)

            return None

        except Exception as e:
            logger.warning(f"Error getting {interval} price: {e}")
            return None

    def _get_trial_price(self, trial: Plan, country: Optional[str]) -> Optional[Dict[str, Any]]:
        """Get trial price.

        NOTE: purchase_plan() requires a COUNTRY-SPECIFIC geo price for
        trials (get_geo_price_for_trial matches country only). The
        country__isnull fallback below can therefore display a trial price
        that purchase rejects for region-priced trials — keep trials
        country-priced, or relax get_geo_price_for_trial to mirror the
        region chain above.
        """
        try:
            if country:
                geo_price = GeoPlanPrice.objects.filter(
                    plan=trial,
                    country=country,
                    is_active=True
                ).first()
                if geo_price:
                    return {
                        'price_cents': geo_price.price_cents,
                        'display': format_price(geo_price.price_cents, geo_price.currency),
                    }

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

    def _get_plan_features(self, plan: Plan) -> List[Dict[str, Any]]:
        """DB-driven feature bullets (PlanFeature) in admin-controlled order.

        Returns an empty list when the plan has no PlanFeature rows, so the
        caller falls back to the hardcoded tier defaults from
        the hardcoded tier lists. DB features are the only source; plans without
    PlanFeature rows render without bullets
        (they are not merged).
        """
        try:
            rows = plan.features.order_by("position", "id")
            return [{"text": f.text, "disabled": False} for f in rows]
        except Exception as e:
            logger.warning(f"Could not load features for plan {plan.pk}: {e}")
            return []




# Removed CustomLoginView and CustomSignupView – use allauth's views instead.
