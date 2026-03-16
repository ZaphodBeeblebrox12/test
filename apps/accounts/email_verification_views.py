
# Add this to apps/accounts/views.py

from django.views import View
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator


@method_decorator(login_required, name="dispatch")
class EmailVerificationRequiredView(View):
    """View to show when email verification is required."""

    def get(self, request):
        return render(request, "account/verification_required.html")
