"""
Telegram authentication URLs.
"""
from django.urls import path

from apps.accounts.telegram_views import TelegramAuthView, TelegramCallbackView

urlpatterns = [
    path("login/", TelegramAuthView.as_view(), name="telegram_login"),
    path("callback/", TelegramCallbackView.as_view(), name="telegram_callback"),
]
