from django.contrib import admin

from .models import (
    ContentBlock, Delivery, MediaAsset, Template, TemplateVersion, VariableSpec)


class TemplateVersionInline(admin.StackedInline):
    model = TemplateVersion
    extra = 0
    fields = ("version_number", "editor_mode", "subject", "preview_text",
              "html", "plain_text", "visual_blocks")
    readonly_fields = ("created_at",)


@admin.register(Template)
class TemplateAdmin(admin.ModelAdmin):
    list_display = ["name", "kind", "created_at"]
    list_filter = ["kind"]
    search_fields = ["name"]
    inlines = [TemplateVersionInline]


@admin.register(TemplateVersion)
class TemplateVersionAdmin(admin.ModelAdmin):
    """Immutable once created: read-only (versions are snapshots)."""
    list_display = ["template", "version_number", "editor_mode", "subject", "created_at"]
    list_filter = ["editor_mode", "template"]
    search_fields = ["subject", "template__name"]
    readonly_fields = [f.name for f in TemplateVersion._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(VariableSpec)
class VariableSpecAdmin(admin.ModelAdmin):
    list_display = ["name", "default_value", "preview_value", "required"]
    search_fields = ["name"]


@admin.register(MediaAsset)
class MediaAssetAdmin(admin.ModelAdmin):
    list_display = ["alt_text", "public_url", "width", "height", "created_at"]
    search_fields = ["alt_text", "public_url"]


@admin.register(ContentBlock)
class ContentBlockAdmin(admin.ModelAdmin):
    list_display = ["name", "block_type", "created_at"]
    list_filter = ["block_type"]
    search_fields = ["name"]


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    """Delivery records: read-only audit (idempotency/deliverability evidence)."""
    list_display = ["to_email", "kind", "state", "provider", "created_at", "sent_at"]
    list_filter = ["state", "kind", "provider"]
    search_fields = ["to_email", "idempotency_key", "provider_message_id"]
    readonly_fields = [f.name for f in Delivery._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
