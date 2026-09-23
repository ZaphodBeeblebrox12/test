"""
Payment URL configuration.
"""
from django.urls import path
from django.views.generic import TemplateView

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
    path("support/", TemplateView.as_view(template_name="support.html"),
         name="support"),
    path("policies/refund/", TemplateView.as_view(template_name="policies/refund.html"),
         name="refund-policy"),
    path("policies/terms/", TemplateView.as_view(template_name="policies/terms.html"),
         name="terms"),
    path("policies/risk/", TemplateView.as_view(template_name="policies/risk.html"),
         name="risk-disclaimer"),
    path("upgrade/", views.upgrade_page, name="upgrade-page"),
    path("upgrade/start/", views.upgrade_start, name="upgrade-start"),
    # Phase 3: Operator's Cockpit (staff only)
    path("staff/ops/", views.ops_dashboard, name="ops-dashboard"),
    path("staff/ops/reconcile/<uuid:user_id>/", views.ops_reconcile_user,
         name="ops-reconcile-user"),
    path("staff/ops/payments.csv", views.ops_payments_csv, name="ops-payments-csv"),
    path("staff/user/<uuid:pk>/", views.ops_user_detail, name="ops-user-detail"),
    path("checkout/<uuid:pk>/", views.CheckoutPageView.as_view(), name="checkout-page"),
    path("confirm-page/<uuid:pk>/", views.ConfirmPageView.as_view(), name="confirm-page"),
    path("webhooks/stripe/", webhook_views.stripe_webhook, name="stripe-webhook"),
    path("webhooks/razorpay/", webhook_views.razorpay_webhook, name="razorpay-webhook"),
]
