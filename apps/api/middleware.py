"""
API middleware for community platform.
"""
from django.shortcuts import redirect, render
from django.urls import reverse


class BanEnforcementMiddleware:
    """Middleware to enforce ban status on authenticated users."""

    def __init__(self, get_response):
        self.get_response = get_response
        # URLs that banned users are allowed to access
        self.allowed_urls = [
            '/accounts/logout/',
            '/admin/',
            '/banned/',
            '/static/',
            '/media/',
        ]

    def __call__(self, request):
        # Check if user is authenticated and banned
        if request.user.is_authenticated and request.user.is_banned:
            # Check if current path is allowed
            path = request.path
            is_allowed = any(
                path.startswith(allowed) for allowed in self.allowed_urls
            )

            if not is_allowed:
                # Return banned page
                return render(request, 'accounts/banned.html', {
                    'ban_reason': request.user.ban_reason
                }, status=403)

        response = self.get_response(request)
        return response


class APIRequestLoggingMiddleware:
    """Middleware to log API requests."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Only log API requests
        if request.path.startswith('/api/'):
            # Process request
            response = self.get_response(request)

            # Log the request (async via celery would be better for production)
            try:
                from apps.api.models import APIRequestLog
                from apps.api.authentication import APIKeyAuthentication

                # Get API key if present
                api_key = None
                auth_header = request.META.get('HTTP_X_API_KEY', '')
                if auth_header:
                    try:
                        api_key = APIKeyAuthentication().get_api_key(auth_header)
                    except:
                        pass

                # Create log entry
                APIRequestLog.objects.create(
                    api_key=api_key,
                    user=request.user if request.user.is_authenticated else None,
                    endpoint=request.path,
                    method=request.method,
                    status_code=response.status_code,
                    response_time_ms=0,  # Would need timing middleware
                    ip_address=self.get_client_ip(request),
                    user_agent=request.META.get('HTTP_USER_AGENT', '')[:255],
                )
            except:
                pass  # Don't fail if logging fails

            return response

        return self.get_response(request)

    def get_client_ip(self, request):
        """Get client IP address."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR', '')
        return ip
