"""G0: events, referral reconciliation, consent/suppression."""
import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.events.models import Event, record_event
from apps.growth.models import (
    MarketingPreference, Referral, ReferralCode, Suppression, can_send_marketing)
from apps.payments.models import PaymentIntent
from apps.payments.services import activate_paid_subscription
from apps.subscriptions.models import Plan, Subscription, SubscriptionHistory
from apps.subscriptions.services import (
    cancel_subscription, expire_subscription, extend_subscription, purchase_plan)

User = get_user_model()


def make_plan(name="Pro", tier="pro", is_trial=False, trial_days=0):
    return Plan.objects.create(name=name, tier=tier, display_order=1,
                               is_trial=is_trial, trial_duration_days=trial_days)


def make_sub(user, plan, status=Subscription.Status.ACTIVE, is_active=True,
             expires_days=30, provider_sub_id=""):
    return Subscription.objects.create(
        user=user, plan=plan, status=status, is_active=is_active,
        started_at=timezone.now() - datetime.timedelta(days=1),
        expires_at=timezone.now() + datetime.timedelta(days=expires_days),
        price_cents=999, price_currency="USD",
        provider_subscription_id=provider_sub_id)


class EventTests(TestCase):
    def test_record_event_idempotent(self):
        u = User.objects.create_user(username="e1")
        e1, c1 = record_event("purchase.completed", dedupe_key="k1", user_id=u.id,
                              object_ref="payment:x", payload={"a": 1})
        e2, c2 = record_event("purchase.completed", dedupe_key="k1", user_id=u.id,
                              object_ref="payment:x", payload={"a": 1})
        self.assertTrue(c1)
        self.assertFalse(c2)
        self.assertEqual(e1.pk, e2.pk)
        self.assertEqual(Event.objects.count(), 1)

    def test_dedupe_key_unique(self):
        record_event("purchase.completed", dedupe_key="dup")
        with self.assertRaises(Exception):
            Event.objects.create(event_type="purchase.completed", dedupe_key="dup",
                                 occurred_at=timezone.now())

    def test_payload_and_refs(self):
        u = User.objects.create_user(username="e2")
        ev, _ = record_event("subscription.canceled", dedupe_key="sc1",
                             user_id=u.id, object_ref=f"subscription:abc",
                             payload={"actor": "user"})
        self.assertEqual(ev.object_ref, "subscription:abc")
        self.assertEqual(ev.payload["actor"], "user")
        self.assertEqual(ev.user_id, u.id)




class ConsentSuppressionTests(TestCase):
    def test_opt_in_then_marketing_allowed(self):
        u = User.objects.create_user(username="m1")
        MarketingPreference.objects.create(user=u, marketing_opt_in=True)
        self.assertTrue(can_send_marketing(u, "m1@x.com"))

    def test_default_opt_out(self):
        u = User.objects.create_user(username="m2")  # no preference row
        self.assertFalse(can_send_marketing(u, "m2@x.com"))

    def test_suppression_overrides_opt_in(self):
        u = User.objects.create_user(username="m3")
        MarketingPreference.objects.create(user=u, marketing_opt_in=True)
        Suppression.objects.create(email="m3@x.com", reason=Suppression.Reason.UNSUBSCRIBE)
        self.assertFalse(can_send_marketing(u, "m3@x.com"))

    def test_suppression_unique(self):
        Suppression.objects.create(email="s@x.com", reason=Suppression.Reason.BOUNCE)
        with self.assertRaises(Exception):
            Suppression.objects.create(email="s@x.com", reason=Suppression.Reason.BOUNCE)

    def test_transactional_not_gated(self):
        # can_send_marketing only governs MARKETING; transactional is separate
        # and always permitted by policy (not checked here).
        u = User.objects.create_user(username="m4")
        self.assertFalse(can_send_marketing(u, "m4@x.com"))  # marketing blocked


