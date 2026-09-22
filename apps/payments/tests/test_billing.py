"""Billing validation: customer payment history, refunds, chargebacks,
notifications, and admin operations.

Covers the minimum-billing behavior added on top of the existing payments
infrastructure. Uses the same conventions as test_payments_api.py and
test_webhooks.py (Django TestCase, reverse(), direct WebhookEvent + processor
invocation for provider events, mock.patch on the job enqueue boundary).
"""
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.events.models import Event
from apps.payments.models import PaymentIntent, Refund, WebhookEvent
from apps.payments.services import (
    apply_refund_policy,
    confirm_chargeback,
    record_dispute_opened,
    record_dispute_won,
    record_refund,
)
from apps.payments.webhook_processor import process_webhook_event
from apps.subscriptions.models import Plan, Subscription, SubscriptionHistory

User = get_user_model()


def make_job():
    return SimpleNamespace(mark_failed=lambda **kw: None)


def make_event(provider, event_id, event_type, payload):
    return WebhookEvent.objects.create(
        provider=provider, provider_event_id=event_id,
        event_type=event_type, payload=payload,
        status=WebhookEvent.Status.RECEIVED)


def run_event(event):
    process_webhook_event({"webhook_event_id": str(event.pk)}, make_job())
    event.refresh_from_db()


class BillingTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="billing_user", email="billing@example.com", password="x")
        self.other = User.objects.create_user(
            username="other_user", email="other@example.com", password="x")
        self.plan = Plan.objects.create(
            name="Pro", tier="pro", is_active=True, display_order=1)
        self.plan_price = self.plan.prices.create(
            price_cents=1000, currency="USD", interval="monthly", is_active=True)

    def make_intent(self, user=None, status=PaymentIntent.Status.SUCCESS, **kw):
        defaults = dict(
            user=user or self.user, plan=self.plan, plan_price=self.plan_price,
            base_amount_cents=1000, amount=1000, currency="USD",
            provider=PaymentIntent.Provider.STRIPE, status=status, country="US",
            provider_reference="cs_test_1", provider_payment_id="pi_1")
        defaults.update(kw)
        return PaymentIntent.objects.create(**defaults)

    def make_subscription(self, user=None):
        return Subscription.objects.create(
            user=user or self.user, plan=self.plan, plan_price=self.plan_price,
            status=Subscription.Status.ACTIVE, is_active=True,
            started_at=timezone.now(),
            expires_at=timezone.now() + timezone.timedelta(days=30),
            price_cents=1000, price_currency="USD")

    def canceled_history_count(self):
        return SubscriptionHistory.objects.filter(
            user=self.user, event_type=SubscriptionHistory.EventType.CANCELED
        ).count()


