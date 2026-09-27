"""P1: hosted-checkout purchase flow (providers mocked; no live calls).

NOTE (chargeback implementation): the flow is now CONSENT-FIRST.
payment_start only creates the PaymentIntent and returns consent_url.
The provider session is created on the consent step (POST agree), which
records the write-once CheckoutEvidence BEFORE any money moves. These
tests exercise exactly that sequence.
"""
import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.payments import providers
from apps.payments.models import PaymentIntent
from apps.subscriptions.models import (
    Plan, PlanPrice, Subscription, SubscriptionHistory)

User = get_user_model()


def make_plan(interval="monthly", price_cents=999, currency="USD"):
    plan = Plan.objects.create(
        name=f"Pro-{interval}", tier=f"pro-{interval}",
        description="d", display_order=1, is_active=True)
    pp = PlanPrice.objects.create(
        plan=plan, interval=interval, price_cents=price_cents, currency=currency)
    return plan, pp


@mock.patch("apps.payments.views.providers")
@mock.patch("apps.payments.views.get_pricing_country", return_value="US")
class PurchaseFlowTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="buyer", password="pw")
        self.client.force_authenticate(self.user)

    def _start(self, plan_id, interval):
        return self.client.post(reverse("payment-start"), {
            "plan_id": str(plan_id), "interval": interval}, format="json")

    def _consent(self, intent):
        """The agreement moment: ONE checkbox -> evidence -> provider handoff."""
        return self.client.post(
            reverse("checkout-consent", args=[intent.id]), {"agree": "on"})

    def _confirm(self, intent_id):
        return self.client.post(reverse("payment-confirm"), {
            "payment_intent_id": str(intent_id)}, format="json")

    def test_monthly_purchase_expires_in_30_days(self, _geo, prov):
        plan, _ = make_plan("monthly")
        prov.create_hosted_checkout.return_value = providers.CreatedCheckout(
            provider_reference="cs_test_123", checkout_url="https://pay.example")
        prov.verify_provider_payment.return_value = True
        r = self._start(plan.id, "monthly")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIn("consent_url", r.data)          # consent-first handoff
        intent = PaymentIntent.objects.get()
        self.assertEqual(intent.provider_reference, "")  # provider NOT called yet
        c = self._consent(intent)
        self.assertEqual(c.status_code, 302)           # -> checkout bridge
        intent.refresh_from_db()
        self.assertEqual(intent.provider_reference, "cs_test_123")
        self.assertEqual(intent.plan_price.interval, "monthly")
        # Evidence captured BEFORE provider handoff:
        self.assertTrue(hasattr(intent, "checkout_evidence"))
        from apps.policies.models import PolicyAcceptance
        self.assertEqual(PolicyAcceptance.objects.filter(
            user=self.user, checkout_evidence__payment_intent=intent
        ).count(), 3)  # terms + refund&cancellation + risk disclosure
        c2 = self._confirm(intent.id)
        self.assertEqual(c2.data["status"], "success")
        sub = Subscription.objects.get(user=self.user)
        self.assertEqual((sub.expires_at - sub.started_at).days, 30)
        self.assertEqual(sub.price_cents, intent.amount)
        self.assertEqual(sub.payment_provider, intent.provider)
        self.assertTrue(SubscriptionHistory.objects.filter(
            subscription=sub, event_type=SubscriptionHistory.EventType.CREATED).exists())

    def test_quarterly_purchase_expires_in_90_days(self, _geo, prov):
        plan, _ = make_plan("quarterly", 2499)
        prov.create_hosted_checkout.return_value = providers.CreatedCheckout(
            provider_reference="cs_q", checkout_url="https://pay.example")
        prov.verify_provider_payment.return_value = True
        self._start(plan.id, "quarterly")
        intent = PaymentIntent.objects.get()
        self.assertEqual(intent.amount, 2499)
        self._consent(intent)
        c = self._confirm(intent.id)
        self.assertEqual(c.data["status"], "success")
        self.assertEqual((Subscription.objects.get().expires_at
                          - Subscription.objects.get().started_at).days, 90)

    def test_yearly_purchase_expires_in_365_days(self, _geo, prov):
        plan, _ = make_plan("yearly", 7999)
        prov.create_hosted_checkout.return_value = providers.CreatedCheckout(
            provider_reference="cs_y", checkout_url="https://pay.example")
        prov.verify_provider_payment.return_value = True
        self._start(plan.id, "yearly")
        intent = PaymentIntent.objects.get()
        self._consent(intent)
        c = self._confirm(intent.id)
        self.assertEqual(c.data["status"], "success")
        self.assertEqual((Subscription.objects.get().expires_at
                          - Subscription.objects.get().started_at).days, 365)

    def test_stripe_routing_and_snapshot_amount(self, _geo, prov):
        plan, pp = make_plan("monthly", 999, "USD")
        prov.create_hosted_checkout.return_value = providers.CreatedCheckout(
            provider_reference="cs_us", checkout_url="https://pay.example")
        self._start(plan.id, "monthly")
        intent = PaymentIntent.objects.get()
        self.assertEqual(intent.provider, "stripe")
        self._consent(intent)
        kwargs = prov.create_hosted_checkout.call_args
        self.assertEqual(kwargs[0][0].amount, pp.price_cents)
        self.assertEqual(kwargs[0][0].currency, "USD")

    def test_razorpay_routing_for_india(self, geo, prov):
        geo.return_value = "IN"
        plan, _ = make_plan("monthly", 99900, "INR")
        prov.create_hosted_checkout.return_value = providers.CreatedCheckout(
            provider_reference="order_in", checkout_url=None)
        self._start(plan.id, "monthly")
        intent = PaymentIntent.objects.get()
        self.assertEqual(intent.provider, "razorpay")
        self.assertEqual(intent.provider_reference, "")
        self._consent(intent)
        intent.refresh_from_db()
        self.assertEqual(intent.provider_reference, "order_in")

    def test_failed_verification_does_not_activate(self, _geo, prov):
        plan, _ = make_plan()
        prov.create_hosted_checkout.return_value = providers.CreatedCheckout(
            provider_reference="cs_f", checkout_url="https://pay.example")
        prov.verify_provider_payment.return_value = False
        self._start(plan.id, "monthly")
        intent = PaymentIntent.objects.get()
        self._consent(intent)
        c = self._confirm(intent.id)
        self.assertEqual(c.data["status"], "failed")
        self.assertFalse(Subscription.objects.exists())
        self.assertEqual(PaymentIntent.objects.get().status,
                         PaymentIntent.Status.FAILED)

    def test_pending_verification_does_not_activate(self, _geo, prov):
        plan, _ = make_plan()
        prov.create_hosted_checkout.return_value = providers.CreatedCheckout(
            provider_reference="cs_p", checkout_url="https://pay.example")
        prov.verify_provider_payment.return_value = None
        self._start(plan.id, "monthly")
        intent = PaymentIntent.objects.get()
        self._consent(intent)
        c = self._confirm(intent.id)
        self.assertEqual(c.data["status"], "pending")
        self.assertFalse(Subscription.objects.exists())
        self.assertEqual(PaymentIntent.objects.get().status,
                         PaymentIntent.Status.PENDING)

    def test_repeated_confirmation_creates_one_subscription(self, _geo, prov):
        plan, _ = make_plan()
        prov.create_hosted_checkout.return_value = providers.CreatedCheckout(
            provider_reference="cs_r", checkout_url="https://pay.example")
        prov.verify_provider_payment.return_value = True
        self._start(plan.id, "monthly")
        intent = PaymentIntent.objects.get()
        self._consent(intent)
        intent_id = intent.id
        first = self._confirm(intent_id)
        second = self._confirm(intent_id)
        self.assertEqual(first.data["status"], "success")
        self.assertEqual(second.data["status"], "success")
        self.assertTrue(second.data.get("already_processed"))
        self.assertEqual(Subscription.objects.count(), 1)
        self.assertEqual(SubscriptionHistory.objects.count(), 1)

    def test_checkout_page_renders(self, _geo, prov):
        plan, _ = make_plan()
        prov.create_hosted_checkout.return_value = providers.CreatedCheckout(
            provider_reference="cs_c", checkout_url="https://pay.example")
        prov.get_stripe_checkout_url.return_value = "https://pay.example"
        self._start(plan.id, "monthly")
        intent = PaymentIntent.objects.get()
        # Evidence-first: the bridge redirects to consent until the agreement
        # moment is captured.
        r = self.client.get(reverse("checkout-page", args=[intent.id]))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("checkout-consent", args=[intent.id]),
                      r["Location"])
        self._consent(intent)
        r = self.client.get(reverse("checkout-page", args=[intent.id]))
        self.assertEqual(r.status_code, 200)

    def test_confirm_page_success(self, _geo, prov):
        plan, _ = make_plan()
        prov.create_hosted_checkout.return_value = providers.CreatedCheckout(
            provider_reference="cs_cp", checkout_url="https://pay.example")
        prov.verify_provider_payment.return_value = True
        self._start(plan.id, "monthly")
        intent = PaymentIntent.objects.get()
        r = self.client.get(reverse("confirm-page", args=[intent.id]))
        self.assertEqual(r.status_code, 200)
        intent.refresh_from_db()
        self.assertEqual(intent.status, PaymentIntent.Status.SUCCESS)

    def test_provider_error_at_consent_keeps_intent_and_shows_error(self, _geo, prov):
        """Provider failure now happens on the consent step, not at start.
        The intent stays PENDING (retryable) and the user sees the error."""
        plan, _ = make_plan()
        from apps.payments import providers as real_providers
        prov.ProviderError = real_providers.ProviderError
        prov.create_hosted_checkout.side_effect = real_providers.ProviderError("down")
        r = self._start(plan.id, "monthly")
        self.assertEqual(r.status_code, 200, r.data)   # start itself succeeds
        intent = PaymentIntent.objects.get()
        c = self._consent(intent)
        self.assertEqual(c.status_code, 200)           # consent re-rendered w/ error
        self.assertIn(b"Payment provider error", c.content)
        intent.refresh_from_db()
        self.assertEqual(intent.status, PaymentIntent.Status.PENDING)
        # Retry succeeds once provider recovers:
        prov.create_hosted_checkout.side_effect = None
        prov.create_hosted_checkout.return_value = providers.CreatedCheckout(
            provider_reference="cs_retry", checkout_url="https://pay.example")
        c2 = self._consent(intent)
        self.assertEqual(c2.status_code, 302)
        intent.refresh_from_db()
        self.assertEqual(intent.provider_reference, "cs_retry")


