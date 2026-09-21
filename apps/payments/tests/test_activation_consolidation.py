"""Activation-path consolidation tests (A-K)."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.payments import providers
from apps.payments.models import PaymentIntent
from apps.payments.services import activate_paid_subscription
from apps.subscriptions.models import Plan, PlanPrice, Subscription, SubscriptionHistory
from apps.events.models import Event
from apps.jobs.models import Job

User = get_user_model()


def make_plan(interval="monthly", price_cents=999, currency="USD"):
    plan = Plan.objects.create(
        name="Pro-%s" % interval, tier="pro-%s" % interval,
        description="d", display_order=1, is_active=True)
    pp = PlanPrice.objects.create(
        plan=plan, interval=interval, price_cents=price_cents, currency=currency)
    return plan, pp


def make_intent(user, plan, ref="cs_test_1", provider="stripe", amount=999,
                base_amount_cents=999, coupon_discount_cents=0,
                applied_coupon_code="", applied_referral_discount=None):
    return PaymentIntent.objects.create(
        user=user, plan=plan, amount=amount, currency="USD",
        status=PaymentIntent.Status.PENDING, provider=provider,
        provider_reference=ref, base_amount_cents=base_amount_cents,
        coupon_discount_cents=coupon_discount_cents,
        applied_coupon_code=applied_coupon_code,
        applied_referral_discount=applied_referral_discount,
        country="US")


@mock.patch("apps.payments.views.providers")
@mock.patch("apps.payments.views.get_pricing_country", return_value="US")
class ActivationConsolidationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="buyer", password="pw")
        self.client.force_authenticate(self.user)

    def _start(self, plan_id, interval):
        return self.client.post(reverse("payment-start"), {
            "plan_id": str(plan_id), "interval": interval}, format="json")

    def _confirm(self, intent_id):
        return self.client.post(reverse("payment-confirm"), {
            "payment_intent_id": str(intent_id)}, format="json")

    def _confirm_page(self, intent_id):
        return self.client.get(reverse("confirm-page", args=[intent_id]))

    # A. Successful browser confirmation creates exactly one Subscription.
    def test_browser_confirm_creates_one_subscription(self, _geo, prov):
        plan, _ = make_plan("monthly")
        prov.create_hosted_checkout.return_value = providers.CreatedCheckout(
            provider_reference="cs_a", checkout_url="https://pay.example")
        prov.verify_provider_payment.return_value = True
        self._start(plan.id, "monthly")
        intent = PaymentIntent.objects.get()
        r = self._confirm(intent.id)
        self.assertEqual(r.data["status"], "success")
        self.assertEqual(Subscription.objects.count(), 1)
        self.assertEqual(SubscriptionHistory.objects.count(), 1)
        self.assertTrue(Event.objects.filter(
            event_type=Event.Type.PURCHASE_COMPLETED).exists())

    # B. Successful webhook processing creates exactly one Subscription.
    def test_service_activation_creates_one_subscription(self, _geo, prov):
        plan, _ = make_plan("monthly")
        intent = make_intent(self.user, plan, ref="cs_b")
        prov.verify_provider_payment.return_value = True
        activated, sub = activate_paid_subscription(intent)
        self.assertTrue(activated)
        self.assertIsNotNone(sub)
        self.assertEqual(Subscription.objects.count(), 1)
        self.assertEqual(SubscriptionHistory.objects.count(), 1)
        self.assertTrue(Event.objects.filter(
            event_type=Event.Type.PURCHASE_COMPLETED).exists())

    # C. Browser confirmation repeated twice creates exactly one Subscription.
    def test_repeated_browser_confirm_one_subscription(self, _geo, prov):
        plan, _ = make_plan("monthly")
        prov.create_hosted_checkout.return_value = providers.CreatedCheckout(
            provider_reference="cs_c", checkout_url="https://pay.example")
        prov.verify_provider_payment.return_value = True
        self._start(plan.id, "monthly")
        intent = PaymentIntent.objects.get()
        first = self._confirm(intent.id)
        second = self._confirm(intent.id)
        self.assertEqual(first.data["status"], "success")
        self.assertEqual(second.data["status"], "success")
        self.assertTrue(second.data.get("already_processed"))
        self.assertEqual(Subscription.objects.count(), 1)
        self.assertEqual(SubscriptionHistory.objects.count(), 1)
        self.assertEqual(Event.objects.filter(
            event_type=Event.Type.PURCHASE_COMPLETED).count(), 1)

    # D. Browser confirm + webhook for same PaymentIntent -> one Subscription.
    def test_browser_then_webhook_no_duplicate(self, _geo, prov):
        plan, _ = make_plan("monthly")
        prov.create_hosted_checkout.return_value = providers.CreatedCheckout(
            provider_reference="cs_d", checkout_url="https://pay.example")
        prov.verify_provider_payment.return_value = True
        self._start(plan.id, "monthly")
        intent = PaymentIntent.objects.get()
        self._confirm(intent.id)
        activated, sub = activate_paid_subscription(intent)
        self.assertFalse(activated)
        self.assertIsNotNone(sub)
        self.assertEqual(Subscription.objects.count(), 1)
        self.assertEqual(SubscriptionHistory.objects.count(), 1)

    # E. Duplicate webhook delivery remains idempotent.
    def test_duplicate_webhook_delivery_idempotent(self, _geo, prov):
        plan, _ = make_plan("monthly")
        intent = make_intent(self.user, plan, ref="cs_e")
        prov.verify_provider_payment.return_value = True
        activated1, sub1 = activate_paid_subscription(intent)
        self.assertTrue(activated1)
        activated2, sub2 = activate_paid_subscription(intent)
        self.assertFalse(activated2)
        self.assertEqual(sub1.pk, sub2.pk)
        self.assertEqual(Subscription.objects.count(), 1)
        self.assertEqual(SubscriptionHistory.objects.count(), 1)
        self.assertEqual(Event.objects.filter(
            event_type=Event.Type.PURCHASE_COMPLETED).count(), 1)

    # F. Referral completion behavior identical regardless of path.
    def test_referral_completion_identical_paths(self, _geo, prov):
        from apps.growth.models import Referral, ReferralReward
        referrer = User.objects.create_user(username="ref", password="pw")
        plan, _ = make_plan("monthly")
        referral = Referral.objects.create(
            referrer=referrer, referred_user=self.user,
            status=Referral.Status.PENDING)
        prov.create_hosted_checkout.return_value = providers.CreatedCheckout(
            provider_reference="cs_f", checkout_url="https://pay.example")
        prov.verify_provider_payment.return_value = True
        self._start(plan.id, "monthly")
        intent = PaymentIntent.objects.get()
        self._confirm(intent.id)
        referral.refresh_from_db()
        self.assertEqual(referral.status, Referral.Status.COMPLETED)
        self.assertTrue(ReferralReward.objects.filter(referral=referral).exists())
        # service path with a second purchaser
        user2 = User.objects.create_user(username="buyer2", password="pw")
        referral2 = Referral.objects.create(
            referrer=referrer, referred_user=user2, status=Referral.Status.PENDING)
        intent2 = make_intent(user2, plan, ref="cs_f2")
        activate_paid_subscription(intent2)
        referral2.refresh_from_db()
        self.assertEqual(referral2.status, Referral.Status.COMPLETED)
        self.assertTrue(ReferralReward.objects.filter(referral=referral2).exists())

    # G. Reward-credit behavior identical regardless of path.
    def test_reward_credit_identical_paths(self, _geo, prov):
        from apps.growth.models import Referral, ReferralReward
        plan, _ = make_plan("monthly", price_cents=1000)
        unlocked = timezone.now() - timedelta(days=1)

        def give_credit(purchaser, tag):
            # Reward credit belongs to the REFERRER.  For the purchaser to
            # have consumable credit, the purchaser must have referred someone
            # else whose referral completed and whose reward is CREDITED.
            referee = User.objects.create_user(
                username="referee-%s" % tag, password="pw")
            referral = Referral.objects.create(
                referrer=purchaser, referred_user=referee,
                status=Referral.Status.COMPLETED)
            return ReferralReward.objects.create(
                referral=referral, referrer=purchaser, amount_cents=500,
                currency="USD", referred_purchase_amount_cents=1000,
                reward_percentage=50,
                status=ReferralReward.Status.CREDITED,
                unlocked_at=unlocked)

        give_credit(self.user, "browser")
        prov.create_hosted_checkout.return_value = providers.CreatedCheckout(
            provider_reference="cs_g", checkout_url="https://pay.example")
        prov.verify_provider_payment.return_value = True
        self._start(plan.id, "monthly")
        intent = PaymentIntent.objects.get()
        self._confirm(intent.id)
        sub_browser = Subscription.objects.get(user=self.user)
        self.assertGreater(
            (sub_browser.expires_at - sub_browser.started_at).days, 30)
        # credit must actually be consumed, not silently skipped
        self.assertEqual(
            ReferralReward.objects.get(referrer=self.user).status,
            ReferralReward.Status.USED)

        user2 = User.objects.create_user(username="buyer3", password="pw")
        give_credit(user2, "service")
        intent2 = make_intent(user2, plan, ref="cs_g2", amount=1000,
                              base_amount_cents=1000)
        activate_paid_subscription(intent2)
        sub_service = Subscription.objects.get(user=user2)
        self.assertGreater(
            (sub_service.expires_at - sub_service.started_at).days, 30)
        # identical extension on both paths (tolerate sub-second clock skew)
        self.assertAlmostEqual(
            (sub_browser.expires_at - sub_browser.started_at).total_seconds(),
            (sub_service.expires_at - sub_service.started_at).total_seconds(),
            delta=2)

    # H. Coupon redemption is finalized exactly once.
    def test_coupon_finalized_exactly_once(self, _geo, prov):
        from apps.promotions.models import Coupon, CouponRedemption
        plan, _ = make_plan("monthly", price_cents=1000)
        coupon = Coupon.objects.create(
            code="TEST10", name="Test 10",
            discount_type=Coupon.DiscountType.FIXED,
            amount_off_cents=100, active=True)
        prov.create_hosted_checkout.return_value = providers.CreatedCheckout(
            provider_reference="cs_h", checkout_url="https://pay.example")
        prov.verify_provider_payment.return_value = True
        self._start(plan.id, "monthly")
        intent = PaymentIntent.objects.get()
        intent.applied_coupon_code = "TEST10"
        intent.coupon_discount_cents = 100
        intent.base_amount_cents = 1000
        intent.save()
        self._confirm(intent.id)
        self.assertEqual(CouponRedemption.objects.count(), 1)
        self.assertTrue(CouponRedemption.objects.get().finalized)
        self._confirm(intent.id)
        self.assertEqual(CouponRedemption.objects.count(), 1)

    # I. purchase.completed recorded exactly once.
    def test_purchase_completed_event_exactly_once(self, _geo, prov):
        plan, _ = make_plan("monthly")
        intent = make_intent(self.user, plan, ref="cs_i")
        prov.verify_provider_payment.return_value = True
        activate_paid_subscription(intent)
        self.assertEqual(Event.objects.filter(
            event_type=Event.Type.PURCHASE_COMPLETED).count(), 1)
        activate_paid_subscription(intent)
        self.assertEqual(Event.objects.filter(
            event_type=Event.Type.PURCHASE_COMPLETED).count(), 1)

    # J. GET confirmation cannot leave SUCCESS intent without Subscription.
    def test_get_confirm_activates_subscription(self, _geo, prov):
        plan, _ = make_plan("monthly")
        prov.create_hosted_checkout.return_value = providers.CreatedCheckout(
            provider_reference="cs_j", checkout_url="https://pay.example")
        prov.verify_provider_payment.return_value = True
        self._start(plan.id, "monthly")
        intent = PaymentIntent.objects.get()
        r = self._confirm_page(intent.id)
        self.assertEqual(r.status_code, 200)
        intent.refresh_from_db()
        self.assertEqual(intent.status, PaymentIntent.Status.SUCCESS)
        self.assertEqual(Subscription.objects.count(), 1)
        self._confirm_page(intent.id)
        self.assertEqual(Subscription.objects.count(), 1)

    # K. Telegram/access side effects identical between paths.
    def test_access_side_effects_parity(self, _geo, prov):
        plan, _ = make_plan("monthly")
        prov.create_hosted_checkout.return_value = providers.CreatedCheckout(
            provider_reference="cs_k1", checkout_url="https://pay.example")
        prov.verify_provider_payment.return_value = True
        self._start(plan.id, "monthly")
        intent1 = PaymentIntent.objects.get()
        self._confirm(intent1.id)
        jobs_after_browser = Job.objects.count()
        user2 = User.objects.create_user(username="buyer4", password="pw")
        intent2 = make_intent(user2, plan, ref="cs_k2")
        activate_paid_subscription(intent2)
        jobs_after_service = Job.objects.count()
        self.assertEqual(jobs_after_browser, jobs_after_service)
