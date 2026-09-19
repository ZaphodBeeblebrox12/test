"""Ban enforcement.

Pre-existing debt: User.is_banned existed but nothing enforced it, so a banned
user could still browse (the dashboard tests expected a 403). This middleware
returns 403 + the existing accounts/banned.html template for authenticated
banned users on app pages, while never intercepting auth/logout/static/admin
paths (a banned user must be able to log out).
"""
from django.shortcuts import render


class BanEnforcementMiddleware:
    EXEMPT_PREFIXES = (
        "/accounts/logout", "/accounts/login", "/admin", "/static",
        "/media", "/api/auth", "/api/accounts",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (user and user.is_authenticated
                and getattr(user, "is_banned", False)
                and not request.path.startswith(self.EXEMPT_PREFIXES)):
            return render(request, "accounts/banned.html", status=403)
        return self.get_response(request)
