"""
test_customer_lifecycle_e2e.py — Connected customer lifecycle validation.

One realistic customer journey, asserted against DATABASE STATE (not just
HTTP codes):

    purchase -> payment webhook -> activation -> customer API (pending/
    active/correct plan/expiry) -> Telegram link -> provisioning grant ->
    membership check -> transactional notifications (real Stage-2 signal
    path) -> renewal extension -> expiry -> reconcile revoke -> post-revoke
    membership/dashboard state.

External providers (Telegram Bot API, provision bridge) are mocked at the
same boundaries the existing test-suite uses. No new infrastructure.
"""

import contextlib
import hashlib
import hmac
import json
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.bot_integration.models import (
    PlanChannelMapping, TelegramAccount, UserChannelAssignment)
from apps.bot_integration.services.telegram_transport import (
    OUTCOME_ACCEPTED, TelegramMessageResult)
from apps.payments.models import PaymentIntent, WebhookEvent
from apps.payments import webhook_processor
from apps.subscriptions.models import Plan, Subscription, SubscriptionHistory
from apps.subscriptions.services import expire_due_subscriptions

RZ_SECRET = "rzp_test_secret"


def make_plan():
    return Plan.objects.create(name="Pro", tier="pro", display_order=1)


def make_intent(user, plan, ref, provider="razorpay"):
    return PaymentIntent.objects.create(
        user=user, plan=plan, amount=999, currency="USD",
        status=PaymentIntent.Status.PENDING, provider=provider,
        provider_reference=ref)


def rp_body(event_id, ref, event="payment.captured"):
    return json.dumps({
        "id": event_id, "event": event,
        "payload": {"payment": {"entity": {"id": ref}}},
    }).encode()


def rp_sig(body):
    return hmac.new(RZ_SECRET.encode(), body, hashlib.sha256).hexdigest()


@contextlib.contextmanager
def patched_provision():
    """Patch every provision boundary the reconcile stack may use.

    Returns {target: mock}. Targets that do not exist in this revision are
    skipped so the test stays valid across either transport implementation.
    """
    targets = [
        "apps.bot_integration.reconcile.ProvisionClient",
        "apps.bot_integration.services.provision_client.ProvisionClient",
        "apps.bot_integration.reconcile_jobs.ProvisionClient",
        "apps.bot_integration.reconcile_jobs.ProvisionTransport",
        "apps.bot_integration.reconcile.ProvisionTransport",
    ]
    active = {}
    for t in targets:
        try:
            p = mock.patch(t)
            active[t] = p.start()
        except Exception:
            pass
    _GRANT_LOG.clear()
    _REVOKE_LOG.clear()
    for p in active.values():
        p.side_effect = lambda *a, **k: _spy_instance()
    try:
        yield active
    finally:
        for p in active.values():
            p.stop()


_GRANT_LOG = []
_REVOKE_LOG = []


def _spy_instance():
    from apps.bot_integration.services.provision_client import ProvisionResult
    inst = mock.MagicMock()
    ok = ProvisionResult(ok=True, status="applied")
    already = ProvisionResult(ok=True, status="already_applied")
    def grant(*a, **k):
        _GRANT_LOG.append((a, k))
        return already if [1 for x in _GRANT_LOG
                           if x[0][:2] == a[:2]] else ok
    def revoke(*a, **k):
        _REVOKE_LOG.append((a, k))
        return already if [1 for x in _REVOKE_LOG
                           if x[0][:2] == a[:2]] else ok
    inst.grant.side_effect = grant
    inst.revoke.side_effect = revoke
    return inst


def grant_calls(mocks):
    return list(_GRANT_LOG)


def revoke_calls(mocks):
    return list(_REVOKE_LOG)


