"""Seed v1.0 ACTIVE placeholder PolicyVersions (marked as placeholders).

These are structural placeholders, NOT final legal text. Replace by creating
new versions (2.0) with real content and publishing them - never edit v1.0.
"""
from django.db import migrations
from django.utils import timezone

_PLACEHOLDER = """<p><strong>PLACEHOLDER v1.0</strong> - structural placeholder,
not final legal text. Replace with approved legal content before accepting
real payment volume. The version, content hash and acceptance records are
evidentiary regardless of the text they freeze.</p>
<p>Trade Thesis provides informational trading-community access. By accepting
you acknowledge the service description, billing interval, renewal behavior,
refund and cancellation terms and the risk disclaimer shown at checkout.</p>"""

_TYPES = [
    ("terms", "Terms of Service"),
    ("refund", "Refund & Cancellation Policy"),
    ("cancellation", "Cancellation Policy"),
    ("risk", "Risk Disclosure (Trading Disclaimer)"),
    ("privacy", "Privacy Policy"),
]


def seed(apps, schema_editor):
    PolicyVersion = apps.get_model("policies", "PolicyVersion")
    for policy_type, title in _TYPES:
        PolicyVersion.objects.get_or_create(
            policy_type=policy_type, version="1.0",
            defaults={
                "title": title,
                "content_html": _PLACEHOLDER,
                "status": "active",
                "effective_from": timezone.now(),
                "published_at": timezone.now(),
            })


def unseed(apps, schema_editor):
    PolicyVersion = apps.get_model("policies", "PolicyVersion")
    PolicyVersion.objects.filter(version="1.0").delete()


class Migration(migrations.Migration):
    dependencies = [("policies", "0001_initial")]

    operations = [migrations.RunPython(seed, unseed)]
