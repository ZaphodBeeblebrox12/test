import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.academy.models import Course, CourseMediaAsset, CourseModule, Lesson

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

    def test_published_preview_renders_multiple_images_in_selected_learning_room_tabs(self):
        placements = [
            ("Idea figure", "Additional concept figure", "idea", "https://example.com/idea.png"),
            ("Example chart", "Additional chart examples", "examples", "https://example.com/examples.png"),
            ("Practice figure", "Practice figure", "practice", "https://example.com/practice.png"),
        ]
        assets = []
        markdown_parts = ["### The question", "", "Look at the evidence in each panel."]
        for index, (alt_text, heading, _tab, url) in enumerate(placements, start=1):
            asset = CourseMediaAsset.objects.create(
                course=self.course,
                asset_key=f"test/learning-room-figure-{index}",
                alt_text=alt_text,
                original_url=url,
            )
            assets.append(asset)
            markdown_parts.extend([
                "",
                f"### {heading}",
                "",
                f"![{alt_text}](asset://{asset.pk})",
            ])
        self.preview_lesson.content_markdown = "\n".join(markdown_parts)
        self.preview_lesson.content_json = {"asset_ids": [str(asset.pk) for asset in assets]}
        self.preview_lesson.save(update_fields=["content_markdown", "content_json", "updated_at"])

        response = self.client.get(
            reverse("academy:lesson", args=[self.course.slug, self.preview_lesson.pk])
        )
        self.assertEqual(response.status_code, 200)
        rendered_html = response.content.decode()
        panel_patterns = {
            tab: re.compile(
                rf'<div class="tt-tab-panel" role="tabpanel" data-tab-panel="{tab}"[^>]*>(.*?)</div>',
                re.DOTALL,
            )
            for tab in ("idea", "examples", "practice")
        }
        panels = {}
        for tab, pattern in panel_patterns.items():
            match = pattern.search(rendered_html)
            self.assertIsNotNone(match, f"Published lesson is missing the {tab} tab panel.")
            panels[tab] = match.group(1)

        self.assertIn('<span class="tt-tab-count">3</span>', rendered_html)
        for alt_text, _heading, expected_tab, url in placements:
            self.assertIn(f'alt="{alt_text}"', panels[expected_tab])
            self.assertIn(f'src="{url}"', panels[expected_tab])
            for other_tab, panel_html in panels.items():
                if other_tab != expected_tab:
                    self.assertNotIn(url, panel_html)

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