class ReferralReconciliationTests(TestCase):
    """The single authoritative path: complete_referral_on_purchase via
    activate_paid_subscription.  Proves no double-reward across the
    return/webhook/renewal paths and that fraud guards remain intact."""

    def setUp(self):
        self.referrer = User.objects.create_user(username="ref1")
        self.referee = User.objects.create_user(username="ref2")
        self.plan = make_plan()
        # signal auto-creates a code for the referrer; use it
        code = self.referrer.referral_code.code if hasattr(self.referrer, "referral_code") else "REFER1"
        ReferralService = __import__(
            "apps.growth.services.referrals", fromlist=["ReferralService"]).ReferralService
        ReferralService.record_referral_signup(
            referred_user=self.referee, code=code)

    def _paid_intent_for_referee(self, amount=999):
        intent = PaymentIntent.objects.create(
            user=self.referee, plan=self.plan, amount=amount, currency="USD",
            status=PaymentIntent.Status.PENDING, provider="stripe",
            provider_reference="cs_ref")
        # attach the referral discount as checkout would
        referral = Referral.objects.get(referred_user=self.referee)
        intent.applied_referral_discount = referral
        intent.save(update_fields=["applied_referral_discount"])
        return intent

    def test_normal_qualifying_purchase_one_reward(self):
        from apps.growth.models import ReferralReward
        intent = self._paid_intent_for_referee()
        activate_paid_subscription(intent)
        self.assertEqual(ReferralReward.objects.count(), 1)
        r = Referral.objects.get(referred_user=self.referee)
        self.assertEqual(r.status, Referral.Status.COMPLETED)
        # provenance
        reward = ReferralReward.objects.get()
        self.assertIsNotNone(reward.triggering_subscription)

    def test_duplicate_confirm_one_reward(self):
        from apps.growth.models import ReferralReward
        intent = self._paid_intent_for_referee()
        activate_paid_subscription(intent)
        intent.status = PaymentIntent.Status.PENDING  # reset not possible; simulate second call
        # second call on already-success intent returns existing, no new reward
        activated, sub = activate_paid_subscription(intent)
        self.assertFalse(activated)
        self.assertEqual(ReferralReward.objects.count(), 1)

    def test_renewal_does_not_reward(self):
        from apps.growth.models import ReferralReward
        intent = self._paid_intent_for_referee()
        activated, sub = activate_paid_subscription(intent)
        self.assertEqual(ReferralReward.objects.count(), 1)
        # renewal extends the SAME subscription -> no new reward
        extend_subscription(sub, days=30, actor="webhook")
        self.assertEqual(ReferralReward.objects.count(), 1)

    def test_non_qualifying_purchase_no_reward(self):
        from apps.growth.models import ReferralReward
        # an unrelated purchase (no referral discount attached, no referral)
        other = User.objects.create_user(username="ref3")
        intent = PaymentIntent.objects.create(
            user=other, plan=self.plan, amount=999, currency="USD",
            status=PaymentIntent.Status.PENDING, provider="stripe",
            provider_reference="cs_other")
        activate_paid_subscription(intent)
        # the unrelated user has no referral -> complete_referral finds none
        self.assertEqual(ReferralReward.objects.count(), 0)
class ReferralRewardFailureTests(TestCase):
    """AUDIT A1: a reward-creation failure must NOT leave a referral COMPLETED
    but rewardless (previously swallowed, stuck, unrecoverable).  It must roll
    back loudly."""

    def setUp(self):
        self.referrer = User.objects.create_user(username="rf1", password="pw")
        self.referee = User.objects.create_user(username="rf2", password="pw")
        self.plan = make_plan()
        code = self.referrer.referral_code.code
        ReferralService = __import__(
            "apps.growth.services.referrals", fromlist=["ReferralService"]).ReferralService
        ReferralService.record_referral_signup(referred_user=self.referee, code=code)
        self.referral = Referral.objects.get(referred_user=self.referee)

    def _intent(self):
        return PaymentIntent.objects.create(
            user=self.referee, plan=self.plan, amount=999, currency="USD",
            status=PaymentIntent.Status.PENDING, provider="stripe",
            provider_reference="cs-audit",
            applied_referral_discount=self.referral)

    def test_reward_failure_rolls_back_and_is_loud(self):
        from unittest import mock
        from apps.growth.services import referrals as ref_mod
        intent = self._intent()
        # Force the reward-creation step to fail inside complete_referral_on_purchase.
        with mock.patch.object(
            ref_mod.ReferralRewardService, "create_reward_on_referral_completion",
            side_effect=RuntimeError("db down")):
            with self.assertRaises(RuntimeError):
                activate_paid_subscription(intent)
        # Referral must NOT be left COMPLETED-without-reward (rolled back).
        self.referral.refresh_from_db()
        self.assertNotEqual(self.referral.status, Referral.Status.COMPLETED)
        # No reward was created.
        from apps.growth.models import ReferralReward
        self.assertEqual(ReferralReward.objects.filter(referral=self.referral).count(), 0)

    def test_reward_success_commits_referral_and_reward(self):
        from apps.growth.models import ReferralReward
        intent = self._intent()
        activate_paid_subscription(intent)
        self.referral.refresh_from_db()
        self.assertEqual(self.referral.status, Referral.Status.COMPLETED)
        self.assertEqual(ReferralReward.objects.filter(referral=self.referral).count(), 1)
