import mimetypes

from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.http import FileResponse, HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html

from .content_rendering import build_lesson_sections
from .forms import CourseImportAssetAdminForm, CourseMediaAssetAdminForm, LessonAdminForm
from .importing import publish_course_import, validate_import_for_publish
from .models import (
    Course,
    CourseContentRevision,
    CourseEntitlement,
    CourseEntitlementEvent,
    CourseImport,
    CourseImportAsset,
    CourseMediaAsset,
    CourseModule,
    CourseOffer,
    Lesson,
    LessonInstructorMaterial,
    LessonProgress,
)
from .permissions import is_course_content_admin


class CourseModuleInline(admin.TabularInline):
    model = CourseModule
    extra = 0
    show_change_link = True


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "status", "position", "updated_at")
    list_filter = ("status",)
    search_fields = ("title", "slug", "summary")
    prepopulated_fields = {"slug": ("title",)}
    inlines = (CourseModuleInline,)
    readonly_fields = ("created_at", "updated_at", "status")

    def has_module_permission(self, request):
        return is_course_content_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return is_course_content_admin(request.user)

    def has_add_permission(self, request):
        return is_course_content_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return is_course_content_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return is_course_content_admin(request.user)


@admin.register(CourseModule)
class CourseModuleAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "position", "is_published")
    list_filter = ("course", "is_published")
    search_fields = ("title", "course__title")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("created_at", "updated_at")

    def has_module_permission(self, request):
        return is_course_content_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return is_course_content_admin(request.user)

    def has_add_permission(self, request):
        return is_course_content_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return is_course_content_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return is_course_content_admin(request.user)


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    form = LessonAdminForm
    list_display = ("title", "module", "format", "position", "release_day", "is_preview", "is_published")
    list_filter = ("format", "is_preview", "is_published", "module__course")
    search_fields = ("title", "summary", "module__title", "module__course__title")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("created_at", "updated_at")

    def has_module_permission(self, request):
        return is_course_content_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return is_course_content_admin(request.user)

    def has_add_permission(self, request):
        return is_course_content_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return is_course_content_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return is_course_content_admin(request.user)

    def save_model(self, request, obj, form, change):
        changed_fields = []
        if change:
            previous = Lesson.objects.filter(pk=obj.pk).first()
            if previous:
                watched = ("title", "summary", "content_markdown", "content_json")
                changed_fields = [field for field in watched if getattr(previous, field) != getattr(obj, field)]
                if changed_fields:
                    CourseContentRevision.objects.create(
                        lesson=previous, editor=request.user,
                        revision_type=CourseContentRevision.RevisionType.ADMIN_EDIT,
                        title_before=previous.title, summary_before=previous.summary,
                        content_markdown_before=previous.content_markdown,
                        content_json_before=previous.content_json,
                        fields_changed=changed_fields,
                    )
        super().save_model(request, obj, form, change)
        if changed_fields:
            from apps.audit.models import AuditLog
            AuditLog.log(
                action="academy_lesson_content_updated", user=request.user,
                object_type="lesson", object_id=obj.pk,
                metadata={"fields_changed": changed_fields},
            )

    fieldsets = (
        (None, {"fields": ("module", "title", "slug", "summary", "format", "position", "duration_minutes", "release_day", "is_preview", "is_published")}),
        ("Learner content", {"fields": ("content_markdown", "content_json")}),
        ("System", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(LessonInstructorMaterial)
class LessonInstructorMaterialAdmin(admin.ModelAdmin):
    list_display = ("lesson", "updated_at")
    search_fields = ("lesson__title", "lesson__module__course__title")
    readonly_fields = ("updated_at",)


@admin.register(CourseOffer)
class CourseOfferAdmin(admin.ModelAdmin):
    list_display = ("name", "course", "plan", "access_duration_days", "is_active", "position")
    list_filter = ("is_active", "course")
    search_fields = ("name", "slug", "course__title", "plan__name")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")


class CourseEntitlementEventInline(admin.TabularInline):
    model = CourseEntitlementEvent
    extra = 0
    can_delete = False
    readonly_fields = tuple(f.name for f in CourseEntitlementEvent._meta.fields)
    ordering = ("-created_at",)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(CourseEntitlement)
class CourseEntitlementAdmin(admin.ModelAdmin):
    list_display = ("user", "course", "status", "starts_at", "expires_at", "source", "payment_intent")
    list_filter = ("status", "source", "course")
    search_fields = ("user__username", "user__email", "course__title", "payment_intent__provider_reference")
    readonly_fields = tuple(f.name for f in CourseEntitlement._meta.fields)
    inlines = (CourseEntitlementEventInline,)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CourseEntitlementEvent)
class CourseEntitlementEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "event_type", "entitlement", "payment_intent", "actor")
    list_filter = ("event_type", "created_at")
    readonly_fields = tuple(f.name for f in CourseEntitlementEvent._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LessonProgress)
