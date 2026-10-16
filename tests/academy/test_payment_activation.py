from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.academy.models import (
    Course, CourseEntitlement, CourseEntitlementEvent, CourseModule, CourseOffer, Lesson,
)
from apps.payments.models import PaymentIntent
from apps.payments.services import activate_paid_subscription, apply_refund_policy
from apps.subscriptions.models import Plan, PlanPrice, Subscription

User = get_user_model()


class CoursePaymentActivationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="course-buyer", password="Test-password-123!"
        )
        self.plan = Plan.objects.create(
            name="Trading Path One-Time", tier=Plan.Tier.BASIC, is_active=True, is_hidden=True,
        )
        self.price = PlanPrice.objects.create(
            plan=self.plan, interval=PlanPrice.Interval.ONE_TIME,
            price_cents=99900, currency="INR", is_active=True,
        )
        self.course = Course.objects.create(
            title="Trading Path", slug="trading-path",
            status=Course.Status.PUBLISHED,
        )
        self.module = CourseModule.objects.create(
            course=self.course, title="Foundations", slug="foundations",
            position=1, is_published=True,
        )
        self.lesson = Lesson.objects.create(
            module=self.module, title="Read the screen", slug="read-the-screen",
            position=1, release_day=1, is_published=True,
            content_markdown="Paid lesson content",
        )
        self.offer = CourseOffer.objects.create(
            course=self.course, name="Trading Path", slug="trading-path",
            plan=self.plan, access_duration_days=None, is_active=True,
        )
        self.intent = PaymentIntent.objects.create(
            user=self.user, plan=self.plan, plan_price=self.price,
            amount=99900, base_amount_cents=99900, currency="INR",
            provider=PaymentIntent.Provider.RAZORPAY,
            status=PaymentIntent.Status.PENDING,
        )

    def test_hidden_plan_without_course_offer_cannot_be_purchased(self):
        hidden_plan = Plan.objects.create(
            name="Internal-only plan", tier=Plan.Tier.PRO, is_active=True, is_hidden=True,
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("payment-start"),
            {"plan_id": str(hidden_plan.pk), "interval": "monthly"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(PaymentIntent.objects.filter(user=self.user, plan=hidden_plan).exists())

    def test_active_course_offer_requires_hidden_commerce_plan(self):
        self.plan.is_hidden = False
        self.plan.save(update_fields=["is_hidden"])
        with self.assertRaises(ValidationError):
            self.offer.full_clean()

    def test_verified_one_time_payment_grants_permanent_course_access_once(self):
        activated, entitlement = activate_paid_subscription(self.intent)

        self.assertTrue(activated)
        self.assertIsInstance(entitlement, CourseEntitlement)
        self.assertIsNone(entitlement.expires_at)
        self.assertTrue(entitlement.has_access)
        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, PaymentIntent.Status.SUCCESS)
        self.assertFalse(Subscription.objects.filter(user=self.user, plan=self.plan).exists())
        self.assertEqual(
            CourseEntitlementEvent.objects.filter(
                entitlement=entitlement,
                event_type=CourseEntitlementEvent.EventType.GRANTED,
            ).count(),
            1,
        )

        # Customer return and webhook may race or be retried. The second call
        # must not create a second grant or alter the lifetime expiry.
        activated_again, entitlement_again = activate_paid_subscription(self.intent)
        self.assertFalse(activated_again)
        self.assertEqual(entitlement_again.pk, entitlement.pk)
        self.assertEqual(
            CourseEntitlement.objects.filter(user=self.user, course=self.course).count(),
            1,
        )
        self.assertEqual(CourseEntitlementEvent.objects.filter(entitlement=entitlement).count(), 1)

    def test_full_refund_revokes_only_the_current_course_purchase(self):
        _activated, entitlement = activate_paid_subscription(self.intent)
        PaymentIntent.objects.filter(pk=self.intent.pk).update(refunded_cents=self.intent.amount)

        result = apply_refund_policy(self.intent)
        self.assertTrue(result)
        entitlement.refresh_from_db()
        self.assertEqual(entitlement.status, CourseEntitlement.Status.REVOKED)
        self.assertFalse(entitlement.has_access)

        # A duplicate captured-payment event after the refund must not reinstate
        # the same original entitlement.
        activated_again, entitlement_again = activate_paid_subscription(self.intent)
        self.assertFalse(activated_again)
        entitlement_again.refresh_from_db()
        self.assertEqual(entitlement_again.status, CourseEntitlement.Status.REVOKED)
        self.assertFalse(entitlement_again.has_access)

    def test_one_time_plan_without_course_offer_cannot_become_subscription(self):
        orphan_plan = Plan.objects.create(
            name="Unmapped one-time plan", tier=Plan.Tier.PRO, is_active=True,
        )
        orphan_price = PlanPrice.objects.create(
            plan=orphan_plan, interval=PlanPrice.Interval.ONE_TIME,
            price_cents=50000, currency="INR", is_active=True,
        )
        orphan_intent = PaymentIntent.objects.create(
            user=self.user, plan=orphan_plan, plan_price=orphan_price,
            amount=50000, currency="INR",
            provider=PaymentIntent.Provider.RAZORPAY,
            status=PaymentIntent.Status.PENDING,
        )
        with self.assertRaises(ValueError):
            activate_paid_subscription(orphan_intent)
        orphan_intent.refresh_from_db()
        self.assertEqual(orphan_intent.status, PaymentIntent.Status.PENDING)
        self.assertFalse(Subscription.objects.filter(user=self.user, plan=orphan_plan).exists())
