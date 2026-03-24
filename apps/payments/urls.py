"""
Payment URL configuration.
"""
from django.urls import path

from . import views

urlpatterns = [
    path("start/", views.payment_start, name="payment-start"),
    path("confirm/", views.payment_confirm, name="payment-confirm"),
    path("status/<uuid:payment_intent_id>/", views.payment_status, name="payment-status"),
]
