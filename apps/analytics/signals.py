from django.dispatch import receiver

from apps.analytics.services.attribution import capture_signup_attribution


@receiver(__import__("allauth.account.signals", fromlist=["user_signed_up"]).user_signed_up)
def record_signup_attribution(request, user, **kwargs):
    """Capture first-touch UTM from the signup request into SignupAttribution."""
    utm = {k: request.GET.get(k, "") for k in
           ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content")}
    if any(utm.values()):
        capture_signup_attribution(user, utm=utm)
