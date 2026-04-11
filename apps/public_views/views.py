"""
Public views for landing page and custom login.
Uses geo‑aware pricing from subscriptions services.
"""
import logging

from django.conf import settings
from django.contrib.auth import views as auth_views
from django.views.generic import TemplateView

logger = logging.getLogger(__name__)


class LandingPageView(TemplateView):
    """
    Landing page with dynamic geo‑pricing from your existing Plan model.
    Template: templates/landing/index.html
    """
    template_name = "landing/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Site branding
        context['site_name'] = getattr(settings, 'SITE_NAME', 'TradeAdmin')

        # Trial info
        context['trial_plan'] = {
            'trial_price': 7,
            'duration_days': 7
        }

        # Get geo‑aware pricing using the request
        context['pricing_plans'] = self._get_pricing_plans(self.request)
        context['default_plan_price'] = self._get_default_price(self.request)

        return context

    def _get_pricing_plans(self, request):
        """Fetch plans with geo‑resolved pricing using subscriptions.services."""
        try:
            from apps.subscriptions.models import Plan
            from apps.subscriptions.services import resolve_plan_price, get_pricing_country

            plans = []
            active_plans = Plan.objects.filter(
                is_active=True
            ).order_by('display_order')[:3]

            # Determine interval (you can make this configurable, e.g., via query param)
            interval = request.GET.get('interval', 'monthly')

            for idx, plan in enumerate(active_plans):
                try:
                    resolved_price = resolve_plan_price(plan, interval, request)
                    price = resolved_price.price_cents / 100  # convert to base unit
                    currency = resolved_price.currency
                    symbol = self._get_currency_symbol(currency)

                    is_geo = hasattr(resolved_price, 'country')  # True for GeoPlanPrice

                except Exception as e:
                    logger.warning(f"Could not resolve price for {plan.name}: {e}")
                    # Fallback to first active PlanPrice
                    from apps.subscriptions.models import PlanPrice
                    price_obj = PlanPrice.objects.filter(plan=plan, interval=interval, is_active=True).first()
                    if price_obj:
                        price = price_obj.price_cents / 100
                        currency = price_obj.currency
                        symbol = self._get_currency_symbol(currency)
                    else:
                        continue  # skip this plan

                # Build feature list based on tier
                features = self._get_features_for_tier(plan.tier)

                plans.append({
                    'plan_id': str(plan.id),
                    'name': plan.name,
                    'description': plan.description or self._get_default_desc(plan.tier),
                    'price': int(price) if price.is_integer() else price,
                    'currency_symbol': symbol,
                    'original_price': None,   # you can compute if needed
                    'savings': None,
                    'is_popular': plan.tier == 'pro',
                    'features': features,
                    'is_geo': is_geo,         # optional, for debugging
                })

            return plans

        except Exception as e:
            logger.warning(f"Could not load plans from database: {e}")
            return []

    def _get_default_price(self, request):
        """Get geo‑resolved default price for hero section."""
        try:
            from apps.subscriptions.models import Plan
            from apps.subscriptions.services import resolve_plan_price

            basic = Plan.objects.filter(tier='basic', is_active=True).first()
            if basic:
                resolved = resolve_plan_price(basic, 'monthly', request)
                return int(resolved.price_cents / 100)
        except Exception:
            pass
        return 47

    def _get_currency_symbol(self, currency_code):
        """Return currency symbol for given ISO code."""
        symbols = {
            'USD': '$',
            'INR': '₹',
            'EUR': '€',
            'GBP': '£',
            'JPY': '¥',
        }
        return symbols.get(currency_code.upper(), currency_code)

    def _get_default_desc(self, tier):
        """Default descriptions by tier."""
        descriptions = {
            'basic': 'Perfect for traders learning real-time execution',
            'pro': 'For serious traders who want unlimited guidance',
            'enterprise': 'White-glove service for professional traders',
        }
        return descriptions.get(tier, 'Full-featured access')

    def _get_features_for_tier(self, tier):
        """Generate feature list based on plan tier."""
        if tier == 'basic':
            return [
                {'text': '5 real-time trades per week', 'disabled': False},
                {'text': 'Entry, stop & target alerts', 'disabled': False},
                {'text': 'Basic risk management', 'disabled': False},
                {'text': 'Email notifications', 'disabled': False},
                {'text': '24/7 chat support', 'disabled': False},
                {'text': 'Advanced analytics', 'disabled': True},
            ]
        elif tier == 'pro':
            return [
                {'text': 'Unlimited real-time trades', 'disabled': False},
                {'text': 'Entry, updates & exit alerts', 'disabled': False},
                {'text': 'Advanced risk management', 'disabled': False},
                {'text': 'SMS + Email notifications', 'disabled': False},
                {'text': '24/7 chat support', 'disabled': False},
                {'text': 'Full trade history & review', 'disabled': False},
            ]
        else:
            return [
                {'text': 'Everything in Pro, plus:', 'disabled': False},
                {'text': '1-on-1 monthly strategy call', 'disabled': False},
                {'text': 'Priority trade alerts (faster)', 'disabled': False},
                {'text': 'Custom risk parameters', 'disabled': False},
                {'text': 'API access Soon', 'disabled': False},
                {'text': 'White-label exports', 'disabled': False},
            ]


class CustomLoginView(auth_views.LoginView):
    """
    Custom login using your provided template.
    Template: templates/auth/login.html
    """
    template_name = "auth/login.html"
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['site_name'] = getattr(settings, 'SITE_NAME', 'TradeAdmin')
        context['trial_plan'] = {
            'trial_price': 7,
            'duration_days': 7
        }
        return context

    def get_success_url(self):
        """Redirect after successful login."""
        from django.urls import reverse
        return reverse('dashboard')