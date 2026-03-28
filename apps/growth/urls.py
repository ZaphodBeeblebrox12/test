"""
Growth app URL configuration.
"""
from django.urls import path

from . import views

app_name = "growth"

urlpatterns = [
    path("r/<str:code>/", views.CaptureReferralCodeView.as_view(), name="capture_referral"),
    path("referrals/", views.ReferralDashboardView.as_view(), name="referral_dashboard"),
    path("referrals/api/rewards/", views.ReferralRewardsAPIView.as_view(), name="referral_rewards_api"),
    path("admin/referral-rewards/", views.AdminReferralRewardsView.as_view(), name="admin_referral_rewards"),
]
