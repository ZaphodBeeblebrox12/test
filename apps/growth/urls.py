"""
URL configuration for growth app.
"""
from django.urls import path

from . import views, api

app_name = "growth"

urlpatterns = [
    # Token-based gift claim flow (web interface)
    path(
        "claim/<str:token>/",
        views.GiftClaimView.as_view(),
        name="claim"
    ),
    path(
        "claim/success/<str:subscription_id>/",
        views.GiftClaimSuccessView.as_view(),
        name="claim_success"
    ),
    path(
        "claim/error/",
        views.GiftClaimErrorView.as_view(),
        name="claim_error"
    ),

    # Legacy gift code claim flow (web interface)
    path(
        "claim-code/",
        views.LegacyGiftClaimView.as_view(),
        name="legacy_claim"
    ),
    path(
        "claim-code/success/<str:subscription_id>/",
        views.LegacyGiftClaimSuccessView.as_view(),
        name="legacy_claim_success"
    ),

    # API endpoints
    path(
        "api/growth/gifts/claim-code/",
        api.ClaimGiftCodeView.as_view(),
        name="api_claim_code"
    ),
    path(
        "api/growth/gifts/claim-token/",
        api.ClaimTokenView.as_view(),
        name="api_claim_token"
    ),
]
