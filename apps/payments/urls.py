"""
Payment URL configuration.
"""
from django.urls import path
from django.views.generic import TemplateView

from apps.policies.views import terms_page, refund_page, risk_page, privacy_page

from . import views, webhook_views

urlpatterns = [
    path("start/", views.payment_start, name="payment-start"),
    path("confirm/", views.payment_confirm, name="payment-confirm"),
    path("status/<uuid:payment_intent_id>/", views.payment_status, name="payment-status"),
    path("history/", views.payment_history, name="payment-history"),
    path("receipt/<uuid:pk>/", views.payment_receipt, name="payment-receipt"),
    path("subscription/manage/", views.manage_subscription_page,
         name="manage-subscription"),
    # Support & policy pages (linked from transactional emails)
    # NOTE: /support/ is now the login-required ticket system (apps.support).
    # This static help page moved to /help/; the route name is unchanged so
    # any reverse("support") callers keep working.
    path("help/", TemplateView.as_view(template_name="support.html"),
         name="support"),
    # Versioned policy pages (served from immutable PolicyVersion rows; the
    # historical versions remain viewable from the dispute admin). Route
    # NAMES unchanged so existing reverse() callers keep working.
    path("policies/refund/", refund_page, name="refund-policy"),
    path("policies/terms/", terms_page, name="terms"),
    path("policies/risk/", risk_page, name="risk-disclaimer"),
    path("policies/privacy/", privacy_page, name="privacy-policy"),
    path("upgrade/", views.upgrade_page, name="upgrade-page"),
    path("upgrade/start/", views.upgrade_start, name="upgrade-start"),
    # Phase 3: Operator's Cockpit (staff only)
    path("staff/ops/", views.ops_dashboard, name="ops-dashboard"),
    path("staff/ops/reconcile/<uuid:user_id>/", views.ops_reconcile_user,
         name="ops-reconcile-user"),
    path("staff/ops/payments.csv", views.ops_payments_csv, name="ops-payments-csv"),
    path("staff/user/<uuid:pk>/", views.ops_user_detail, name="ops-user-detail"),
    # Chargeback evidence capture: the consent step (ONE checkbox) happens
    # BEFORE the provider handoff. Must precede the checkout bridge route.
    path("checkout/consent/<uuid:pk>/", views.CheckoutConsentView.as_view(),
         name="checkout-consent"),
    path("checkout/<uuid:pk>/", views.CheckoutPageView.as_view(), name="checkout-page"),
    path("confirm-page/<uuid:pk>/", views.ConfirmPageView.as_view(), name="confirm-page"),
    path("webhooks/stripe/", webhook_views.stripe_webhook, name="stripe-webhook"),
    path("webhooks/razorpay/", webhook_views.razorpay_webhook, name="razorpay-webhook"),
]
