"""
URL configuration for community platform.
"""
from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views

# Import allauth views for customization
from allauth.account.views import SignupView

urlpatterns = [
    # Growth admin views - MUST be first to avoid catch_all_view interception
    path("admin/", include("apps.growth.admin_urls", namespace="growth_admin")),

    # Django admin (default) - comes after so growth URLs take precedence
    path("admin/", admin.site.urls),
    path('', include('apps.public_views.urls')),

    # Custom auth views (before allauth to take precedence)
    path("accounts/login/", auth_views.LoginView.as_view(
        template_name="auth/login.html",
        redirect_authenticated_user=True,
    ), name="account_login"),

    # NEW: Custom signup view using allauth SignupView with custom template
    path("accounts/signup/", SignupView.as_view(
        template_name="auth/signup.html",
    ), name="account_signup"),

    path("accounts/", include("allauth.urls")),
    path("api/auth/", include("apps.accounts.urls")),
    path("api/admin/", include("apps.accounts.admin_urls")),
    path("auth/telegram/", include("apps.accounts.telegram_urls")),
    path("auth/discord/", include("apps.accounts.discord_urls")),
    
    # Subscriptions API
    path("api/subscriptions/", include("apps.subscriptions.urls")),
    
    path("api/", include("apps.api.urls")),
    path("", include("apps.core.urls")),
    path("", include("apps.accounts.profile_urls")),
    path("", include("apps.payments.urls")),
    path("growth/", include("apps.growth.urls")),

    # NEW: Bot integration app (Telegram deep link, Discord OAuth, channel sync)
    path("bot/", include("apps.bot_integration.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)