class PaymentHistoryTests(BillingTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_own_history_visible(self):
        self.make_intent()
        r = self.client.get(reverse("payment-history"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["payments"]), 1)

    def test_other_users_payments_not_visible(self):
        self.make_intent()
        self.make_intent(user=self.other, provider_reference="cs_other")
        r = self.client.get(reverse("payment-history"))
        ids = [p["provider_reference"] for p in r.data["payments"]]
        self.assertEqual(ids, ["cs_test_1"])
        self.assertNotIn("cs_other", ids)

    def test_success_and_failed_display(self):
        self.make_intent(status=PaymentIntent.Status.SUCCESS)
        self.make_intent(status=PaymentIntent.Status.FAILED,
                         provider_reference="cs_fail")
        r = self.client.get(reverse("payment-history"))
        statuses = {p["provider_reference"]: p["status"]
                    for p in r.data["payments"]}
        self.assertEqual(statuses["cs_test_1"], "success")
        self.assertEqual(statuses["cs_fail"], "failed")

    def test_subscription_derived_safely(self):
        self.make_intent()
        sub = self.make_subscription()
        r = self.client.get(reverse("payment-history"))
        block = r.data["payments"][0]["subscription"]
        self.assertIsNotNone(block)
        self.assertEqual(block["id"], str(sub.id))
        self.assertEqual(block["status"], "active")

    def test_requires_authentication(self):
        anon = APIClient()
        r = anon.get(reverse("payment-history"))
        self.assertIn(r.status_code, (401, 403))


class RefundTests(BillingTestCase):
    def test_full_refund_cancels_and_keeps_original_record(self):
        self.make_intent()
        sub = self.make_subscription()
        intent = PaymentIntent.objects.get()
        record_refund(intent, provider="stripe", provider_refund_id="rfd_1",
                      amount_cents=400, currency="USD")
        record_refund(intent, provider="stripe", provider_refund_id="rfd_2",
                      amount_cents=600, currency="USD")
        apply_refund_policy(intent)  # policy runs after recording (webhook/admin parity)
        intent.refresh_from_db()
        # Original payment record intact.
        self.assertEqual(intent.status, PaymentIntent.Status.SUCCESS)
        self.assertEqual(intent.amount, 1000)
        self.assertEqual(intent.base_amount_cents, 1000)
        # Refund state correct.
        self.assertEqual(intent.refunded_cents, 1000)
        self.assertTrue(intent.is_fully_refunded)
        self.assertFalse(intent.is_partially_refunded)
        # Entitlement revoked through the existing cancel flow.
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.CANCELED)
        self.assertFalse(sub.is_active)
        self.assertEqual(self.canceled_history_count(), 1)
        self.assertTrue(Event.objects.filter(
            event_type="payment.refunded").exists())

    def test_partial_refund_keeps_access(self):
        self.make_intent()
        sub = self.make_subscription()
        intent = PaymentIntent.objects.get()
        record_refund(intent, provider="stripe", provider_refund_id="rfd_1",
                      amount_cents=400, currency="USD")
        intent.refresh_from_db()
        self.assertTrue(intent.is_partially_refunded)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)
        self.assertEqual(self.canceled_history_count(), 0)

    def test_record_refund_idempotent_per_refund_id(self):
        self.make_intent()
        intent = PaymentIntent.objects.get()
        _, created1 = record_refund(
            intent, provider="stripe", provider_refund_id="rfd_1",
            amount_cents=400, currency="USD")
        _, created2 = record_refund(
            intent, provider="stripe", provider_refund_id="rfd_1",
            amount_cents=400, currency="USD")
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(Refund.objects.count(), 1)
        intent.refresh_from_db()
        self.assertEqual(intent.refunded_cents, 400)  # not doubled

    def test_full_refund_policy_uses_cancel_service(self):
        self.make_intent()
        self.make_subscription()
        intent = PaymentIntent.objects.get()
        with mock.patch("apps.jobs.enqueue.enqueue_reconcile") as enq:
            record_refund(intent, provider="stripe",
                          provider_refund_id="rfd_full",
                          amount_cents=1000, currency="USD")
            apply_refund_policy(intent)
        enq.assert_called_once()  # access reconcile enqueued exactly once

    def test_razorpay_refund_webhook_idempotent(self):
        intent = self.make_intent(
            provider=PaymentIntent.Provider.RAZORPAY,
            provider_reference="order_1", provider_payment_id="pay_1")
        payload = {"refund": {"entity": {
            "id": "rf_1", "amount": 400, "currency": "INR",
            "payment_id": "pay_1", "created_at": 1700000000}},
            "payment": {"entity": {"id": "pay_1", "order_id": "order_1"}}}
        e1 = make_event("razorpay", "evt_rf_1", "refund.processed", payload)
        run_event(e1)
        self.assertEqual(e1.status, WebhookEvent.Status.PROCESSED)
        self.assertEqual(Refund.objects.count(), 1)
        # Reprocess same event (durable-job retry) — no duplicate refund.
        run_event(e1)
        self.assertEqual(Refund.objects.count(), 1)
        intent.refresh_from_db()
        self.assertEqual(intent.refunded_cents, 400)
        # A second delivery with a new event id but same refund id is also a no-op.
        e2 = make_event("razorpay", "evt_rf_2", "refund.processed", payload)
        run_event(e2)
        self.assertEqual(Refund.objects.count(), 1)

    def test_stripe_refund_webhook_dedupes_refund_list(self):
        self.make_intent()
        self.make_subscription()
        def charge_refunded_evt(evid, refunds):
            return {"id": evid, "type": "charge.refunded", "data": {"object": {
                "id": "ch_1", "payment_intent": "pi_1", "currency": "usd",
                "amount_refunded": sum(r["amount"] for r in refunds),
                "refunds": {"data": refunds}}}}
        rfd = lambda rid, amt: {"id": rid, "amount": amt, "created": 1700000000}
        run_event(make_event("stripe", "evt_c1", "charge.refunded",
                             charge_refunded_evt("evt_c1", [rfd("rfd_1", 400)])))
        run_event(make_event("stripe", "evt_c2", "charge.refunded",
                             charge_refunded_evt("evt_c2", [rfd("rfd_1", 400),
                                                            rfd("rfd_2", 600)])))
        self.assertEqual(Refund.objects.count(), 2)
        intent = PaymentIntent.objects.get()
        self.assertEqual(intent.refunded_cents, 1000)
        self.assertTrue(intent.is_fully_refunded)
        sub = Subscription.objects.get(user=self.user)
        self.assertEqual(sub.status, Subscription.Status.CANCELED)


