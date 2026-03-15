"""
Admin URL configuration for accounts.
"""
from django.urls import path

from apps.accounts.debug_views import debug_users_list

urlpatterns = [
    path("debug/users/", debug_users_list, name="admin_debug_users"),
]
