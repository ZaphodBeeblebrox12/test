"""Course catalogue, course entitlements and learner progress."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Course(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        PUBLISHED = "published", _("Published")
        ARCHIVED = "archived", _("Archived")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=160, unique=True)
    summary = models.CharField(max_length=360, blank=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    position = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "title"]
        indexes = [models.Index(fields=["status", "position"], name="academy_course_status_pos")]

    def __str__(self):
        return self.title


class CourseModule(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="modules")
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=160)
    description = models.TextField(blank=True)
    position = models.PositiveSmallIntegerField(default=0)
    is_published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "title"]
        constraints = [
            models.UniqueConstraint(fields=["course", "slug"], name="academy_mod_course_slug_uq"),
            models.UniqueConstraint(fields=["course", "position"], name="academy_mod_course_pos_uq"),
        ]

    def __str__(self):
        return f"{self.course.title} · {self.title}"


class Lesson(models.Model):
    class Format(models.TextChoices):
        READING = "reading", _("Reading")
        VIDEO = "video", _("Video")
        INTERACTIVE = "interactive", _("Interactive")
        QUIZ = "quiz", _("Quiz")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    module = models.ForeignKey(CourseModule, on_delete=models.CASCADE, related_name="lessons")
    title = models.CharField(max_length=240)
    slug = models.SlugField(max_length=160)
    summary = models.CharField(max_length=360, blank=True)
    format = models.CharField(max_length=16, choices=Format.choices, default=Format.READING)
    # Paid lesson body is stored server-side, never in the public static website.
    content_markdown = models.TextField(blank=True)
    # Learner-safe interaction data only; answer keys live in LessonInstructorMaterial.
    content_json = models.JSONField(default=dict, blank=True)
    position = models.PositiveSmallIntegerField(default=0)
    duration_minutes = models.PositiveSmallIntegerField(default=10)
    is_preview = models.BooleanField(default=False)
    is_published = models.BooleanField(default=False)
    # Day 1 unlocks at enrolment; day 2 unlocks 24 hours later, etc.
    release_day = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "title"]
        constraints = [
            models.UniqueConstraint(fields=["module", "slug"], name="academy_les_mod_slug_uq"),
            models.UniqueConstraint(fields=["module", "position"], name="academy_les_mod_pos_uq"),
        ]
        indexes = [models.Index(fields=["module", "is_published", "position"], name="academy_lesson_release_idx")]

    @property
    def course(self):
        return self.module.course

    def __str__(self):
        return f"{self.module.course.title} · {self.title}"


class LessonInstructorMaterial(models.Model):
    """Instructor-only answer keys and rubrics, separate from learner payloads."""
    lesson = models.OneToOneField(Lesson, on_delete=models.CASCADE, related_name="instructor_material")
    answer_key_markdown = models.TextField(blank=True)
    instructor_notes = models.TextField(blank=True)
    rubric_json = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Instructor material · {self.lesson}"


class CourseOffer(models.Model):
    """Connects a one-time commerce Plan to the specific course it grants."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="offers")
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=160, unique=True)
    plan = models.OneToOneField(
        "subscriptions.Plan", on_delete=models.PROTECT, related_name="academy_offer",
        null=True, blank=True,
        help_text="Use a dedicated Plan with an active one_time price; do not reuse a subscription Plan.",
    )
    # NULL means permanent access. Timed access must be an explicit product rule.
    access_duration_days = models.PositiveIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1)],
        help_text="Leave blank for permanent course access.",
    )
    is_active = models.BooleanField(default=False)
    position = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "name"]

    def clean(self):
        super().clean()
        errors = {}
        if self.is_active:
            if not self.plan_id:
                errors["plan"] = "An active course offer must map to a commerce Plan."
            if self.course_id and self.course.status != Course.Status.PUBLISHED:
                errors["course"] = "Publish the course before activating its offer."
            if self.plan_id:
                if not self.plan.is_hidden:
                    errors["plan"] = (
                        "Course offer Plans must be hidden from the subscription catalogue."
                    )
                from apps.subscriptions.models import GeoPlanPrice, PlanPrice
                has_price = PlanPrice.objects.filter(
                    plan_id=self.plan_id, interval="one_time", is_active=True
                ).exists() or GeoPlanPrice.objects.filter(
                    plan_id=self.plan_id, interval="one_time", is_active=True
                ).exists()
                if not has_price:
                    errors["plan"] = "Add an active one-time price before activating this offer."
            if errors:
                raise ValidationError(errors)

        # Do not reinterpret old/pending payments as a different course or access term.
        if self.plan_id:
            from apps.payments.models import PaymentIntent
            from apps.subscriptions.models import Subscription
            if self._state.adding:
                if PaymentIntent.objects.filter(plan_id=self.plan_id).exists() or Subscription.objects.filter(plan_id=self.plan_id).exists():
                    raise ValidationError({
                        "plan": "This Plan has payment or subscription history; create a new Plan for the course offer."
                    })
            else:
                previous = CourseOffer.objects.filter(pk=self.pk).first()
                if previous and (
                    previous.plan_id != self.plan_id
                    or previous.course_id != self.course_id
                    or previous.access_duration_days != self.access_duration_days
                ) and previous.plan_id and PaymentIntent.objects.filter(plan_id=previous.plan_id).exists():
                    raise ValidationError(
                        "Course, Plan and access duration are frozen after a payment intent exists. "
                        "Create a new offer for future sales instead."
                    )

    @property
    def is_saleable(self):
        return bool(self.is_active and self.plan_id and self.course.status == Course.Status.PUBLISHED)

    def __str__(self):
        return self.name