class ChargebackTests(BillingTestCase):
    DISPUTE = {"id": "dp_1", "charge": "ch_1", "payment_intent": "pi_1",
               "amount": 1000, "currency": "usd", "status": "lost"}

    def dispute_event(self, evid, etype, status):
        return make_event("stripe", evid, etype, {
            "id": evid, "type": etype,
            "data": {"object": dict(self.DISPUTE, status=status)}})

    def test_dispute_opened_does_not_cancel(self):
        self.make_intent()
        sub = self.make_subscription()
        with mock.patch("apps.jobs.enqueue.enqueue_reconcile") as enq:
            record_dispute_opened(PaymentIntent.objects.get(),
                                  dispute_reference="dp_1")
        intent = PaymentIntent.objects.get()
        self.assertTrue(intent.chargeback)
        self.assertFalse(intent.chargeback_confirmed)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)
        enq.assert_not_called()
        self.assertTrue(AuditLog.objects.filter(action="payment.disputed").exists())

    def test_confirmed_cancels_revokes_notifies_once(self):
        self.make_intent()
        self.make_subscription()
        with mock.patch("apps.jobs.enqueue.enqueue_reconcile") as enq, \
             mock.patch("apps.notifications.services.NotificationService.send_email") as send_email, \
             mock.patch("apps.bot_integration.transactional."
                        "TransactionalTelegramService.send_text") as tg:
            run_event(self.dispute_event("evt_cb1",
                                         "charge.dispute.funds_withdrawn", "lost"))
        intent = PaymentIntent.objects.get()
        self.assertTrue(intent.chargeback_confirmed)
        self.assertEqual(intent.chargeback_reference, "dp_1")
        sub = Subscription.objects.get(user=self.user)
        self.assertEqual(sub.status, Subscription.Status.CANCELED)
        self.assertFalse(sub.is_active)
        self.assertEqual(self.canceled_history_count(), 1)
        enq.assert_called_once()               # Telegram/Discord reconcile triggered
        send_email.assert_called_once()        # email via existing infra
        tg.assert_called_once()                # telegram via existing infra
        self.assertIn("revoked", tg.call_args[0][1])
        self.assertTrue(AuditLog.objects.filter(action="payment.chargeback").exists())
        # Duplicate processing of the same event: no duplicates anywhere.
        with mock.patch("apps.notifications.services.NotificationService.send_email") as se2, \
             mock.patch("apps.bot_integration.transactional."
                        "TransactionalTelegramService.send_text") as tg2:
            run_event(WebhookEvent.objects.get(provider_event_id="evt_cb1"))
        self.assertEqual(self.canceled_history_count(), 1)
        se2.assert_not_called()
        tg2.assert_not_called()

    def test_dispute_won_clears_without_cancel(self):
        self.make_intent()
        self.make_subscription()
        record_dispute_opened(PaymentIntent.objects.get(),
                              dispute_reference="dp_1")
        record_dispute_won(PaymentIntent.objects.get(),
                           dispute_reference="dp_1")
        intent = PaymentIntent.objects.get()
        self.assertFalse(intent.chargeback)
        self.assertFalse(intent.chargeback_confirmed)
        self.assertEqual(Subscription.objects.get(user=self.user).status,
                         Subscription.Status.ACTIVE)
        self.assertEqual(self.canceled_history_count(), 0)

    def test_confirm_chargeback_idempotent_claim(self):
        self.make_intent()
        self.make_subscription()
        intent = PaymentIntent.objects.get()
        first = confirm_chargeback(intent, dispute_reference="dp_1")
        second = confirm_chargeback(intent, dispute_reference="dp_1")
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(self.canceled_history_count(), 1)


