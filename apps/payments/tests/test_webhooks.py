"""P4/P5: webhook signature, idempotency, shared lifecycle, renewal."""
import hashlib
import hmac
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.jobs.models import Job
from apps.payments.models import PaymentIntent, WebhookEvent
from apps.subscriptions.models import Plan, Subscription, SubscriptionHistory

User = get_user_model()
RZ_SECRET = "rzp_test_secret"


def _rz_sig(body: bytes) -> str:
    return hmac.new(RZ_SECRET.encode(), body, hashlib.sha256).hexdigest()


def make_plan():
    return Plan.objects.create(name="Pro", tier="pro", display_order=1)


def make_intent(user, plan, ref="cs_test_1", provider="razorpay"):
    return PaymentIntent.objects.create(
        user=user, plan=plan, amount=999, currency="USD",
        status=PaymentIntent.Status.PENDING, provider=provider,
        provider_reference=ref)


@override_settings(RAZORPAY_WEBHOOK_SECRET=RZ_SECRET, STRIPE_WEBHOOK_SECRET="whsec_test")
class WebhookSignatureTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="w1", password="pw")
        self.plan = make_plan()
        self.url = reverse("razorpay-webhook")

    def _post(self, body, sig):
        return self.client.post(self.url, data=body, content_type="application/json",
                                HTTP_X_RAZORPAY_SIGNATURE=sig)

    def test_valid_signature_accepted(self):
        body = json.dumps({"id": "evt_1", "event": "payment.captured",
                           "payload": {"payment": {"entity": {"id": "cs_test_1"}}}}).encode()
        r = self._post(body, _rz_sig(body))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(WebhookEvent.objects.count(), 1)

    def test_invalid_signature_rejected_no_event(self):
        body = json.dumps({"id": "evt_2", "event": "payment.captured"}).encode()
        r = self._post(body, "deadbeef")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(WebhookEvent.objects.count(), 0)

    def test_missing_signature_rejected(self):
        body = json.dumps({"id": "evt_3", "event": "payment.captured"}).encode()
        r = self.client.post(self.url, data=body, content_type="application/json")
        self.assertEqual(r.status_code, 400)


@override_settings(RAZORPAY_WEBHOOK_SECRET=RZ_SECRET)
class WebhookIdempotencyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="w2", password="pw")
        self.plan = make_plan()
        self.intent = make_intent(self.user, self.plan)
        self.url = reverse("razorpay-webhook")

    def _post_captured(self, event_id):
        body = json.dumps({
            "id": event_id, "event": "payment.captured",
            "payload": {"payment": {"entity": {"id": self.intent.provider_reference}}},
        }).encode()
        return self.client.post(self.url, data=body, content_type="application/json",
                                HTTP_X_RAZORPAY_SIGNATURE=_rz_sig(body))

    def test_duplicate_delivery_one_event_one_activation(self):
        for _ in range(3):
            r = self._post_captured("evt_dup")
            self.assertEqual(r.status_code, 200)
        # receive is idempotent: a single stored event, a single job
        self.assertEqual(WebhookEvent.objects.count(), 1)
        self.assertEqual(Job.objects.filter(kind="process_webhook_event").count(), 1)
        # process it -> exactly one activation
        from apps.payments.webhook_processor import process_webhook_event
        ev = WebhookEvent.objects.get()
        job = Job.objects.get(kind="process_webhook_event")
        process_webhook_event({"webhook_event_id": str(ev.id)}, job)
        self.assertEqual(Subscription.objects.count(), 1)
        self.assertEqual(SubscriptionHistory.objects.count(), 1)
        # process the SAME stored event again -> still one activation
        process_webhook_event({"webhook_event_id": str(ev.id)}, job)
        self.assertEqual(Subscription.objects.count(), 1)
        self.assertEqual(SubscriptionHistory.objects.count(), 1)

    def test_processing_enqueued_as_durable_job(self):
        self._post_captured("evt_job")
        # receive stores + enqueues; the job processes it
        from apps.payments.webhook_processor import process_webhook_event
        ev = WebhookEvent.objects.get()
        job = Job.objects.create(kind="process_webhook_event",
                                 payload={"webhook_event_id": str(ev.id)},
                                 status=Job.STATUS_RUNNING, attempts=1, max_attempts=3)
        process_webhook_event({"webhook_event_id": str(ev.id)}, job)
        ev.refresh_from_db()
        self.assertEqual(ev.status, WebhookEvent.Status.PROCESSED)
        self.assertEqual(Subscription.objects.count(), 1)


