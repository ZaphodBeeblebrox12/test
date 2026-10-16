from django.contrib import admin

from .models import (
    Course, CourseEntitlement, CourseEntitlementEvent, CourseModule, CourseOffer,
    Lesson, LessonInstructorMaterial, LessonProgress,
)


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
    readonly_fields = ("created_at", "updated_at")


@admin.register(CourseModule)
class CourseModuleAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "position", "is_published")
    list_filter = ("course", "is_published")
    search_fields = ("title", "course__title")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("created_at", "updated_at")


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ("title", "module", "format", "position", "release_day", "is_preview", "is_published")
    list_filter = ("format", "is_preview", "is_published", "module__course")
    search_fields = ("title", "summary", "module__title", "module__course__title")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("created_at", "updated_at")
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
