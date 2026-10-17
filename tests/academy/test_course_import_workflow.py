import json
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from apps.academy.content_rendering import validate_markdown_source
from apps.academy.importing import publish_course_import, validate_import_for_publish
from apps.academy.models import Course, CourseContentRevision, CourseImport, CourseModule, Lesson, LessonProgress

User = get_user_model()


class CourseImportWorkflowTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "lessons").mkdir()
        self.source = self.root / "lessons" / "module-01.md"
        self.manifest_path = self.root / "course-manifest.json"
        self.write_source("Initial lesson explanation.")
        self.write_manifest()
        self.admin = User.objects.create_user(
            username="course-admin", password="test-password",
            role=User.Role.ADMIN, is_staff=True,
        )
        self.learner = User.objects.create_user(username="learner", password="test-password")
        self.ordinary_staff = User.objects.create_user(
            username="ordinary-staff", password="test-password",
            role=User.Role.STAFF, is_staff=True,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def write_source(self, body):
        self.source.write_text(
            "## Lesson 1.1 — Read the evidence\n\n### The question\n\n" + body + "\n",
            encoding="utf-8",
        )

    def write_manifest(self):
        manifest = {
            "schema_version": 1,
            "manifest_version": "test-v1",
            "course": {
                "title": "Import Test Course",
                "slug": "import-test-course",
                "summary": "A course used in import tests.",
                "description": "Review before publication.",
                "position": 50,
            },
            "notebook": {
                "title": "Your Trading Notebook",
                "prompts": [{"label": "Observe", "text": "Describe the evidence."}],
            },
            "modules": [{
                "title": "Foundations",
                "slug": "foundations",
                "summary": "The first lesson.",
                "position": 1,
                "source": "lessons/module-01.md",
                "lessons": [{
                    "source_id": "1.1",
                    "title": "Read the evidence",
                    "slug": "read-the-evidence",
                    "position": 1,
                    "duration_minutes": 12,
                    "release_day": 1,
                }],
            }],
        }
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def import_draft(self):
        call_command("import_microcourse", manifest=str(self.manifest_path), verbosity=0)
        return CourseImport.objects.get(course_slug="import-test-course")

    def test_repeated_import_is_idempotent_and_does_not_create_live_course(self):
        first = self.import_draft()
        second = self.import_draft()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(CourseImport.objects.filter(course_slug="import-test-course").count(), 1)
        self.assertFalse(Course.objects.filter(slug="import-test-course").exists())
        self.assertEqual(first.status, CourseImport.Status.REVIEW)

    def test_import_update_is_staged_until_explicit_publish_and_keeps_progress(self):
        first = self.import_draft()
        published_course = publish_course_import(first, user=self.admin)
        module = CourseModule.objects.get(course=published_course, slug="foundations")
        lesson = Lesson.objects.get(module=module, slug="read-the-evidence")
        lesson_id = lesson.pk
        progress = LessonProgress.objects.create(
            user=self.learner, lesson=lesson,
            status=LessonProgress.Status.COMPLETED,
        )
        old_content = lesson.content_markdown

        self.write_source("Updated explanation with a contrasting example.")
        second = self.import_draft()
        lesson.refresh_from_db()
        self.assertEqual(lesson.content_markdown, old_content)
        self.assertEqual(published_course.status, Course.Status.PUBLISHED)

        publish_course_import(second, user=self.admin)
        lesson.refresh_from_db()
        progress.refresh_from_db()
        self.assertEqual(lesson.pk, lesson_id)
        self.assertIn("Updated explanation", lesson.content_markdown)
        self.assertEqual(progress.status, LessonProgress.Status.COMPLETED)
        self.assertEqual(CourseContentRevision.objects.filter(lesson=lesson).count(), 1)

    def test_missing_image_blocks_publish_and_leaves_course_unpublished(self):
        self.write_source("![Missing chart](../assets/not-ready.png)\n\nThis is the lesson.")
        record = self.import_draft()
        errors = validate_import_for_publish(record)
        self.assertTrue(any("Missing course image" in error for error in errors))
        with self.assertRaises(ValidationError):
            publish_course_import(record, user=self.admin)
        self.assertFalse(Course.objects.filter(slug="import-test-course").exists())

    def test_ordinary_staff_cannot_open_admin_course_review(self):
        record = self.import_draft()
        self.client.force_login(self.ordinary_staff)
        response = self.client.get(
            reverse("admin:academy_courseimport_review", args=[record.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_publish_requires_csrf_token(self):
        record = self.import_draft()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        response = client.post(
            reverse("admin:academy_courseimport_publish", args=[record.pk]),
            {},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Course.objects.filter(slug="import-test-course").exists())

    def test_unsafe_active_markup_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_markdown_source("Lesson text <script>alert(1)</script>")


class CourseImportAdminPreviewTests(TestCase):
    def test_authorized_admin_can_open_course_preview(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "lessons").mkdir()
        (root / "lessons" / "module-01.md").write_text(
            "## Lesson 1.1 — Read the evidence\n\n### The question\n\nPreview text.",
            encoding="utf-8",
        )
        manifest = {
            "schema_version": 1,
            "course": {"title": "Reviewable Course", "slug": "reviewable-course"},
            "modules": [{
                "title": "Start", "slug": "start", "position": 1,
                "source": "lessons/module-01.md",
                "lessons": [{"source_id": "1.1", "title": "Read the evidence", "slug": "read-the-evidence"}],
            }],
        }
        manifest_path = root / "course-manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        call_command("import_microcourse", manifest=str(manifest_path), verbosity=0)
        record = CourseImport.objects.get(course_slug="reviewable-course")
        admin_user = User.objects.create_user(
            username="preview-admin", password="test-password",
            role=User.Role.ADMIN, is_staff=True,
        )
        self.client.force_login(admin_user)
        response = self.client.get(reverse("admin:academy_courseimport_review", args=[record.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Preview text.")
        self.assertContains(response, "Approve and publish course")
