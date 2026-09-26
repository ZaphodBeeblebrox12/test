"""Admin review queue for support tickets."""
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect

from .models import AffiliateLink, SupportTicket, TicketAttachment
from .services import approve_ticket, reject_ticket


@admin.register(AffiliateLink)
class AffiliateLinkAdmin(admin.ModelAdmin):
    list_display = ["name", "referral_code", "plan", "is_active", "created_at"]
    list_filter = ["is_active", "plan"]
    search_fields = ["name", "referral_code", "url"]
    autocomplete_fields = ["plan"]


class TicketAttachmentInline(admin.TabularInline):
    model = TicketAttachment
    extra = 0
    can_delete = False
    readonly_fields = ["file_link", "created_at"]

    def file_link(self, obj):
        if obj.file:
            return obj.file.url
        return "-"
    file_link.short_description = "Screenshot"


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ["short_id", "user", "category", "affiliate_link", "plan",
                    "status", "created_at", "reviewed_by"]
    list_filter = ["category", "status", "created_at", "affiliate_link"]
    search_fields = ["user__username", "user__email", "message", "admin_notes"]
    list_select_related = ["user", "plan", "affiliate_link", "reviewed_by"]
    readonly_fields = ["user", "category", "affiliate_link", "message", "status",
                       "created_at", "updated_at", "reviewed_by", "reviewed_at"]
    inlines = [TicketAttachmentInline]
    change_form_template = "admin/support/supportticket/change_form.html"

    fieldsets = (
        ("Ticket", {
            "fields": (("user", "created_at"), "category", "affiliate_link", "message"),
        }),
        ("Review", {
            "fields": ("plan", "status", "admin_notes",
                       ("reviewed_by", "reviewed_at")),
        }),
    )

    actions = ["approve_tickets", "reject_tickets"]

    # --- permission: staff see the queue; per-ticket reviewer rules apply ---
    def has_view_permission(self, request, obj=None):
        return request.user.is_staff

    def has_module_permission(self, request):
        return request.user.is_staff

    def has_change_permission(self, request, obj=None):
        if not request.user.is_staff:
            return False
        if obj is None:
            return True
        return obj.can_be_reviewed_by(request.user)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "plan":
            from apps.subscriptions.models import Plan
            kwargs["queryset"] = Plan.objects.filter(is_hidden=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    # --- review buttons on the change form ---
    def change_view(self, request, object_id, form_url="", extra_context=None):
        ticket = self.get_object(request, object_id)
        if ticket and not ticket.can_be_reviewed_by(request.user):
            raise PermissionDenied
        if request.method == "POST" and ticket is not None:
            if "_approve_grant" in request.POST:
                return self._approve(request, ticket)
            if "_reject" in request.POST:
                return self._reject(request, ticket)
        return super().change_view(request, object_id, form_url, extra_context)

    def _approve(self, request, ticket):
        from apps.subscriptions.models import Plan
        plan = None
        plan_id = request.POST.get("plan")
        if plan_id:
            plan = Plan.objects.filter(pk=plan_id, is_hidden=True).first()
            if plan is None:
                messages.error(request, "Pick a valid hidden plan.")
                return HttpResponseRedirect(request.path)
        if ticket.is_access_request and plan is None:
            messages.error(request, "Assign a plan before approving an access request.")
            return HttpResponseRedirect(request.path)
        try:
            approve_ticket(ticket, plan, request.user)
            if ticket.is_access_request:
                messages.success(request, f"Approved - {ticket.user} granted {plan.name}.")
            else:
                messages.success(request, "Ticket marked resolved; user notified.")
        except (ValueError, PermissionError) as e:
            messages.error(request, str(e))
        return HttpResponseRedirect(request.path)

    def _reject(self, request, ticket):
        notes = request.POST.get("admin_notes", "")
        try:
            reject_ticket(ticket, request.user, notes)
            messages.success(request, "Ticket closed; user notified.")
        except (ValueError, PermissionError) as e:
            messages.error(request, str(e))
        return HttpResponseRedirect(request.path)

    # --- bulk actions (list) ---
    @admin.action(description="Approve selected tickets (plan required for access requests)")
    def approve_tickets(self, request, queryset):
        for ticket in queryset.select_related("plan"):
            if ticket.is_access_request and ticket.plan_id is None:
                messages.warning(request, f"{ticket.short_id}: no plan assigned - skipped.")
                continue
            try:
                approve_ticket(ticket, ticket.plan, request.user)
            except (ValueError, PermissionError) as e:
                messages.warning(request, f"{ticket.short_id}: {e}")
        messages.success(request, "Approval pass complete.")

    @admin.action(description="Reject selected tickets (admin notes required)")
    def reject_tickets(self, request, queryset):
        for ticket in queryset:
            try:
                reject_ticket(ticket, request.user, ticket.admin_notes)
            except (ValueError, PermissionError) as e:
                messages.warning(request, f"{ticket.short_id}: {e}")
        messages.success(request, "Rejection pass complete.")
