# Generated for the staged micro-course review and publication workflow.
import uuid

import apps.academy.models
import apps.academy.storage
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("academy", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CourseImport",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("course_slug", models.SlugField(db_index=True, max_length=160)),
                ("title", models.CharField(max_length=200)),
                ("manifest_version", models.CharField(default="1", max_length=40)),
                ("content_hash", models.CharField(max_length=64)),
                ("source_manifest", models.TextField()),
                ("payload", models.JSONField(default=dict)),
                ("status", models.CharField(choices=[("review", "Awaiting review"), ("published", "Published"), ("rejected", "Rejected"), ("superseded", "Superseded by a newer import")], default="review", max_length=16)),
                ("imported_at", models.DateTimeField(auto_now_add=True)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("imported_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="academy_course_imports", to=settings.AUTH_USER_MODEL)),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="academy_course_imports_reviewed", to=settings.AUTH_USER_MODEL)),
                ("target_course", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="imports", to="academy.course")),
            ],
            options={"ordering": ["-imported_at"]},
        ),
        migrations.CreateModel(
            name="CourseImportAsset",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("asset_key", models.CharField(max_length=512)),
                ("stable_asset_id", models.UUIDField(db_index=True)),
                ("alt_text", models.CharField(blank=True, max_length=500)),
                ("source_path", models.CharField(blank=True, max_length=512)),
                ("source_error", models.CharField(blank=True, max_length=240)),
                ("file", models.ImageField(blank=True, storage=apps.academy.storage.PrivateCourseAssetStorage(), upload_to=apps.academy.models.course_import_asset_upload_to)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("course_import", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="assets", to="academy.courseimport")),
            ],
            options={"ordering": ["asset_key"]},
        ),
        migrations.CreateModel(
            name="CourseMediaAsset",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("asset_key", models.CharField(max_length=512)),
                ("file", models.ImageField(blank=True, storage=apps.academy.storage.PrivateCourseAssetStorage(), upload_to=apps.academy.models.course_media_asset_upload_to)),
                ("alt_text", models.CharField(blank=True, max_length=500)),
                ("source_path", models.CharField(blank=True, max_length=512)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="media_assets", to="academy.course")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="academy_media_assets_updated", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["asset_key"]},
        ),
        migrations.CreateModel(
            name="CourseContentRevision",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("revision_type", models.CharField(choices=[("admin_edit", "Administrator edit"), ("import", "Approved course import")], max_length=16)),
                ("title_before", models.CharField(max_length=240)),
                ("summary_before", models.CharField(blank=True, max_length=360)),
                ("content_markdown_before", models.TextField(blank=True)),
                ("content_json_before", models.JSONField(blank=True, default=dict)),
                ("fields_changed", models.JSONField(blank=True, default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("editor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="academy_content_revisions", to=settings.AUTH_USER_MODEL)),
                ("lesson", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="content_revisions", to="academy.lesson")),
                ("source_import", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="content_revisions", to="academy.courseimport")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="courseimport",
            constraint=models.UniqueConstraint(fields=("course_slug", "content_hash"), name="academy_import_slug_hash_uq"),
        ),
        migrations.AddIndex(
            model_name="courseimport",
            index=models.Index(fields=["status", "imported_at"], name="academy_import_status_idx"),
        ),
        migrations.AddConstraint(
            model_name="courseimportasset",
            constraint=models.UniqueConstraint(fields=("course_import", "asset_key"), name="academy_import_asset_key_uq"),
        ),
        migrations.AddConstraint(
            model_name="coursemediaasset",
            constraint=models.UniqueConstraint(fields=("course", "asset_key"), name="academy_media_course_key_uq"),
        ),
    ]