class FailureNotificationTests(BillingTestCase):
    def test_failure_notifies_once(self):
        intent = self.make_intent(status=PaymentIntent.Status.PENDING)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        with mock.patch("apps.payments.providers.verify_provider_payment",
                        return_value=False), \
             mock.patch("apps.notifications.services.NotificationService.send_email") as se, \
             mock.patch("apps.bot_integration.transactional."
                        "TransactionalTelegramService.send_text") as tg:
            with self.captureOnCommitCallbacks(execute=True):
                r = self.client.post(reverse("payment-confirm"),
                                     {"payment_intent_id": str(intent.pk)})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.data["status"], "failed")
            se.assert_called_once()
            tg.assert_called_once()
            # Retry: claim fails (already FAILED) -> no duplicate notification.
            with self.captureOnCommitCallbacks(execute=True):
                r2 = self.client.post(reverse("payment-confirm"),
                                      {"payment_intent_id": str(intent.pk)})
            self.assertEqual(r2.status_code, 200)
            self.assertEqual(r2.data["status"], "failed")
            self.assertEqual(se.call_count, 1)
            self.assertEqual(tg.call_count, 1)
        intent.refresh_from_db()
        self.assertEqual(intent.status, PaymentIntent.Status.FAILED)


class AdminTests(BillingTestCase):
    def setUp(self):
        super().setUp()
        from django.contrib.admin.sites import AdminSite
        from apps.payments.admin import PaymentIntentAdmin
        self.admin = PaymentIntentAdmin(PaymentIntent, AdminSite())
        self.request = RequestFactory().get("/admin/payments/paymentintent/")
        self.admin_user = User.objects.create_superuser(
            username="admin", email="a@a.com", password="x")
        self.request.user = self.admin_user

    def test_search_includes_provider_references(self):
        for f in ("provider_reference", "provider_payment_id",
                  "chargeback_reference"):
            self.assertIn(f, self.admin.search_fields)

    def test_original_fields_readonly(self):
        for f in ("amount", "status", "base_amount_cents", "currency"):
            self.assertIn(f, self.admin.readonly_fields)

    def test_manual_chargeback_action_safe_and_audited(self):
        self.make_intent()
        self.make_subscription()
        intent = PaymentIntent.objects.get()
        with mock.patch.object(self.admin, "message_user"):
            self.admin.mark_charged_back(self.request, [intent])
        intent.refresh_from_db()
        self.assertTrue(intent.chargeback_confirmed)
        self.assertTrue(intent.chargeback_reference.startswith("manual:"))
        self.assertEqual(Subscription.objects.get(user=self.user).status,
                         Subscription.Status.CANCELED)
        self.assertTrue(AuditLog.objects.filter(
            action="payment.chargeback",
            metadata__admin_id=str(self.admin_user.pk)).exists())
        # Idempotent: second run cancels nothing new.
        with mock.patch.object(self.admin, "message_user"):
            self.admin.mark_charged_back(self.request, [intent])
        self.assertEqual(self.canceled_history_count(), 1)

    def test_manual_refund_action_records_and_audits(self):
        self.make_intent()
        self.make_subscription()
        intent = PaymentIntent.objects.get()
        with mock.patch.object(self.admin, "message_user"):
            self.admin.record_manual_refund(self.request, [intent])
        self.assertEqual(Refund.objects.count(), 1)
        refund = Refund.objects.get()
        self.assertEqual(refund.source, "manual")
        intent.refresh_from_db()
        self.assertEqual(intent.refunded_cents, 1000)
        self.assertTrue(intent.is_fully_refunded)
        self.assertEqual(Subscription.objects.get(user=self.user).status,
                         Subscription.Status.CANCELED)
        self.assertTrue(AuditLog.objects.filter(
            action="payment.refund.manual").exists())
        # Idempotent: nothing left to refund -> no second row.
        with mock.patch.object(self.admin, "message_user"):
            self.admin.record_manual_refund(self.request, [intent])
        self.assertEqual(Refund.objects.count(), 1)
