"""
URL configuration for community platform.
"""
from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import logout
from django.shortcuts import render, redirect

def logout_view(request):
    """Logout view that logs out user and shows confirmation page."""
    if request.method == 'POST':
        logout(request)
        return render(request, 'logout.html', {'logged_out': True})
    # For GET requests, also log out and show confirmation
    logout(request)
    return render(request, 'logout.html', {'logged_out': True})

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("api/auth/", include("apps.accounts.urls")),
    path("api/admin/", include("apps.accounts.admin_urls")),
    path("auth/telegram/", include("apps.accounts.telegram_urls")),
    path("api/notifications/", include("apps.notifications.urls")),
    path("api/", include("apps.api.urls")),
    path("", include("apps.core.urls")),
    path("", include("apps.accounts.profile_urls")),
    # Logout URL - renders logout template
    path("logout/", logout_view, name="account_logout"),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