@override_settings(RAZORPAY_WEBHOOK_SECRET=RZ_SECRET)
class ReturnThenWebhookIdempotencyTests(TestCase):
    """Customer return and webhook converge; second arrival is a no-op."""

    def setUp(self):
        self.user = User.objects.create_user(username="w3", password="pw")
        self.plan = make_plan()
        self.intent = make_intent(self.user, self.plan, ref="cs_rt_1")
        self.url = reverse("razorpay-webhook")

    def _post_captured(self, event_id):
        body = json.dumps({
            "id": event_id, "event": "payment.captured",
            "payload": {"payment": {"entity": {"id": self.intent.provider_reference}}},
        }).encode()
        return self.client.post(self.url, data=body, content_type="application/json",
                                HTTP_X_RAZORPAY_SIGNATURE=_rz_sig(body))

    def test_webhook_then_confirm_no_duplicate(self):
        self._post_captured("evt_wh_first")
        # process the webhook's durable job -> activation happens here
        from apps.payments.webhook_processor import process_webhook_event
        from apps.jobs.models import Job
        ev = WebhookEvent.objects.get()
        job = Job.objects.get(kind="process_webhook_event")
        process_webhook_event({"webhook_event_id": str(ev.id)}, job)
        # now the customer-return confirm path hits the shared service
        from apps.payments.services import activate_paid_subscription
        self.intent.refresh_from_db()
        activated, sub = activate_paid_subscription(self.intent)
        self.assertFalse(activated)  # already done by webhook
        self.assertEqual(Subscription.objects.count(), 1)
        self.assertEqual(SubscriptionHistory.objects.count(), 1)


@override_settings(RAZORPAY_WEBHOOK_SECRET=RZ_SECRET)
class RenewalTests(TestCase):
    """P5: subscription.charged extends the existing Subscription."""

    def setUp(self):
        self.user = User.objects.create_user(username="w4", password="pw")
        self.plan = make_plan()
        self.url = reverse("razorpay-webhook")

    def test_renewal_extends_existing_subscription(self):
        import datetime as _dt
        from django.utils import timezone as _tz
        sub = Subscription.objects.create(
            user=self.user, plan=self.plan, status=Subscription.Status.ACTIVE,
            is_active=True, provider_subscription_id="sub_rzp_1",
            started_at=_tz.now() - _dt.timedelta(days=30),
            expires_at=_tz.now() - _dt.timedelta(days=1),  # already due
            price_cents=999, price_currency="USD")
        old_expiry = sub.expires_at
        body = json.dumps({
            "id": "evt_renew", "event": "subscription.charged",
            "payload": {"subscription": {"entity": {"id": "sub_rzp_1"}}},
        }).encode()
        self.client.post(self.url, data=body, content_type="application/json",
                         HTTP_X_RAZORPAY_SIGNATURE=_rz_sig(body))
        from apps.payments.webhook_processor import process_webhook_event
        ev = WebhookEvent.objects.get()
        job = Job.objects.create(kind="process_webhook_event",
                                 payload={"webhook_event_id": str(ev.id)},
                                 status=Job.STATUS_RUNNING, attempts=1, max_attempts=3)
        process_webhook_event({"webhook_event_id": str(ev.id)}, job)
        sub.refresh_from_db()
        self.assertGreater(sub.expires_at, old_expiry)
        # RENEWED history written; still ONE subscription (no duplicate row)
        self.assertEqual(Subscription.objects.count(), 1)
        self.assertTrue(SubscriptionHistory.objects.filter(
            event_type=SubscriptionHistory.EventType.RENEWED).exists())


@override_settings(RAZORPAY_WEBHOOK_SECRET=RZ_SECRET)
class NoCrossActivationTests(TestCase):
    def test_payment_a_does_not_activate_subscription_b(self):
        user_a = User.objects.create_user(username="wa", password="pw")
        user_b = User.objects.create_user(username="wb", password="pw")
        plan = make_plan()
        intent_a = make_intent(user_a, plan, ref="cs_a")
        Subscription.objects.create(
            user=user_b, plan=plan, status=Subscription.Status.PENDING,
            is_active=False, price_cents=999, price_currency="USD")
        # webhook references intent_a's provider_reference -> only user_a activates
        body = json.dumps({
            "id": "evt_x", "event": "payment.captured",
            "payload": {"payment": {"entity": {"id": "cs_a"}}},
        }).encode()
        self.client.post(reverse("razorpay-webhook"), data=body,
                         content_type="application/json",
                         HTTP_X_RAZORPAY_SIGNATURE=_rz_sig(body))
        from apps.payments.webhook_processor import process_webhook_event
        ev = WebhookEvent.objects.get()
        job = Job.objects.create(kind="process_webhook_event",
                                 payload={"webhook_event_id": str(ev.id)},
                                 status=Job.STATUS_RUNNING, attempts=1, max_attempts=3)
        process_webhook_event({"webhook_event_id": str(ev.id)}, job)
        # user_b's pending sub untouched; user_a got the active one
        self.assertEqual(Subscription.objects.filter(is_active=True).count(), 1)
        self.assertTrue(Subscription.objects.filter(user=user_a, is_active=True).exists())
        self.assertFalse(Subscription.objects.filter(user=user_b, is_active=True).exists())
