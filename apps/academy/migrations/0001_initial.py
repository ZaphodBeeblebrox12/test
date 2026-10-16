import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("payments", "0010_refund_commercial_context"),
        ("subscriptions", "0018_add_one_time_price_interval"),
    ]
    operations = [
        migrations.CreateModel(name="Course", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ("title", models.CharField(max_length=200)), ("slug", models.SlugField(max_length=160, unique=True)),
            ("summary", models.CharField(blank=True, max_length=360)), ("description", models.TextField(blank=True)),
            ("status", models.CharField(choices=[("draft", "Draft"), ("published", "Published"), ("archived", "Archived")], default="draft", max_length=16)),
            ("position", models.PositiveSmallIntegerField(default=0)), ("created_at", models.DateTimeField(auto_now_add=True)),
            ("updated_at", models.DateTimeField(auto_now=True)),
        ], options={"ordering": ["position", "title"]}),
        migrations.CreateModel(name="CourseModule", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ("title", models.CharField(max_length=200)), ("slug", models.SlugField(max_length=160)),
            ("description", models.TextField(blank=True)), ("position", models.PositiveSmallIntegerField(default=0)),
            ("is_published", models.BooleanField(default=False)), ("created_at", models.DateTimeField(auto_now_add=True)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="modules", to="academy.course")),
        ], options={"ordering": ["position", "title"]}),
        migrations.CreateModel(name="CourseOffer", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ("name", models.CharField(max_length=200)), ("slug", models.SlugField(max_length=160, unique=True)),
            ("access_duration_days", models.PositiveIntegerField(blank=True, help_text="Leave blank for permanent course access.", null=True, validators=[django.core.validators.MinValueValidator(1)])),
            ("is_active", models.BooleanField(default=False)), ("position", models.PositiveSmallIntegerField(default=0)),
            ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
            ("course", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="offers", to="academy.course")),
            ("plan", models.OneToOneField(blank=True, help_text="Use a dedicated Plan with an active one_time price; do not reuse a subscription Plan.", null=True, on_delete=django.db.models.deletion.PROTECT, related_name="academy_offer", to="subscriptions.plan")),
        ], options={"ordering": ["position", "name"]}),
        migrations.CreateModel(name="Lesson", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ("title", models.CharField(max_length=240)), ("slug", models.SlugField(max_length=160)),
            ("summary", models.CharField(blank=True, max_length=360)),
            ("format", models.CharField(choices=[("reading", "Reading"), ("video", "Video"), ("interactive", "Interactive"), ("quiz", "Quiz")], default="reading", max_length=16)),
            ("content_markdown", models.TextField(blank=True)), ("content_json", models.JSONField(blank=True, default=dict)),
            ("position", models.PositiveSmallIntegerField(default=0)), ("duration_minutes", models.PositiveSmallIntegerField(default=10)),
            ("is_preview", models.BooleanField(default=False)), ("is_published", models.BooleanField(default=False)),
            ("release_day", models.PositiveSmallIntegerField(default=1, validators=[django.core.validators.MinValueValidator(1)])),
            ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
            ("module", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lessons", to="academy.coursemodule")),
        ], options={"ordering": ["position", "title"]}),
        migrations.CreateModel(name="LessonInstructorMaterial", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("answer_key_markdown", models.TextField(blank=True)), ("instructor_notes", models.TextField(blank=True)),
            ("rubric_json", models.JSONField(blank=True, default=dict)), ("updated_at", models.DateTimeField(auto_now=True)),
            ("lesson", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="instructor_material", to="academy.lesson")),
        ]),
        migrations.CreateModel(name="CourseEntitlement", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ("source", models.CharField(choices=[("purchase", "Purchase"), ("admin", "Administrator grant"), ("migration", "Migration"), ("complimentary", "Complimentary access")], default="purchase", max_length=16)),
            ("status", models.CharField(choices=[("active", "Active"), ("revoked", "Revoked")], default="active", max_length=12)),
            ("starts_at", models.DateTimeField(default=django.utils.timezone.now)), ("expires_at", models.DateTimeField(blank=True, help_text="NULL means permanent access.", null=True)),
            ("revoked_at", models.DateTimeField(blank=True, null=True)), ("revocation_reason", models.CharField(blank=True, default="", max_length=240)),
            ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
            ("course", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="entitlements", to="academy.course")),
            ("granted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="academy_entitlements_granted", to=settings.AUTH_USER_MODEL)),
            ("offer", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="entitlements", to="academy.courseoffer")),
            ("payment_intent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="academy_entitlements", to="payments.paymentintent")),
            ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="academy_entitlements", to=settings.AUTH_USER_MODEL)),
        ], options={"ordering": ["-created_at"]}),
        migrations.CreateModel(name="CourseEntitlementEvent", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ("event_type", models.CharField(choices=[("granted", "Granted"), ("extended", "Extended"), ("reinstated", "Reinstated"), ("revoked", "Revoked")], max_length=16)),
            ("reason", models.CharField(blank=True, default="", max_length=240)), ("details", models.JSONField(blank=True, default=dict)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ("entitlement", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="events", to="academy.courseentitlement")),
            ("payment_intent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="academy_entitlement_events", to="payments.paymentintent")),
        ], options={"ordering": ["-created_at"]}),
        migrations.CreateModel(name="LessonProgress", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ("status", models.CharField(choices=[("not_started", "Not started"), ("in_progress", "In progress"), ("completed", "Completed")], default="not_started", max_length=16)),
            ("started_at", models.DateTimeField(blank=True, null=True)), ("completed_at", models.DateTimeField(blank=True, null=True)),
            ("last_seen_at", models.DateTimeField(blank=True, null=True)), ("time_spent_seconds", models.PositiveIntegerField(default=0)),
            ("response_json", models.JSONField(blank=True, default=dict)), ("created_at", models.DateTimeField(auto_now_add=True)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("lesson", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="learner_progress", to="academy.lesson")),
            ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="academy_lesson_progress", to=settings.AUTH_USER_MODEL)),
        ], options={"ordering": ["-updated_at"]}),
        migrations.AddConstraint(model_name="coursemodule", constraint=models.UniqueConstraint(fields=("course", "slug"), name="academy_module_course_slug_uniq")),
        migrations.AddConstraint(model_name="coursemodule", constraint=models.UniqueConstraint(fields=("course", "position"), name="academy_module_course_pos_uniq")),
        migrations.AddConstraint(model_name="lesson", constraint=models.UniqueConstraint(fields=("module", "slug"), name="academy_lesson_module_slug_uniq")),
        migrations.AddConstraint(model_name="lesson", constraint=models.UniqueConstraint(fields=("module", "position"), name="academy_lesson_module_pos_uniq")),
        migrations.AddConstraint(model_name="courseentitlement", constraint=models.UniqueConstraint(fields=("user", "course"), name="academy_entitlement_user_course_uniq")),
        migrations.AddConstraint(model_name="courseentitlement", constraint=models.CheckConstraint(check=models.Q(("expires_at__isnull", True), ("expires_at__gt", models.F("starts_at")), _connector="OR"), name="academy_entitlement_expiry_after_start")),
        migrations.AddConstraint(model_name="lessonprogress", constraint=models.UniqueConstraint(fields=("user", "lesson"), name="academy_progress_user_lesson_uniq")),
        migrations.AddIndex(model_name="course", index=models.Index(fields=["status", "position"], name="academy_course_status_pos")),
        migrations.AddIndex(model_name="lesson", index=models.Index(fields=["module", "is_published", "position"], name="academy_lesson_release_idx")),
        migrations.AddIndex(model_name="courseentitlement", index=models.Index(fields=["user", "status", "expires_at"], name="academy_entitlement_access_idx")),
        migrations.AddIndex(model_name="lessonprogress", index=models.Index(fields=["user", "status"], name="academy_progress_user_status_idx")),
    ]