@mock.patch("apps.subscriptions.views.get_pricing_country", return_value="US")
@mock.patch("apps.subscriptions.services.get_pricing_country", return_value="US")
class PurchaseBypassTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="bp", password="pw")
        self.client.force_authenticate(self.user)

    def test_paid_purchase_routes_to_payment_flow(self, _sgeo, _vgeo):
        plan, _ = make_plan("monthly")
        r = self.client.post(reverse("subscriptions:purchase-plan"), {"plan_id": str(plan.id)},
                             format="json")
        self.assertEqual(r.status_code, 402)
        self.assertTrue(r.data["requires_payment"])
        self.assertEqual(r.data["payment_start_url"], reverse("payment-start"))
        self.assertFalse(Subscription.objects.exists())

    def test_trial_purchase_still_activates(self, _sgeo, _vgeo):
        plan = Plan.objects.create(
            name="Trial-bypass", tier="trial-bypass", is_trial=True,
            trial_duration_days=7,
            description="t", display_order=2, is_active=True)
        from apps.subscriptions.models import GeoPlanPrice
        GeoPlanPrice.objects.create(
            plan=plan, country="US", price_cents=0, currency="USD",
            is_active=True, interval="monthly")
        r = self.client.post(reverse("subscriptions:purchase-plan"), {"plan_id": str(plan.id)},
                             format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(Subscription.objects.filter(user=self.user, is_trial=True).exists())

    def test_paid_direct_service_call_raises(self, _sgeo, _vgeo):
        from apps.subscriptions.services import purchase_plan
        from django.core.exceptions import PermissionDenied
        plan, _ = make_plan("monthly")
        with self.assertRaises(PermissionDenied):
            purchase_plan(self.user, plan)
        self.assertFalse(Subscription.objects.exists())


@mock.patch("apps.payments.views.providers")
@mock.patch("apps.payments.views.get_pricing_country", return_value="US")
class CtaFlowTests(APITestCase):
    """Landing CTA: plan + selected interval must reach payment_start."""

    def setUp(self):
        self.user = User.objects.create_user(username="cta", password="pw")
        self.client.force_authenticate(self.user)

    def test_get_start_carries_plan_and_interval(self, _geo, prov):
        plan, _ = make_plan("quarterly")
        prov.create_hosted_checkout.return_value = providers.CreatedCheckout(
            provider_reference="cs_cta", checkout_url="https://pay.example")
        r = self.client.get(reverse("payment-start"),
                            {"plan_id": str(plan.id), "interval": "quarterly"})
        self.assertEqual(r.status_code, 200, r.data)
        intent = PaymentIntent.objects.get()
        self.assertEqual(intent.plan_price.interval, "quarterly")
        self.assertEqual(intent.provider_reference, "")  # consent-first
        self.client.post(reverse("checkout-consent", args=[intent.id]),
                         {"agree": "on"})
        intent.refresh_from_db()
        self.assertEqual(intent.provider_reference, "cs_cta")

    def test_get_start_defaults_to_monthly(self, _geo, prov):
        plan, _ = make_plan("monthly")
        prov.create_hosted_checkout.return_value = providers.CreatedCheckout(
            provider_reference="cs_m", checkout_url="https://pay.example")
        r = self.client.get(reverse("payment-start"), {"plan_id": str(plan.id)})
        self.assertEqual(r.status_code, 200, r.data)
        intent = PaymentIntent.objects.get()
        self.assertEqual(intent.plan_price.interval, "monthly")
        self.client.post(reverse("checkout-consent", args=[intent.id]),
                         {"agree": "on"})
        intent.refresh_from_db()
        self.assertEqual(intent.provider_reference, "cs_m")

    def test_get_start_without_plan_id_is_400(self, _geo, prov):
        r = self.client.get(reverse("payment-start"))
        self.assertEqual(r.status_code, 400)
