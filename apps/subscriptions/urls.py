"""
URL configuration for subscriptions app.
"""
from django.urls import path

from . import views

app_name = "subscriptions"

urlpatterns = [
    path("plans/", views.plan_list, name="plan-list"),
    path("subscription/me/", views.my_subscription, name="my-subscription"),
]
