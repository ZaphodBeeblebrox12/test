"""Retain source image URLs so uploaded replacements remain optional overrides."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("academy", "0002_course_import_review_workflow"),
    ]

    operations = [
        migrations.AddField(
            model_name="courseimportasset",
            name="original_url",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="coursemediaasset",
            name="original_url",
            field=models.TextField(blank=True),
        ),
    ]
