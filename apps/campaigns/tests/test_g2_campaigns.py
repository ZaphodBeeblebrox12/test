"""G2: campaigns, audiences, send idempotency, snapshot, metrics."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.campaigns.models import (
    Audience, Campaign, CampaignMetrics, CampaignRecipient)
from apps.campaigns.services.campaigns import campaign_send_sweep, schedule_campaign
from apps.emailing.models import Delivery, Template, TemplateVersion
from apps.growth.models import MarketingPreference, Suppression

User = get_user_model()


def make_template(html="<p>Hi {{ first_name }}</p>"):
    t = Template.objects.create(name="Camp", kind="marketing")
    v = TemplateVersion.objects.create(
        template=t, version_number=1, editor_mode="html",
        subject="Subject", preview_text="", html=html, plain_text="")
    return t, v


def make_user(email, username=None, marketing=True):
    u = User.objects.create_user(username=username or email.split("@")[0], email=email, password="pw")
    MarketingPreference.objects.create(user=u, marketing_opt_in=marketing)
    return u


class ScheduleSnapshotTests(TestCase):
    def test_snapshot_materializes_members(self):
        u1 = make_user("a@x.com"); u2 = make_user("b@x.com")
        aud = Audience.objects.create(name="All", rules={})
        t, v = make_template()
        c = Campaign.objects.create(name="C", template_version=v, audience=aud)
        schedule_campaign(c)
        self.assertEqual(CampaignRecipient.objects.filter(campaign=c).count(), 2)
        c.refresh_from_db()
        self.assertEqual(c.state, Campaign.State.SCHEDULED)
        self.assertIsNotNone(c.snapshot_at)

    def test_snapshot_respects_marketing_opt_in(self):
        u1 = make_user("in@x.com", marketing=True)
        u2 = make_user("out@x.com", marketing=False)
        aud = Audience.objects.create(name="M", rules={})
        _, v = make_template()
        c = Campaign.objects.create(name="C", template_version=v, audience=aud)
        schedule_campaign(c)
        self.assertEqual(CampaignRecipient.objects.filter(
            campaign=c, status=CampaignRecipient.Status.QUEUED).count(), 1)
        self.assertEqual(CampaignRecipient.objects.filter(
            campaign=c, status=CampaignRecipient.Status.SKIPPED).count(), 1)


class SendIdempotencyTests(TestCase):
    def setUp(self):
        self.users = [make_user(f"u{i}@x.com") for i in range(3)]
        self.aud = Audience.objects.create(name="A", rules={})
        _, self.v = make_template()
        self.c = Campaign.objects.create(name="C", template_version=self.v, audience=self.aud)
        schedule_campaign(self.c)

    def test_send_creates_deliveries_and_marks_done(self):
        result = campaign_send_sweep(self.c.id)
        self.assertTrue(result["done"])
        self.assertEqual(result["sent"], 3)
        self.assertEqual(Delivery.objects.count(), 3)
        self.assertEqual(CampaignRecipient.objects.filter(
            status=CampaignRecipient.Status.SENT).count(), 3)

    def test_duplicate_sweep_no_duplicate_deliveries(self):
        campaign_send_sweep(self.c.id)
        again = campaign_send_sweep(self.c.id)
        self.assertEqual(again["sent"], 0)  # nothing left to claim
        self.assertEqual(Delivery.objects.count(), 3)  # idempotent_key dedup

    def test_concurrent_claim_no_double_send(self):
        # simulate a concurrent sweeper: claim the same QUEUED rows
        CampaignRecipient.objects.filter(campaign=self.c).update(status="queued")
        from apps.campaigns.services.campaigns import _claim_recipients
        batch1 = _claim_recipients(self.c)
        batch2 = _claim_recipients(self.c)  # should claim nothing
        self.assertEqual(len(batch1), 3)
        self.assertEqual(len(batch2), 0)


class SnapshotImmutabilityTests(TestCase):
    def test_template_edit_does_not_change_snapshot(self):
        u = make_user("s@x.com")
        aud = Audience.objects.create(name="A", rules={})
        t, v1 = make_template(html="<p>V1</p>")
        c = Campaign.objects.create(name="C", template_version=v1, audience=aud)
        schedule_campaign(c)
        campaign_send_sweep(c.id)
        d = Delivery.objects.get()
        self.assertEqual(d.template_version_id, v1.id)
        # author a NEW version; campaign keeps v1
        v2 = TemplateVersion.objects.create(
            template=t, version_number=2, editor_mode="html",
            subject="S2", html="<p>V2</p>")
        self.assertEqual(c.template_version_id, v1.id)
        self.assertNotEqual(c.template_version_id, v2.id)


class MetricsTests(TestCase):
    def test_metrics_updated_from_sweep(self):
        make_user("m1@x.com"); make_user("m2@x.com")
        aud = Audience.objects.create(name="A", rules={})
        _, v = make_template()
        c = Campaign.objects.create(name="C", template_version=v, audience=aud)
        schedule_campaign(c)
        campaign_send_sweep(c.id)
        m = CampaignMetrics.objects.get(campaign=c)
        self.assertEqual(m.total, 2)
        self.assertEqual(m.sent, 2)
