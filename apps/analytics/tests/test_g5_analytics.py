"""G5: signup attribution, last-touch purchase credit, analytics reports."""
import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.analytics.models import SignupAttribution
from apps.analytics.services.attribution import (
    attribute_purchase, capture_signup_attribution)
from apps.analytics.services import report
from apps.campaigns.models import Audience, Campaign, CampaignMetrics, CampaignRecipient
from apps.emailing.models import Delivery, Template, TemplateVersion
from apps.events.models import record_event

User = get_user_model()


class SignupAttributionTests(TestCase):
    def test_capture_first_touch(self):
        u = User.objects.create_user(username="a1", password="pw")
        obj, created = capture_signup_attribution(
            u, utm={"utm_source": "google", "utm_campaign": "launch"})
        self.assertTrue(created)
        self.assertEqual(obj.utm_source, "google")
        self.assertEqual(obj.utm_campaign, "launch")

    def test_first_touch_wins_on_repeat(self):
        u = User.objects.create_user(username="a2", password="pw")
        capture_signup_attribution(u, utm={"utm_source": "first"})
        obj, created = capture_signup_attribution(u, utm={"utm_source": "second"})
        self.assertFalse(created)
        self.assertEqual(obj.utm_source, "first")  # first touch preserved


class LastTouchTests(TestCase):
    def _template(self):
        import uuid
        t = Template.objects.create(name=f"T-{uuid.uuid4().hex[:6]}", kind="marketing")
        return TemplateVersion.objects.create(template=t, version_number=1, editor_mode="html",
                                              subject="s", html="<p>x</p>")

    def _delivery(self, user, days_ago, campaign=None):
        v = self._template()
        d = Delivery.objects.create(
            idempotency_key=f"lt-{user.username}-{days_ago}-{v.id}", template_version=v, user=user,
            to_email=f"{user.username}@x.com", kind="marketing", subject="s",
            state=Delivery.State.SENT, provider="stub",
            sent_at=timezone.now() - datetime.timedelta(days=days_ago))
        if campaign:
            CampaignRecipient.objects.create(
                campaign=campaign, user=user, email=d.to_email, status="sent", delivery=d)
        return d

    def test_last_touch_before_purchase_gets_credit(self):
        u = User.objects.create_user(username="lt1", password="pw")
        aud = Audience.objects.create(name="A", rules={})
        tv = self._template()
        camp = Campaign.objects.create(name="Launch", template_version=tv, audience=aud)
        self._delivery(u, days_ago=3)                        # older, no campaign link
        d2 = self._delivery(u, days_ago=1, campaign=camp)    # last touch
        purchase_at = timezone.now()
        credit = attribute_purchase(u, purchase_at)
        self.assertIsNotNone(credit)
        self.assertEqual(credit["campaign_name"], "Launch")

    def test_no_delivery_no_credit(self):
        u = User.objects.create_user(username="lt2", password="pw")
        self.assertIsNone(attribute_purchase(u, timezone.now()))

    def test_delivery_after_purchase_not_credited(self):
        u = User.objects.create_user(username="lt3", password="pw")
        self._delivery(u, days_ago=-1)  # sent AFTER purchase
        self.assertIsNone(attribute_purchase(u, timezone.now() - datetime.timedelta(days=2)))


class ReportTests(TestCase):
    def test_signup_analytics_aggregates_purchases(self):
        u = User.objects.create_user(username="r1", password="pw")
        for i in range(3):
            record_event("purchase.completed", dedupe_key=f"rep{i}",
                         user_id=u.id, payload={"amount": 500})
        result = report.signup_analytics(days=7)
        self.assertEqual(result["total_purchases"], 3)
        self.assertEqual(result["purchases_per_day"][0]["revenue_cents"], 1500)

    def test_email_performance_counts_states(self):
        u = User.objects.create_user(username="r2", password="pw")
        t = Template.objects.create(name="T", kind="marketing")
        v = TemplateVersion.objects.create(template=t, version_number=1, editor_mode="html",
                                           subject="s", html="<p>x</p>")
        Delivery.objects.create(idempotency_key="e1", template_version=v, user=u,
                                to_email="r2@x.com", kind="marketing", subject="s",
                                state=Delivery.State.SENT, provider="stub")
        Delivery.objects.create(idempotency_key="e2", template_version=v, user=u,
                                to_email="r2@x.com", kind="marketing", subject="s",
                                state=Delivery.State.BOUNCED, provider="stub")
        result = report.email_performance(days=7)
        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["bounced"], 1)

    def test_campaign_performance_from_metrics(self):
        u = User.objects.create_user(username="r3", password="pw")
        aud = Audience.objects.create(name="A", rules={})
        t = Template.objects.create(name="T", kind="marketing")
        tv = TemplateVersion.objects.create(template=t, version_number=1, editor_mode="html",
                                            subject="s", html="<p>x</p>")
        camp = Campaign.objects.create(name="C", template_version=tv, audience=aud)
        CampaignMetrics.objects.create(campaign=camp, total=5, sent=4, failed=1)
        result = report.campaign_performance()
        self.assertEqual(result[0]["campaign"], "C")
        self.assertEqual(result[0]["sent"], 4)
