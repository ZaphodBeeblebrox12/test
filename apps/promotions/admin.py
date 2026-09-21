from django.contrib import admin

from .models import Coupon, CouponRedemption


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "discount_type", "percent_off", "amount_off_cents",
                    "campaign", "active", "valid_from", "valid_until", "max_redemptions"]
    list_filter = ["discount_type", "active", "campaign"]
    search_fields = ["code", "name"]
    readonly_fields = ["id", "created_at"]


@admin.register(CouponRedemption)
class CouponRedemptionAdmin(admin.ModelAdmin):
    """Redemption audit: read-only, no delete (refund/chargeback provenance)."""
    list_display = ["coupon", "user", "base_amount_cents", "discount_cents",
                    "final_amount_cents", "finalized", "created_at"]
    list_filter = ["finalized", "coupon"]
    search_fields = ["coupon__code", "user__username"]
    readonly_fields = [f.name for f in CouponRedemption._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
