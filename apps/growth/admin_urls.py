"""
Admin URLs for growth app.
"""
from django.urls import path
from . import admin_views

urlpatterns = [
    path(
        'growth/send-gift/',
        admin_views.SendGiftAdminView.as_view(),
        name='growth_send_gift'
    ),
    path(
        'growth/send-gift/success/<str:gift_invite_id>/',
        admin_views.SendGiftSuccessAdminView.as_view(),
        name='growth_send_gift_success'
    ),
]
