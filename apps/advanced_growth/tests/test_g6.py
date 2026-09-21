"""G6: affiliates, experiments, multi-channel, announcements."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
import datetime

from apps.advanced_growth.models import (
    Affiliate, AffiliateAttribution, AffiliateCommission, Announcement,
    AnnouncementExposure, CampaignAction, Channel, Experiment, ExperimentAssignment,
    ExperimentConversion, ExperimentVariant, TelegramMarketingDelivery)
from apps.advanced_growth.services.growth import (
    assign_variant, deliver_telegram_marketing, eligible_announcements,
    record_affiliate_conversion, record_affiliate_signup,
    record_announcement_exposure, record_conversion, transition_commission)
from apps.campaigns.models import Audience, Campaign
from apps.emailing.models import Template, TemplateVersion
from apps.events.models import record_event
from apps.growth.models import MarketingPreference, Suppression
from apps.payments.models import PaymentIntent
from apps.subscriptions.models import Plan

User = get_user_model()


def make_user(email, marketing=True):
    u = User.objects.create_user(username=email.split("@")[0], email=email, password="pw")
    MarketingPreference.objects.create(user=u, marketing_opt_in=marketing)
    return u


def make_template(name="T", kind="marketing"):
    t = Template.objects.create(name=name, kind=kind)
    return TemplateVersion.objects.create(template=t, version_number=1, editor_mode="html",
                                          subject="s", html="<p>x</p>")


class AffiliateTests(TestCase):
    def setUp(self):
        self.creator = make_user("creator@x.com")
        self.customer = make_user("cust@x.com")
        self.affiliate = Affiliate.objects.create(user=self.creator, code="CREATOR1",
                                                  commission_percent=20)

    def test_signup_attribution_first_touch_wins(self):
        obj, created = record_affiliate_signup(self.affiliate, self.customer)
        self.assertTrue(created)
        obj2, created2 = record_affiliate_signup(self.affiliate, self.customer)
        self.assertFalse(created2)

    def test_self_referral_blocked(self):
        obj, created = record_affiliate_signup(self.affiliate, self.creator)
        self.assertFalse(created)
        self.assertIsNone(obj)

    def test_conversion_creates_idempotent_commission(self):
        record_affiliate_signup(self.affiliate, self.customer)
        plan = Plan.objects.create(name="P", tier="pro", display_order=1)
        intent = PaymentIntent.objects.create(
            user=self.customer, plan=plan, amount=1000, currency="USD",
            status="pending", provider="stripe", provider_reference="cs1")
        comm, created = record_affiliate_conversion(self.affiliate, self.customer, intent)
        self.assertTrue(created)
        self.assertEqual(comm.amount_cents, 200)  # 20% of 1000
        comm2, created2 = record_affiliate_conversion(self.affiliate, self.customer, intent)
        self.assertFalse(created2)
        self.assertEqual(AffiliateCommission.objects.count(), 1)

    def test_commission_never_alters_payment_or_entitlement(self):
        """Financial boundary: commission is accounting only."""
        record_affiliate_signup(self.affiliate, self.customer)
        plan = Plan.objects.create(name="P", tier="pro", display_order=1)
        intent = PaymentIntent.objects.create(
            user=self.customer, plan=plan, amount=1000, currency="USD",
            status="pending", provider="stripe", provider_reference="cs2")
        record_affiliate_conversion(self.affiliate, self.customer, intent)
        intent.refresh_from_db()
        self.assertEqual(intent.amount, 1000)  # unchanged

    def test_commission_transitions(self):
        c = AffiliateCommission.objects.create(
            affiliate=self.affiliate, user=self.customer, amount_cents=200)
        self.assertTrue(transition_commission(c, AffiliateCommission.Status.APPROVED))
        self.assertTrue(transition_commission(c, AffiliateCommission.Status.PAYABLE))
        self.assertTrue(transition_commission(c, AffiliateCommission.Status.PAID))
        self.assertFalse(transition_commission(c, AffiliateCommission.Status.REVERSED))  # PAID is terminal


class ExperimentTests(TestCase):
    def setUp(self):
        self.user = make_user("exp@x.com")
        self.exp = Experiment.objects.create(name="Subject test", key="subj-test")
        self.va = ExperimentVariant.objects.create(experiment=self.exp, key="A",
                                                   name="Control", allocation_percent=50)
        self.vb = ExperimentVariant.objects.create(experiment=self.exp, key="B",
                                                   name="Variant", allocation_percent=50)

    def test_assignment_deterministic_and_stable(self):
        v1, _ = assign_variant(self.exp, self.user)
        v2, _ = assign_variant(self.exp, self.user)
        self.assertEqual(v1.key, v2.key)  # stable
        self.assertEqual(ExperimentAssignment.objects.filter(
            experiment=self.exp, user=self.user).count(), 1)

    def test_assignment_distribution_both_variants_used(self):
        seen = set()
        for i in range(20):
            u = make_user(f"e{i}@x.com")
            v, _ = assign_variant(self.exp, u)
            seen.add(v.key)
        self.assertEqual(seen, {"A", "B"})

    def test_completed_preserves_assignment(self):
        assign_variant(self.exp, self.user)
        self.exp.status = Experiment.Status.COMPLETED
        self.exp.save(update_fields=["status"])
        a = ExperimentAssignment.objects.get(experiment=self.exp, user=self.user)
        self.assertIsNotNone(a.variant)  # historical assignment preserved

    def test_conversion_idempotent(self):
        assignment = ExperimentAssignment.objects.create(
            experiment=self.exp, user=self.user, variant=self.va)
        ev, _ = record_event("purchase.completed", dedupe_key=f"ec:{self.user.id}",
                             user_id=self.user.id)
        record_conversion(assignment, ev)
        record_conversion(assignment, ev)  # duplicate
        self.assertEqual(ExperimentConversion.objects.count(), 1)


class TelegramMarketingTests(TestCase):
    """Telegram marketing is SEPARATE from entitlement/provisioning."""

    def setUp(self):
        self.user = make_user("tg@x.com")
        Channel.objects.get_or_create(kind="telegram", defaults={"name": "Telegram"})
        self.action = CampaignAction.objects.create(
            campaign=Campaign.objects.create(
                name="C", template_version=make_template(),
                audience=Audience.objects.create(name="A", rules={})),
            channel=Channel.objects.get(kind="telegram"))

    def test_marketing_opt_in_sends(self):
        d, status = deliver_telegram_marketing(self.action, self.user, "Hello")
        self.assertEqual(status, "ok")
        self.assertEqual(d.state, TelegramMarketingDelivery.State.SENT)

    def test_opt_out_suppressed(self):
        self.user.marketing_preference.marketing_opt_in = False
        self.user.marketing_preference.save(update_fields=["marketing_opt_in"])
        d, status = deliver_telegram_marketing(self.action, self.user, "Hello")
        self.assertEqual(status, "suppressed")
        self.assertIsNone(d)

    def test_duplicate_send_idempotent(self):
        deliver_telegram_marketing(self.action, self.user, "Hello")
        d2, status = deliver_telegram_marketing(self.action, self.user, "Hello")
        self.assertEqual(status, "duplicate")
        self.assertEqual(TelegramMarketingDelivery.objects.count(), 1)

    def test_no_entitlement_mutation(self):
        """Marketing NEVER grants/revokes membership (that stays in bot_integration)."""
        from apps.bot_integration.models import UserChannelAssignment
        deliver_telegram_marketing(self.action, self.user, "Hello")
        self.assertEqual(UserChannelAssignment.objects.filter(user=self.user).count(), 0)


class AnnouncementTests(TestCase):
    def setUp(self):
        self.user = make_user("ann@x.com")

    def _ann(self, **kw):
        d = dict(title="New feature", body="Check it out", kind="banner",
                 max_shows_per_user=2, active=True)
        d.update(kw)
        return Announcement.objects.create(**d)

    def test_eligible_when_active_and_in_window(self):
        self._ann()
        self.assertEqual(len(eligible_announcements(self.user)), 1)

    def test_not_shown_after_end(self):
        self._ann(ends_at=timezone.now() - datetime.timedelta(days=1))
        self.assertEqual(len(eligible_announcements(self.user)), 0)

    def test_frequency_cap(self):
        a = self._ann(max_shows_per_user=2)
        record_announcement_exposure(a, self.user, "v1")
        record_announcement_exposure(a, self.user, "v2")
        self.assertEqual(len(eligible_announcements(self.user)), 0)  # cap reached

    def test_under_cap_still_shown(self):
        a = self._ann(max_shows_per_user=3)
        record_announcement_exposure(a, self.user, "v1")
        self.assertEqual(len(eligible_announcements(self.user)), 1)

    def test_dismissal_recorded(self):
        a = self._ann()
        obj, created = record_announcement_exposure(a, self.user, "v1", dismissed=True)
        self.assertTrue(created)
        self.assertTrue(obj.dismissed)

    def test_exposure_idempotent_per_key(self):
        a = self._ann()
        record_announcement_exposure(a, self.user, "v1")
        obj, created = record_announcement_exposure(a, self.user, "v1")
        self.assertFalse(created)
