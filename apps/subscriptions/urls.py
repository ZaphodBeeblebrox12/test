"""
URL configuration for subscriptions app.
"""
from django.urls import path

from . import views

app_name = "subscriptions"

urlpatterns = [
    # Existing basic endpoints
    path("plans/", views.plan_list, name="plan-list"),
    path("subscription/me/", views.my_subscription, name="my-subscription"),

    # ========== GEO PRICING ENDPOINTS ==========
    path("plans/geo/", views.plan_list_geo, name="plan-list-geo"),
    path("plans/<uuid:plan_id>/geo/", views.plan_detail_geo, name="plan-detail-geo"),

    # ========== GIFT SUBSCRIPTIONS ==========
    path("gifts/create/", views.create_gift, name="create-gift"),
    path("gifts/claim/", views.claim_gift, name="claim-gift"),
    path("gifts/my/", views.my_gifts, name="my-gifts"),

    # ========== ADMIN GRANTS & TRIALS ==========
    path("admin/grant/", views.admin_grant_subscription, name="admin-grant"),
    path("admin/trial/", views.admin_start_trial, name="admin-trial"),

    # ========== HISTORY & UPGRADES ==========
    path("history/", views.subscription_history, name="subscription-history"),
    path("upgrades/", views.upgrade_history_list, name="upgrade-history"),
]