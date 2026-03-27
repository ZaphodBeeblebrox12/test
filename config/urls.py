"""
URL configuration for community platform.
"""
from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # Growth admin views - MUST be first to avoid catch_all_view interception
    path("admin/", include("apps.growth.admin_urls", namespace="growth_admin")),

    # Django admin (default) - comes after so growth URLs take precedence
    path("admin/", admin.site.urls),

    path("accounts/", include("allauth.urls")),
    path("api/auth/", include("apps.accounts.urls")),
    path("api/admin/", include("apps.accounts.admin_urls")),
    path("auth/telegram/", include("apps.accounts.telegram_urls")),
    path("auth/discord/", include("apps.accounts.discord_urls")),
    path("api/", include("apps.api.urls")),
    path("", include("apps.core.urls")),
    path("", include("apps.accounts.profile_urls")),
    path("", include("apps.payments.urls")),
    path("growth/", include("apps.growth.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
