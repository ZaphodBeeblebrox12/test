from django.utils.deprecation import MiddlewareMixin
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import decorator_from_middleware


class DisableCSRFForWebhook(MiddlewareMixin):
    """Disable CSRF for the Telegram webhook endpoint."""
    
    def process_request(self, request):
        if request.path == "/bot/telegram/webhook/":
            # Disable CSRF processing completely
            setattr(request, '_dont_enforce_csrf_checks', True)
            request.csrf_processing_done = True
            # Also mark the request as exempt
            request._csrf_exempt = True