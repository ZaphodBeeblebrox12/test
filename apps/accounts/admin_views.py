"""Admin API: user ban/unban and staff approval.

Pre-existing debt: the User model had ban()/unban()/approve_staff() and the
audit model existed, but no HTTP layer or routes were ever wired. These views
implement the exact contract the tests define, reusing the existing model
methods and AuditLog.log() convention. Admin endpoints require role=ADMIN.
"""
from django.contrib.auth import get_user_model
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import AuditLog

User = get_user_model()


class IsRoleAdmin(permissions.BasePermission):
    """Require an authenticated user with role=ADMIN (the app's admin notion)."""

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and getattr(u, "role", None) == User.Role.ADMIN)


class BanUserView(APIView):
    """POST /api/admin/ban/  {user_id, reason?}"""
    permission_classes = [IsRoleAdmin]

    def post(self, request):
        user_id = request.data.get("user_id")
        reason = request.data.get("reason", "")
        if not user_id:
            return Response({"error": "user_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            target = User.objects.get(pk=user_id)
        except (User.DoesNotExist, ValueError):
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        if target.role == User.Role.ADMIN or target.is_superuser:
            return Response({"error": "Cannot ban admin users"}, status=status.HTTP_403_FORBIDDEN)
        target.ban(reason)
        AuditLog.log("user_banned", request.user, object_type="user", object_id=target.id,
                     metadata={"reason": reason})
        return Response({"status": "banned"}, status=status.HTTP_200_OK)


class UnbanUserView(APIView):
    """POST /api/admin/unban/  {user_id}"""
    permission_classes = [IsRoleAdmin]

    def post(self, request):
        user_id = request.data.get("user_id")
        if not user_id:
            return Response({"error": "user_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            target = User.objects.get(pk=user_id)
        except (User.DoesNotExist, ValueError):
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        target.unban()
        AuditLog.log("user_unbanned", request.user, object_type="user", object_id=target.id)
        return Response({"status": "unbanned"}, status=status.HTTP_200_OK)


class StaffApprovalListView(APIView):
    """GET /api/admin/staff-approvals/  -> staff with role=STAFF not yet approved."""
    permission_classes = [IsRoleAdmin]

    def get(self, request):
        pending = User.objects.filter(role=User.Role.STAFF, is_staff_approved=False)
        data = [{"id": str(u.id), "telegram_username": u.telegram_username,
                 "requested_at": u.date_joined.isoformat()} for u in pending]
        return Response(data, status=status.HTTP_200_OK)


class StaffApprovalActionView(APIView):
    """POST /api/admin/staff-approvals/action/  {user_id, action: approve|reject}"""
    permission_classes = [IsRoleAdmin]

    def post(self, request):
        user_id = request.data.get("user_id")
        action = request.data.get("action")
        if not user_id or action not in ("approve", "reject"):
            return Response({"error": "user_id and a valid action are required"},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            target = User.objects.get(pk=user_id)
        except (User.DoesNotExist, ValueError):
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

        if action == "approve":
            target.role = User.Role.STAFF
            target.is_staff = True
            target.is_staff_approved = True
            target.save(update_fields=["role", "is_staff", "is_staff_approved"])
            AuditLog.log("staff_approved", request.user, object_type="user", object_id=target.id)
            return Response({"status": "approved"}, status=status.HTTP_200_OK)

        # reject: demote back to a normal user
        target.role = User.Role.USER
        target.is_staff = False
        target.is_staff_approved = False
        target.save(update_fields=["role", "is_staff", "is_staff_approved"])
        AuditLog.log("staff_rejected", request.user, object_type="user", object_id=target.id)
        return Response({"status": "rejected"}, status=status.HTTP_200_OK)
