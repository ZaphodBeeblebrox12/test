"""Product admin extension.

Wire-up: add ONE line at the end of apps/subscriptions/admin.py:

    from . import admin_product_extension  # noqa: F401  (registers Product)

Keeping this in a separate module means the patch does not need to rewrite
your existing admin.py.
"""
from django.contrib import admin

from .models import Plan, Product


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "display_order", "plan_count")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("display_order", "name")

    @admin.display(description="Plans")
    def plan_count(self, obj):
        return obj.plans.count()


def _extend_plan_admin(plan_admin):
    """Surface product on the Plan changelist AND its change form.

    The existing PlanAdmin uses explicit fieldsets; a field missing from
    every fieldset never renders on the add/change form, so we inject
    'product' into the fieldset that holds 'tier' (creating a sensible
    fallback if no fieldset contains it).
    """
    # changelist
    if "product" not in getattr(plan_admin, "list_display", ()):
        plan_admin.list_display = tuple(
            getattr(plan_admin, "list_display", ("name",))) + ("product",)
    if "product" not in getattr(plan_admin, "list_filter", ()):
        plan_admin.list_filter = tuple(
            getattr(plan_admin, "list_filter", ())) + ("product",)

    # change form (fieldsets)
    fieldsets = getattr(plan_admin, "fieldsets", None)
    if not fieldsets:
        # no explicit fieldsets -> the form shows all model fields already,
        # but make it explicit and grouped anyway
        plan_admin.fieldsets = (
            ("Plan information", {
                "fields": ("product", "tier", "name", "description",
                           "description_html", "is_active", "display_order"),
            }),
        )
        return
    new_fieldsets = []
    placed = False
    for title, opts in fieldsets:
        opts = dict(opts)
        fields = list(opts.get("fields", ()))
        flat = [f for f in fields if isinstance(f, str)]
        if not placed and ("tier" in flat or "name" in flat):
            # insert product just before tier/name for a natural layout
            anchor = "tier" if "tier" in flat else "name"
            idx = fields.index(anchor)
            fields.insert(idx, "product")
            placed = True
        opts["fields"] = tuple(fields)
        new_fieldsets.append((title, opts))
    if not placed:
        # tier/name not in any fieldset (unexpected): prepend a section
        new_fieldsets.insert(0, ("Product", {"fields": ("product",)}))
    plan_admin.fieldsets = tuple(new_fieldsets)


# Mutate the already-registered PlanAdmin in place -- no admin.py rewrite.
try:
    _plan_admin = admin.site._registry.get(Plan)
    if _plan_admin is not None:
        _extend_plan_admin(_plan_admin)
except Exception:
    pass