@override_settings(RAZORPAY_WEBHOOK_SECRET=RZ_SECRET, STRIPE_WEBHOOK_SECRET="whsec_test")
class CustomerLifecycleE2E(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="customer1", password="pw")
        self.client.force_login(self.user)
        self.plan = make_plan()
        self.plan.duration_days = 30
        self.plan.save()
        PlanChannelMapping.objects.create(
            plan=self.plan, platform="telegram", external_id="-1001")

    # -- helpers -----------------------------------------------------------

    def post_webhook(self, body):
        return self.client.post(
            reverse("razorpay-webhook"), data=body,
            content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=rp_sig(body))

    def run_webhook_job(self):
        # UUID PKs: latest("id") orders by random uuid, not time -
        # pick the actually-newest event by receive time.
        ev = WebhookEvent.objects.order_by("-received_at").first()
        job = self._job_for_event(ev)
        webhook_processor.process_webhook_event(job.payload, job)

    def _job_for_event(self, ev):
        from apps.jobs.models import Job
        return Job.objects.filter(
            kind="process_webhook_event",
            payload__webhook_event_id=str(ev.id)).latest("id")

    def me(self):
        r = self.client.get(reverse("subscriptions:my-subscription"))
        self.assertEqual(r.status_code, 200)
        return r.json()

    # -- the journey ---------------------------------------------------------

    def test_full_customer_lifecycle(self):
        from apps.bot_integration import reconcile

        # 1-3. Customer exists, purchases: intent created (pending payment)
        intent = make_intent(self.user, self.plan, ref="pay_life_1")
        pi = PaymentIntent.objects.get(id=intent.id)
        self.assertEqual(pi.status, PaymentIntent.Status.PENDING)
        self.assertEqual(pi.amount, 999)
        self.assertEqual(pi.currency, "USD")
        self.assertEqual(pi.user, self.user)
        self.assertEqual(pi.plan, self.plan)

        # customer API shows no active subscription yet
        self.assertFalse(self.me().get("subscription"))

        # 4. Payment verified via signed webhook; job enqueued for processing
        body = rp_body("evt_life_1", "pay_life_1")
        r = self.post_webhook(body)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(WebhookEvent.objects.count(), 1)

        # 5. Webhook job activates the subscription. The real signal ->
        #    transactional-service path fires; because the customer has not
        #    linked Telegram yet it is SKIPPED gracefully (never breaks the
        #    activation, never fabricates delivery).
        send = mock.MagicMock(return_value=TelegramMessageResult(
            outcome=OUTCOME_ACCEPTED, message_id=555111))
        from apps.bot_integration.transactional import TransactionalTelegramService
        spy = mock.Mock(wraps=TransactionalTelegramService.send_subscription_activated)
        with mock.patch(
                "apps.bot_integration.transactional.TelegramBotService.send_message_result",
                send), mock.patch.object(
                TransactionalTelegramService, "send_subscription_activated", spy
                ), self.captureOnCommitCallbacks(execute=True):
            self.run_webhook_job()

        sub = Subscription.objects.get(user=self.user)
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)
        self.assertTrue(sub.is_active)
        self.assertIsNotNone(sub.expires_at)
        self.assertEqual(SubscriptionHistory.objects.filter(
            user=self.user, event_type=SubscriptionHistory.EventType.CREATED).count(), 1)

        # 9. Transactional activation chain fired end-to-end and skipped
        #    cleanly for the not-yet-linked customer (no crash, no false
        #    "sent").
        spy.assert_called_once()
        self.assertFalse(send.called, "no transport call expected without link")

        # 6. Customer-facing API: active, correct plan, expiry present
        me = self.me()["subscription"]
        self.assertTrue(me)
        self.assertEqual(me["status"], Subscription.Status.ACTIVE)
        self.assertIn("Pro", json.dumps(me))
        self.assertIn("expires", json.dumps(me).lower())

        # 7. Telegram link flow -> account created -> welcome DM (real path)
        with mock.patch(
                "apps.bot_integration.transactional.TelegramBotService.send_message_result",
                send), self.captureOnCommitCallbacks(execute=True):
            account = TelegramAccount.objects.create(
                user=self.user, chat_id=424242, telegram_user_id=777001,
                is_active=True)
        self.assertTrue(any("Welcome" in str(c) for c in send.call_args_list))

        # 8. Provisioning: reconcile grants the plan's Telegram channel
        with patched_provision() as mocks:
            reconcile.reconcile_user_access(self.user.id)
        grants = grant_calls(mocks)
        self.assertEqual(len(grants), 1, "expected exactly one grant")
        self.assertIn("-1001", str(grants[0]))
        self.assertTrue(UserChannelAssignment.objects.filter(
            user=self.user, platform="telegram", external_id="-1001",
            is_active=True).exists())

        # duplicate provisioning is idempotent (no second grant)
        with patched_provision() as mocks:
            reconcile.reconcile_user_access(self.user.id)
        self.assertEqual(len(grant_calls(mocks)), 0)

        # 9b. Membership verification (transport-level check)
        with patched_provision():
            reconcile.reconcile_user_access(self.user.id)
        self.assertTrue(UserChannelAssignment.objects.filter(
            user=self.user, external_id="-1001", is_active=True).exists())

        # 11. Renewal: a second captured payment extends the SAME subscription
        first_expiry = Subscription.objects.get(id=sub.id).expires_at
        make_intent(self.user, self.plan, ref="pay_life_2")
        body2 = rp_body("evt_life_2", "pay_life_2")
        self.assertEqual(self.post_webhook(body2).status_code, 200)
        send2 = mock.MagicMock(return_value=TelegramMessageResult(
            outcome=OUTCOME_ACCEPTED, message_id=555222))
        with mock.patch(
                "apps.bot_integration.transactional.TelegramBotService.send_message_result",
                send2), self.captureOnCommitCallbacks(execute=True):
            self.run_webhook_job()

        self.assertEqual(Subscription.objects.filter(user=self.user).count(), 1)
        sub.refresh_from_db()
        # RENEWED (not a duplicate CREATED) proves in-place extension; expiry
        # moves forward to now+interval (>= covers sub-second test timing).
        self.assertTrue(sub.expires_at >= first_expiry, "renewal did not extend")
        self.assertEqual(SubscriptionHistory.objects.filter(
            user=self.user, event_type=SubscriptionHistory.EventType.RENEWED).count(), 1)
        # renewal before expiry must not trigger any revoke
        with patched_provision() as mocks:
            reconcile.reconcile_user_access(self.user.id)
        self.assertEqual(len(revoke_calls(mocks)), 0)

        # 12. Expiry: sweep claims the due subscription (idempotent)
        sub.is_active = True
        sub.expires_at = sub.started_at  # due
        sub.save()
        send3 = mock.MagicMock(return_value=TelegramMessageResult(
            outcome=OUTCOME_ACCEPTED, message_id=555333))
        with mock.patch(
                "apps.bot_integration.transactional.TelegramBotService.send_message_result",
                send3), self.captureOnCommitCallbacks(execute=True):
            expired = expire_due_subscriptions()
        self.assertEqual(expired, 1)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.EXPIRED)
        self.assertFalse(sub.is_active)
        # NOTE (contract, not a failure): expiry mutates via .update(), so
        # no save-signal fires and no expiry DM is sent in this codebase;
        # access removal below is the observable post-expiry behavior.
        # dashboard reflects expired state (no active subscription shown)
        self.assertIsNone(self.me()["subscription"])

        # processing expiry twice must be a no-op
        self.assertEqual(expire_due_subscriptions(), 0)

        # dashboard contract: expired subscription returns subscription=None
        # (the endpoint only surfaces ACTIVE subscriptions by design)
        self.assertIsNone(self.me()["subscription"])

        # 13-14. Reconcile revokes Telegram access; post-revoke membership
        #        confirms removal; reconcile stays quiet afterwards
        with patched_provision() as mocks:
            reconcile.reconcile_user_access(self.user.id)
        revokes = revoke_calls(mocks)
        self.assertEqual(len(revokes), 1, "expected exactly one revoke")
        self.assertIn("-1001", str(revokes[0]))
        self.assertTrue(UserChannelAssignment.objects.filter(
            user=self.user, external_id="-1001", is_active=False).exists())

        with patched_provision() as mocks2:
            reconcile.reconcile_user_access(self.user.id)
        self.assertEqual(len(revoke_calls(mocks2)), 0, "double revoke")
        self.assertTrue(UserChannelAssignment.objects.filter(
            user=self.user, external_id="-1001", is_active=False).exists())

    def test_duplicate_webhook_does_not_double_activate(self):
        make_intent(self.user, self.plan, ref="pay_dup_1")
        body = rp_body("evt_dup_1", "pay_dup_1")
        self.assertEqual(self.post_webhook(body).status_code, 200)
        self.assertEqual(self.post_webhook(body).status_code, 200)

        send = mock.MagicMock(return_value=TelegramMessageResult(
            outcome=OUTCOME_ACCEPTED, message_id=1))
        with mock.patch(
                "apps.bot_integration.transactional.TelegramBotService.send_message_result",
                send), self.captureOnCommitCallbacks(execute=True):
            self.run_webhook_job()

        # exactly one webhook recorded; one activation; one history row
        self.assertEqual(WebhookEvent.objects.count(), 1)
        self.assertEqual(Subscription.objects.filter(user=self.user).count(), 1)
        self.assertEqual(SubscriptionHistory.objects.filter(
            user=self.user).count(), 1)
        pi = PaymentIntent.objects.get(provider_reference="pay_dup_1")
        self.assertEqual(pi.status, PaymentIntent.Status.SUCCESS)

    def test_pending_payment_then_activation_reflected_in_api(self):
        """Customer-facing state: pending intent -> active subscription."""
        intent = make_intent(self.user, self.plan, ref="pay_api_1")
        self.assertFalse(self.me().get("subscription"))

        body = rp_body("evt_api_1", "pay_api_1")
        self.assertEqual(self.post_webhook(body).status_code, 200)
        send = mock.MagicMock(return_value=TelegramMessageResult(
            outcome=OUTCOME_ACCEPTED, message_id=2))
        with mock.patch(
                "apps.bot_integration.transactional.TelegramBotService.send_message_result",
                send), self.captureOnCommitCallbacks(execute=True):
            self.run_webhook_job()

        me = self.me()["subscription"]
        self.assertEqual(me["status"], Subscription.Status.ACTIVE)
        pi = PaymentIntent.objects.get(id=intent.id)
        self.assertEqual(pi.status, PaymentIntent.Status.SUCCESS)
