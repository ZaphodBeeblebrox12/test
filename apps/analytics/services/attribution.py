"""G5 attribution: first-touch signup capture + last-touch purchase credit."""
from ..models import SignupAttribution


def capture_signup_attribution(user, *, utm=None, referring_campaign=None):
    """Record the user's FIRST-touch UTM/campaign.  Idempotent: existing
    attribution is preserved (first touch wins); only fills empty fields."""
    utm = utm or {}
    obj, created = SignupAttribution.objects.get_or_create(
        user=user,
        defaults={
            "utm_source": utm.get("utm_source", ""),
            "utm_medium": utm.get("utm_medium", ""),
            "utm_campaign": utm.get("utm_campaign", ""),
            "utm_term": utm.get("utm_term", ""),
            "utm_content": utm.get("utm_content", ""),
            "referring_campaign": referring_campaign,
        },
    )
    return obj, created


def attribute_purchase(user, purchased_at):
    """LAST-TOUCH: credit the most recent marketing Delivery sent to the user
    before the purchase.  Returns a dict describing the credit, or None."""
    from apps.emailing.models import Delivery
    from apps.campaigns.models import CampaignRecipient
    from apps.automation.models import AutomationRun

    delivery = (Delivery.objects.filter(
        user=user, kind="marketing", state=Delivery.State.SENT, sent_at__lte=purchased_at)
        .order_by("-sent_at").first())
    if delivery is None:
        return None
    # Walk the provenance FKs to find which campaign/automation sent it.
    cr = CampaignRecipient.objects.filter(delivery=delivery).select_related("campaign").first()
    ar = AutomationRun.objects.filter(delivery=delivery).select_related("rule").first()
    return {
        "delivery_id": str(delivery.id),
        "sent_at": delivery.sent_at,
        "campaign_id": str(cr.campaign_id) if cr else None,
        "campaign_name": cr.campaign.name if cr else None,
        "automation_rule_id": str(ar.rule_id) if ar else None,
        "automation_rule_name": ar.rule.name if ar else None,
    }
