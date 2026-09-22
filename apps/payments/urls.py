"""
Payment URL configuration.
"""
from django.urls import path

from . import views, webhook_views

urlpatterns = [
    path("start/", views.payment_start, name="payment-start"),
    path("confirm/", views.payment_confirm, name="payment-confirm"),
    path("status/<uuid:payment_intent_id>/", views.payment_status, name="payment-status"),
    path("history/", views.payment_history, name="payment-history"),
    path("checkout/<uuid:pk>/", views.CheckoutPageView.as_view(), name="checkout-page"),
    path("confirm-page/<uuid:pk>/", views.ConfirmPageView.as_view(), name="confirm-page"),
    path("webhooks/stripe/", webhook_views.stripe_webhook, name="stripe-webhook"),
    path("webhooks/razorpay/", webhook_views.razorpay_webhook, name="razorpay-webhook"),
]
