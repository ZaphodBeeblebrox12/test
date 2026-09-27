from django.contrib import admin

from .models import PolicyAcceptance, PolicyVersion
from .services import publish


@admin.register(PolicyVersion)
class PolicyVersionAdmin(admin.ModelAdmin):
    list_display = ("policy_type", "version", "title", "status",
                    "effective_from", "published_at", "created_by")
    list_filter = ("policy_type", "status")
    search_fields = ("version", "title")
    readonly_fields = ("id", "content_sha256", "published_at", "created_at")
    actions = ["publish_versions"]

    @admin.action(description="Publish selected version (serves new acceptances; "
                              "archives current active)")
    def publish_versions(self, request, queryset):
        for pv in queryset:
            publish(pv, actor=request.user)

    def has_delete_permission(self, request, obj=None):
        # Published versions are evidentiary; never delete.
        return False


@admin.register(PolicyAcceptance)
class PolicyAcceptanceAdmin(admin.ModelAdmin):
    list_display = ("user", "policy_version", "context", "accepted_at",
                    "ip_address")
    list_filter = ("policy_version__policy_type", "context")
    search_fields = ("user__username", "user__email")
    readonly_fields = [f.name for f in PolicyAcceptance._meta.fields]
    date_hierarchy = "accepted_at"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