class LessonProgressAdmin(admin.ModelAdmin):
    list_display = ("user", "lesson", "status", "time_spent_seconds", "updated_at")
    list_filter = ("status", "lesson__module__course")
    search_fields = ("user__username", "user__email", "lesson__title")
    readonly_fields = tuple(f.name for f in LessonProgress._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CourseImport)
class CourseImportAdmin(admin.ModelAdmin):
    list_display = ("title", "course_slug", "status", "manifest_version", "imported_at", "review_link")
    list_filter = ("status", "imported_at")
    search_fields = ("title", "course_slug", "content_hash")
    readonly_fields = tuple(field.name for field in CourseImport._meta.fields)
    ordering = ("-imported_at",)

    def has_module_permission(self, request):
        return is_course_content_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return is_course_content_admin(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Review")
    def review_link(self, obj):
        if obj.status not in (CourseImport.Status.REVIEW, CourseImport.Status.PUBLISHED):
            return "Not reviewable"
        return format_html('<a href="{}">Preview course</a>', reverse("admin:academy_courseimport_review", args=[obj.pk]))

    def get_urls(self):
        custom = [
            path("<uuid:object_id>/review/", self.admin_site.admin_view(self.review_view), name="academy_courseimport_review"),
            path("<uuid:object_id>/publish/", self.admin_site.admin_view(self.publish_view), name="academy_courseimport_publish"),
            path("<uuid:object_id>/asset/<uuid:asset_id>/", self.admin_site.admin_view(self.asset_view), name="academy_courseimport_asset"),
        ]
        return custom + super().get_urls()

    def _is_authorized(self, request):
        return is_course_content_admin(request.user)

    def review_view(self, request, object_id):
        if not self._is_authorized(request):
            return HttpResponseForbidden("Administrator course-content access is required.")
        record = get_object_or_404(CourseImport, pk=object_id)
        asset_urls = {}
        staged_assets = list(record.assets.all())
        for asset in staged_assets:
            live_asset = CourseMediaAsset.objects.filter(pk=asset.stable_asset_id).first()
            if asset.file or (live_asset and live_asset.file):
                asset_urls[str(asset.stable_asset_id)] = reverse(
                    "admin:academy_courseimport_asset", args=[record.pk, asset.stable_asset_id]
                )
            elif asset.original_url.startswith(("https://", "http://")):
                asset_urls[str(asset.stable_asset_id)] = asset.original_url
            elif live_asset and live_asset.original_url.startswith(("https://", "http://")):
                asset_urls[str(asset.stable_asset_id)] = live_asset.original_url

        review_modules = []
        for module in record.payload.get("modules", []):
            review_lessons = []
            for lesson_data in module.get("lessons", []):
                notebook = lesson_data.get("content_json", {}).get("notebook") or record.payload.get("notebook", {})
                review_lessons.append({
                    "lesson": lesson_data,
                    "sections": build_lesson_sections(lesson_data.get("content_markdown", ""), asset_urls),
                    "notebook": notebook,
                })
            review_modules.append({"module": module, "lessons": review_lessons})

        errors = validate_import_for_publish(record) if record.status == CourseImport.Status.REVIEW else []
        asset_rows = []
        for asset in staged_assets:
            live_asset = CourseMediaAsset.objects.filter(pk=asset.stable_asset_id).first()
            asset_rows.append({
                "asset": asset,
                "has_file": bool(asset.file) or bool(live_asset and live_asset.file),
                "preview_url": asset_urls.get(str(asset.stable_asset_id), ""),
                "original_url": asset.original_url or (live_asset.original_url if live_asset else ""),
                "edit_url": reverse("admin:academy_courseimportasset_change", args=[asset.pk]),
            })
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Review course import",
            "import_record": record,
            "review_modules": review_modules,
            "publish_errors": errors,
            "asset_rows": asset_rows,
            "publish_url": reverse("admin:academy_courseimport_publish", args=[record.pk]),
            "changelist_url": reverse("admin:academy_courseimport_changelist"),
        }
        return render(request, "academy/course_review.html", context)

    def asset_view(self, request, object_id, asset_id):
        if not self._is_authorized(request):
            return HttpResponseForbidden("Administrator course-content access is required.")
        record = get_object_or_404(CourseImport, pk=object_id)
        staged = record.assets.filter(stable_asset_id=asset_id).first()
        file_field = staged.file if staged and staged.file else None
        if not file_field:
            live = CourseMediaAsset.objects.filter(pk=asset_id).first()
            file_field = live.file if live and live.file else None
        if not file_field:
            from django.http import Http404
            raise Http404("This course image has not been uploaded yet.")
        response = FileResponse(
            file_field.open("rb"),
            content_type=mimetypes.guess_type(file_field.name)[0] or "application/octet-stream",
        )
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    def publish_view(self, request, object_id):
        if not self._is_authorized(request):
            return HttpResponseForbidden("Administrator course-content access is required.")
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        record = get_object_or_404(CourseImport, pk=object_id)
        try:
            course = publish_course_import(record, user=request.user)
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(request, message)
            return redirect("admin:academy_courseimport_review", object_id=record.pk)
        messages.success(request, f"{course.title} has been published from the reviewed import.")
        return redirect("admin:academy_course_changelist")


