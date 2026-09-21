"""Campaign admin workspace tests (single-hub UX)."""
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test.client import RequestFactory

from apps.advanced_growth.models import CampaignAction, Channel
from apps.campaigns.admin import (
    CampaignActionInline, CampaignAdmin, CampaignRecipientInline,
)
from apps.campaigns.models import (
    Audience, Campaign, CampaignMetrics, CampaignRecipient,
)
from apps.emailing.models import Template, TemplateVersion

User = get_user_model()


def auth_req():
    rf = RequestFactory()
    request = rf.get("/admin/campaigns/campaign/1/change/")
    request.user = User.objects.create_superuser(
        username="ops", email="ops@x.com", password="pw")
    return request


def make_template():
    t = Template.objects.create(name="T", kind="marketing")
    return TemplateVersion.objects.create(
        template=t, version_number=1, editor_mode="html", subject="s", html="<p>x</p>")


class NavigationTests(TestCase):
    def test_campaign_action_not_top_level(self):
        self.assertFalse(admin.site.is_registered(CampaignAction))

    def test_channel_not_top_level(self):
        self.assertFalse(admin.site.is_registered(Channel))

    def test_recipient_and_metrics_folded_into_campaign(self):
        self.assertFalse(admin.site.is_registered(CampaignRecipient))
        self.assertFalse(admin.site.is_registered(CampaignMetrics))
        # models themselves are untouched
        self.assertEqual(CampaignRecipient._meta.app_label, "campaigns")
        self.assertEqual(CampaignMetrics._meta.app_label, "campaigns")

    def test_hub_and_libraries_remain_registered(self):
        self.assertTrue(admin.site.is_registered(Campaign))
        self.assertTrue(admin.site.is_registered(Audience))


class ChannelSeedTests(TestCase):
    def test_seed_data_exists_exactly_once(self):
        kinds = set(Channel.objects.values_list("kind", flat=True))
        self.assertEqual(kinds, {"email", "telegram", "in_product"})
        self.assertEqual(Channel.objects.count(), 3)  # unique kind constraint


class CampaignWorkspaceTests(TestCase):
    def setUp(self):
        self.request = auth_req()

    def _admin(self):
        return CampaignAdmin(Campaign, admin.site)

    def test_actions_inline_present(self):
        self.assertIn(CampaignActionInline, CampaignAdmin.inlines)

    def test_recipient_inline_present_and_readonly(self):
        self.assertIn(CampaignRecipientInline, CampaignAdmin.inlines)
        inline = CampaignRecipientInline(CampaignRecipient, admin.site)
        self.assertFalse(inline.has_add_permission(self.request))
        self.assertFalse(inline.can_delete)

    def test_email_channel_excluded_from_action_inline(self):
        inline = CampaignActionInline(CampaignAction, admin.site)
        field = CampaignAction._meta.get_field("channel")
        formfield = inline.formfield_for_foreignkey(field, request=self.request)
        kinds = set(formfield.queryset.values_list("kind", flat=True))
        self.assertEqual(kinds, {"telegram", "in_product"})

    def test_email_source_of_truth_is_campaign_fields(self):
        flat = [f for fs in CampaignAdmin.fieldsets for f in fs[1]["fields"]]
        self.assertIn("template_version", flat)
        self.assertIn("subject_override", flat)

    def test_unsupported_channels_not_shown_as_executable(self):
        self.assertIn("state", CampaignActionInline.readonly_fields)
        self.assertIn("delivery_status", CampaignActionInline.readonly_fields)

    def test_metrics_readonly_on_campaign(self):
        for name in ("results_summary", "audit_links", "snapshot_at", "state"):
            self.assertIn(name, CampaignAdmin.readonly_fields)

    def test_schedule_and_send_actions_intact(self):
        self.assertIn("action_schedule", CampaignAdmin.actions)
        self.assertIn("action_send_batch", CampaignAdmin.actions)

    def test_audience_size_links_to_audience(self):
        a = Audience.objects.create(name="All", rules={})
        User.objects.create_user(username="m1", email="m1@x.com", password="pw")
        c = Campaign.objects.create(name="C", audience=a,
                                    template_version=make_template())
        expected = User.objects.count()
        self.assertIn("%d current member" % expected,
                      str(self._admin().audience_size(c)))

    def test_results_summary_handles_missing_metrics(self):
        a = Audience.objects.create(name="All", rules={})
        c = Campaign.objects.create(name="C", audience=a,
                                    template_version=make_template())
        self.assertIn("No metrics yet", self._admin().results_summary(c))
