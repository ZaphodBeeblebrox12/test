"""
Core URL configuration.
"""
from django.urls import path

from apps.core.views import index

urlpatterns = [
    path("", index, name="index"),
]
