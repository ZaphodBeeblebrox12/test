from django.urls import path
from apps.accounts import debug_views
from apps.accounts.admin_views import (
    BanUserView, UnbanUserView, StaffApprovalListView, StaffApprovalActionView,
)

urlpatterns = [
    path("ban/", BanUserView.as_view(), name="admin_ban"),
    path("unban/", UnbanUserView.as_view(), name="admin_unban"),
    path("staff-approvals/", StaffApprovalListView.as_view(), name="admin_staff_approvals"),
    path("staff-approvals/action/", StaffApprovalActionView.as_view(), name="admin_staff_approval_action"),
    path("debug/users/", debug_views.debug_users_list, name="debug_users"),
]
