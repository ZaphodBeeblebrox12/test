from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import report


class AnalyticsOverviewView(APIView):
    """Read-only marketing analytics over the durable Event store."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response({
            "signups": report.signup_analytics(),
            "email": report.email_performance(),
            "campaigns": report.campaign_performance(),
            "promotions": report.promotion_performance(),
            "referrals": report.referral_performance(),
        })
