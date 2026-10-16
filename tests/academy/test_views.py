from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.academy.models import Course, CourseModule, Lesson

User = get_user_model()


class AcademyViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="learner", password="Test-password-123!")
        self.course = Course.objects.create(
            title="Trading Path", slug="trading-path", status=Course.Status.PUBLISHED,
            summary="Learn to read the screen.",
        )
        self.module = CourseModule.objects.create(
            course=self.course, title="Foundations", slug="foundations",
            position=1, is_published=True,
        )
        self.paid_lesson = Lesson.objects.create(
            module=self.module, title="Private lesson", slug="private-lesson",
            position=1, is_published=True, content_markdown="SECRET PAID LESSON BODY",
        )
        self.preview_lesson = Lesson.objects.create(
            module=self.module, title="Preview lesson", slug="preview-lesson",
            position=2, is_published=True, is_preview=True,
            content_markdown="Public preview content.",
        )
        self.client.force_login(self.user)

    def test_course_overview_does_not_include_paid_lesson_body(self):
        response = self.client.get(reverse("academy:course-detail", args=[self.course.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Private lesson")
        self.assertNotContains(response, "SECRET PAID LESSON BODY")

    def test_paid_lesson_is_not_retrievable_without_entitlement(self):
        response = self.client.get(reverse("academy:lesson", args=[self.course.slug, self.paid_lesson.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, "SECRET PAID LESSON BODY", status_code=403)

    def test_published_preview_lesson_can_be_opened(self):
        response = self.client.get(reverse("academy:lesson", args=[self.course.slug, self.preview_lesson.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Public preview content.")

    def test_progress_endpoint_rejects_unentitled_paid_lesson(self):
        response = self.client.post(
            reverse("academy:lesson-progress", args=[self.paid_lesson.pk]),
            {"status": "completed"},
        )
        self.assertEqual(response.status_code, 403)

    def test_learning_room_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("academy:home"))
        self.assertEqual(response.status_code, 302)