class CourseEntitlement(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        REVOKED = "revoked", _("Revoked")

    class Source(models.TextChoices):
        PURCHASE = "purchase", _("Purchase")
        ADMIN = "admin", _("Administrator grant")
        MIGRATION = "migration", _("Migration")
        COMPLIMENTARY = "complimentary", _("Complimentary access")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="academy_entitlements")
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="entitlements")
    offer = models.ForeignKey(CourseOffer, on_delete=models.SET_NULL, null=True, blank=True, related_name="entitlements")
    payment_intent = models.ForeignKey(
        "payments.PaymentIntent", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="academy_entitlements",
    )
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.PURCHASE)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    starts_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True, help_text="NULL means permanent access.")
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.CharField(max_length=240, blank=True, default="")
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="academy_entitlements_granted",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "course"], name="academy_ent_user_course_uq"),
            models.CheckConstraint(
                check=models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=models.F("starts_at")),
                name="academy_ent_expiry_start_ck",
            ),
        ]
        indexes = [models.Index(fields=["user", "status", "expires_at"], name="academy_entitlement_access_idx")]

    def has_access_at(self, at=None):
        at = at or timezone.now()
        return self.status == self.Status.ACTIVE and self.starts_at <= at and (self.expires_at is None or self.expires_at > at)

    @property
    def has_access(self):
        return self.has_access_at()

    def __str__(self):
        return f"{self.user} · {self.course.title} ({self.status})"


class CourseEntitlementEvent(models.Model):
    class EventType(models.TextChoices):
        GRANTED = "granted", _("Granted")
        EXTENDED = "extended", _("Extended")
        REINSTATED = "reinstated", _("Reinstated")
        REVOKED = "revoked", _("Revoked")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entitlement = models.ForeignKey(CourseEntitlement, on_delete=models.PROTECT, related_name="events")
    payment_intent = models.ForeignKey(
        "payments.PaymentIntent", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="academy_entitlement_events",
    )
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    event_type = models.CharField(max_length=16, choices=EventType.choices)
    reason = models.CharField(max_length=240, blank=True, default="")
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.event_type} · {self.entitlement}"


class LessonProgress(models.Model):
    class Status(models.TextChoices):
        NOT_STARTED = "not_started", _("Not started")
        IN_PROGRESS = "in_progress", _("In progress")
        COMPLETED = "completed", _("Completed")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="academy_lesson_progress")
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="learner_progress")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NOT_STARTED)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    time_spent_seconds = models.PositiveIntegerField(default=0)
    # Learner responses only; instructor answer keys are stored separately.
    response_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [models.UniqueConstraint(fields=["user", "lesson"], name="academy_prog_user_lesson_uq")]
        indexes = [models.Index(fields=["user", "status"], name="academy_prog_user_status_idx")]

    def __str__(self):
        return f"{self.user} · {self.lesson.title} ({self.status})"
