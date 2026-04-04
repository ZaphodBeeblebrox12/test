from django.urls import path
from . import views

app_name = 'bot_integration'

urlpatterns = [
    path('telegram/connect/', views.start_telegram_connect, name='telegram_connect'),
    path('telegram/unlink/', views.unlink_telegram, name='telegram_unlink'),
    path('telegram/webhook/', views.telegram_webhook, name='telegram_webhook'),
    path('discord/connect/', views.discord_oauth_start, name='discord_connect'),
    path('discord/unlink/', views.unlink_discord, name='discord_unlink'),
    path('discord/callback/', views.discord_oauth_callback, name='discord_callback'),
]