@admin.register(CourseImportAsset)
class CourseImportAssetAdmin(admin.ModelAdmin):
    form = CourseImportAssetAdminForm
    list_display = ("course_import", "asset_key", "source_error", "file", "updated_at")
    list_filter = ("course_import__status",)
    search_fields = ("course_import__title", "asset_key", "alt_text")
    readonly_fields = ("course_import", "asset_key", "stable_asset_id", "source_path", "original_url", "source_error", "created_at", "updated_at")
    fields = ("course_import", "asset_key", "stable_asset_id", "source_path", "original_url", "source_error", "alt_text", "file", "created_at", "updated_at")

    def has_module_permission(self, request):
        return is_course_content_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return is_course_content_admin(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return is_course_content_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        from apps.audit.models import AuditLog
        if obj.file:
            obj.source_error = ""
        super().save_model(request, obj, form, change)
        AuditLog.log(
            action="academy_course_import_asset_updated", user=request.user,
            object_type="course_import_asset", object_id=obj.pk,
            metadata={"course_import_id": str(obj.course_import_id), "asset_key": obj.asset_key},
        )


@admin.register(CourseMediaAsset)
class CourseMediaAssetAdmin(admin.ModelAdmin):
    form = CourseMediaAssetAdminForm
    list_display = ("course", "asset_key", "alt_text", "updated_at", "updated_by")
    list_filter = ("course",)
    search_fields = ("course__title", "asset_key", "alt_text")
    readonly_fields = ("course", "asset_key", "source_path", "original_url", "created_at", "updated_at", "updated_by")
    fields = ("course", "asset_key", "source_path", "original_url", "alt_text", "file", "created_at", "updated_at", "updated_by")

    def has_module_permission(self, request):
        return is_course_content_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return is_course_content_admin(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return is_course_content_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        from apps.audit.models import AuditLog
        old_name = ""
        if change:
            previous = CourseMediaAsset.objects.filter(pk=obj.pk).first()
            old_name = previous.file.name if previous and previous.file else ""
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        if change and old_name != (obj.file.name if obj.file else ""):
            AuditLog.log(
                action="academy_course_media_replaced", user=request.user,
                object_type="course_media_asset", object_id=obj.pk,
                metadata={"asset_key": obj.asset_key, "old_file": old_name, "new_file": obj.file.name if obj.file else ""},
            )


@admin.register(CourseContentRevision)
class CourseContentRevisionAdmin(admin.ModelAdmin):
    list_display = ("lesson", "revision_type", "editor", "created_at")
    list_filter = ("revision_type", "created_at")
    search_fields = ("lesson__title", "lesson__module__course__title")
    readonly_fields = tuple(field.name for field in CourseContentRevision._meta.fields)

    def has_module_permission(self, request):
        return is_course_content_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return is_course_content_admin(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
