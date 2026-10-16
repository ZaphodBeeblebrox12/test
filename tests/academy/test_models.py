from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.test import TestCase

from apps.academy.models import (
    Course, CourseEntitlement, CourseEntitlementEvent, CourseModule,
    CourseOffer, Lesson, LessonInstructorMaterial, LessonProgress,
)
from apps.academy.services import (
    can_access_lesson, grant_course_entitlement, lesson_release_at,
    revoke_course_entitlement_for_payment, update_lesson_progress,
)
from apps.payments.models import PaymentIntent
from apps.subscriptions.models import Plan

User = get_user_model()


class AcademyModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="academy-user", password="test-password")
        self.plan = Plan.objects.create(name="Trading Path", tier=Plan.Tier.BASIC)
        self.course = Course.objects.create(title="Trading Path", slug="trading-path", status=Course.Status.PUBLISHED)
        self.module = CourseModule.objects.create(course=self.course, title="Foundations", slug="foundations", position=1, is_published=True)
        self.lesson = Lesson.objects.create(module=self.module, title="Read the screen", slug="read-the-screen", position=1, release_day=1, is_published=True, content_markdown="Paid lesson body")
        self.offer = CourseOffer.objects.create(course=self.course, plan=self.plan, name="Trading Path", slug="trading-path", is_active=False, access_duration_days=None)
        self.payment = PaymentIntent.objects.create(user=self.user, plan=self.plan, amount=99900, currency="INR", provider=PaymentIntent.Provider.RAZORPAY, status=PaymentIntent.Status.SUCCESS)

    def test_permanent_entitlement_has_no_expiry(self):
        entitlement, created = grant_course_entitlement(user=self.user, offer=self.offer, payment_intent=self.payment)
        self.assertTrue(created)
        self.assertIsNone(entitlement.expires_at)
        self.assertTrue(entitlement.has_access)
        self.assertEqual(CourseEntitlementEvent.objects.filter(entitlement=entitlement).count(), 1)

    def test_grant_is_idempotent_for_same_payment(self):
        first, created = grant_course_entitlement(user=self.user, offer=self.offer, payment_intent=self.payment)
        again, created_again = grant_course_entitlement(user=self.user, offer=self.offer, payment_intent=self.payment)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(CourseEntitlement.objects.filter(user=self.user, course=self.course).count(), 1)
        self.assertEqual(CourseEntitlementEvent.objects.filter(entitlement=first).count(), 1)

    def test_timed_entitlement_release_timing(self):
        self.offer.access_duration_days = 30
        self.offer.save(update_fields=["access_duration_days", "updated_at"])
        entitlement, _created = grant_course_entitlement(user=self.user, offer=self.offer, payment_intent=self.payment)
        self.lesson.release_day = 2
        self.lesson.save(update_fields=["release_day", "updated_at"])
        before = entitlement.starts_at + timedelta(hours=23)
        unlocked = entitlement.starts_at + timedelta(days=1)
        self.assertEqual(lesson_release_at(entitlement, self.lesson), unlocked)
        self.assertFalse(can_access_lesson(self.user, self.lesson, before))
        self.assertTrue(can_access_lesson(self.user, self.lesson, unlocked))
        self.assertFalse(entitlement.has_access_at(entitlement.expires_at))

    def test_preview_is_public_and_answer_key_is_separate(self):
        self.lesson.is_preview = True
        self.lesson.save(update_fields=["is_preview", "updated_at"])
        material = LessonInstructorMaterial.objects.create(lesson=self.lesson, answer_key_markdown="Instructor-only answer")
        self.assertTrue(can_access_lesson(AnonymousUser(), self.lesson))
        self.assertEqual(material.answer_key_markdown, "Instructor-only answer")
        self.assertFalse(hasattr(self.lesson, "answer_key_markdown"))

    def test_progress_requires_access_and_persists(self):
        with self.assertRaises(PermissionDenied):
            update_lesson_progress(user=self.user, lesson=self.lesson, status=LessonProgress.Status.COMPLETED)
        grant_course_entitlement(user=self.user, offer=self.offer, payment_intent=self.payment)
        progress = update_lesson_progress(user=self.user, lesson=self.lesson, status=LessonProgress.Status.IN_PROGRESS, response_json={"observation": "price turned twice"}, seconds_delta=45)
        self.assertEqual(progress.time_spent_seconds, 45)
        self.assertEqual(progress.response_json["observation"], "price turned twice")

    def test_refund_revoke_only_targets_current_purchase(self):
        entitlement, _created = grant_course_entitlement(user=self.user, offer=self.offer, payment_intent=self.payment)
        unrelated = PaymentIntent.objects.create(user=self.user, plan=self.plan, amount=99900, currency="INR", provider=PaymentIntent.Provider.RAZORPAY, status=PaymentIntent.Status.SUCCESS)
        self.assertFalse(revoke_course_entitlement_for_payment(unrelated, reason="full refund"))
        self.assertTrue(revoke_course_entitlement_for_payment(self.payment, reason="full refund"))
        entitlement.refresh_from_db()
        self.assertEqual(entitlement.status, CourseEntitlement.Status.REVOKED)
        self.assertFalse(entitlement.has_access)

    def test_progress_is_unique_per_user_and_lesson(self):
        LessonProgress.objects.create(user=self.user, lesson=self.lesson)
        with self.assertRaises(IntegrityError):
            LessonProgress.objects.create(user=self.user, lesson=self.lesson)
