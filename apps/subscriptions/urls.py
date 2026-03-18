"""
URL configuration for subscriptions app - Phase 3B.
"""
from django.urls import path

from . import views

app_name = "subscriptions"

urlpatterns = [
    # Phase 3A endpoints
    path("plans/", views.plan_list, name="plan-list"),
    path("subscription/me/", views.my_subscription, name="my-subscription"),

    # Phase 3B - Upgrades
    path("subscription/upgrade/", views.upgrade_subscription, name="subscription-upgrade"),

    # Phase 3B - Discounts
    path("discounts/validate/", views.validate_discount, name="discount-validate"),

    # Phase 3B - Gifts
    path("gifts/create/", views.create_gift, name="gift-create"),
    path("gifts/redeem/", views.redeem_gift, name="gift-redeem"),

    # Phase 3B - Admin
    path("admin/grant/", views.admin_grant_subscription, name="admin-grant"),
    path("admin/events/unprocessed/", views.list_unprocessed_events, name="admin-unprocessed-events"),
]
