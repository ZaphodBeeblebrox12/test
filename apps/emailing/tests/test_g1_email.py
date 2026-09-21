"""G1: email foundation — templates, versions, variables, rendering, delivery."""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.emailing.models import (
    ContentBlock, Delivery, MediaAsset, Template, TemplateVersion, VariableSpec)
from apps.emailing.services.render import (
    html_to_plain_text, render_blocks_html, render_version, substitute_variables)

User = get_user_model()


class TemplateVersionTests(TestCase):
    def _tpl(self, mode="html", html="<p>Hi {{ first_name }}</p>", blocks=None):
        t = Template.objects.create(name="Welcome", kind="marketing")
        v = TemplateVersion(
            template=t, version_number=1, editor_mode=mode,
            subject="Welcome {{ first_name }}", preview_text="pt",
            html=html, plain_text="", visual_blocks=blocks or [])
        v.save()
        return t, v

    def test_immutable_version_unique_per_template(self):
        t, v = self._tpl()
        dup = TemplateVersion(template=t, version_number=1, editor_mode="html",
                              subject="x", html="y")
        with self.assertRaises(Exception):
            dup.save()

    def test_html_mode_preserves_html_verbatim(self):
        authored = '<div class="x"><p>Line1</p>\n  <p>{{ plan_name }}</p></div>'
        _, v = self._tpl(mode="html", html=authored)
        self.assertEqual(v.html, authored)  # not rewritten/normalized

    def test_visual_mode_requires_blocks(self):
        t = Template.objects.create(name="V", kind="marketing")
        v = TemplateVersion(template=t, version_number=1, editor_mode="visual",
                            subject="s", html="", visual_blocks=[])
        with self.assertRaises(ValidationError):
            v.full_clean()

    def test_visual_mode_generates_html(self):
        blocks = [{"type": "heading", "props": {"text": "Hello"}},
                  {"type": "text", "props": {"text": "Body"}}]
        _, v = self._tpl(mode="visual", html="", blocks=blocks)
        subj, html, plain = render_version(v, {})
        self.assertIn("Hello", html)
        self.assertIn("Body", html)
        self.assertIn("Hello", plain)


class VariableTests(TestCase):
    def test_only_declared_variables_render(self):
        t = Template.objects.create(name="V", kind="marketing")
        v = TemplateVersion.objects.create(
            template=t, version_number=1, editor_mode="html",
            subject="s", html="<p>{{ first_name }} {{ undeclared_thing }}</p>")
        vs = VariableSpec.objects.create(name="first_name", default_value="Friend")
        v.variables.add(vs)
        _, html, _ = render_version(v, {"first_name": "Ada"})
        self.assertIn("Ada", html)
        self.assertIn("{{ undeclared_thing }}", html)  # left verbatim, not executed

    def test_missing_variable_deterministic(self):
        t = Template.objects.create(name="V", kind="marketing")
        v = TemplateVersion.objects.create(
            template=t, version_number=1, editor_mode="html",
            subject="s", html="<p>{{ first_name }}</p>")
        vs = VariableSpec.objects.create(name="first_name", default_value="Friend")
        v.variables.add(vs)
        _, html, _ = render_version(v, {})  # not provided
        self.assertIn("{{ first_name }}", html)  # stays as token

    def test_variable_substitution_inserts_verbatim(self):
        # Values are substituted verbatim (author controls HTML); the system
        # does not silently escape or rewrite.  Authors escape user data.
        out = substitute_variables("<p>{{ x }}</p>", {"x": "Hello"})
        self.assertIn("Hello", out)
        # undeclared/unknown tokens are left untouched (never executed)
        out2 = substitute_variables("<p>{{ unknown }}</p>", {"x": 1})
        self.assertIn("{{ unknown }}", out2)


class BlockRenderTests(TestCase):
    def test_each_block_type(self):
        cases = {
            "heading": ({"type": "heading", "props": {"text": "H"}}, "<h2"),
            "text": ({"type": "text", "props": {"text": "T"}}, "<p"),
            "button": ({"type": "button", "props": {"url": "https://x", "label": "Go"}}, "<a href"),
            "image": ({"type": "image", "props": {"src": "https://i", "alt": "a"}}, "<img"),
            "divider": ({"type": "divider", "props": {}}, "<hr"),
            "spacer": ({"type": "spacer", "props": {"height": 10}}, 'height:10px'),
            "footer": ({"type": "footer", "props": {"text": "F"}}, "F"),
            "social": ({"type": "social", "props": {"links": [{"url": "https://t", "label": "T"}]}}, "https://t"),
            "columns": ({"type": "columns", "props": {"columns": [{"text": "A"}, {"text": "B"}]}}, "<table"),
        }
        for name, (block, marker) in cases.items():
            html = render_blocks_html([block])
            self.assertIn(marker, html, f"{name} did not render")

    def test_deterministic(self):
        blocks = [{"type": "text", "props": {"text": "X"}}]
        self.assertEqual(render_blocks_html(blocks), render_blocks_html(blocks))


class PlainTextTests(TestCase):
    def test_html_to_plain(self):
        self.assertEqual(html_to_plain_text("<p>Hello</p><p>World</p>"), "Hello\nWorld")

    def test_authored_plain_preserved(self):
        t = Template.objects.create(name="V", kind="marketing")
        v = TemplateVersion.objects.create(
            template=t, version_number=1, editor_mode="html",
            subject="s", html="<p>Hi</p>", plain_text="Authored plain")
        _, _, plain = render_version(v, {})
        self.assertEqual(plain, "Authored plain")


class MediaTests(TestCase):
    def test_media_asset_url_not_base64(self):
        m = MediaAsset.objects.create(
            public_url="https://cdn.example.com/a.png", alt_text="Logo",
            width=100, height=50)
        self.assertTrue(m.public_url.startswith("https://"))
        self.assertNotIn("base64", m.public_url)
        self.assertEqual(m.alt_text, "Logo")


class DeliveryTests(TestCase):
    def test_idempotency_key_unique(self):
        d1 = Delivery.objects.create(idempotency_key="k1", to_email="a@b.c")
        with self.assertRaises(Exception):
            Delivery.objects.create(idempotency_key="k1", to_email="a@b.c")

    def test_states(self):
        d = Delivery.objects.create(idempotency_key="k2", to_email="a@b.c")
        for s in ["queued", "sending", "sent", "failed", "bounced", "complained", "suppressed"]:
            d.state = s
            d.save()
            self.assertEqual(d.state, s)
