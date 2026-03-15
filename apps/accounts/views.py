"""
Account views for community platform.
"""
import hashlib
import hmac
import json
from datetime import datetime

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework import generics, permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User, UserPreference
from apps.accounts.serializers import (
    UserSerializer, UserProfileSerializer, UserPreferenceSerializer
)
from apps.audit.models import AuditLog


def check_banned(view_func):
    """Decorator to check if user is banned."""
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated and request.user.is_banned:
            return render(request, "accounts/banned.html", {
                "ban_reason": request.user.ban_reason
            })
        return view_func(request, *args, **kwargs)
    return wrapper


@method_decorator(login_required, name="dispatch")
@method_decorator(check_banned, name="dispatch")
class DashboardView(View):
    """User dashboard view."""
    
    def get(self, request):
        user = request.user
        
        # Get recent activity
        recent_activity = AuditLog.objects.filter(
            user=user
        ).order_by("-created_at")[:10]
        
        # Get recent notifications
        from apps.notifications.models import Notification
        recent_notifications = Notification.objects.filter(
            user=user
        ).order_by("-created_at")[:5]
        
        # Get unread notification count
        unread_count = Notification.objects.filter(
            user=user, is_read=False
        ).count()
        
        context = {
            "user": user,
            "telegram_connected": bool(user.telegram_id and user.telegram_verified),
            "recent_activity": recent_activity,
            "recent_notifications": recent_notifications,
            "unread_count": unread_count,
        }
        return render(request, "accounts/dashboard.html", context)


@method_decorator(login_required, name="dispatch")
@method_decorator(check_banned, name="dispatch")
class ProfileView(View):
    """User profile view and edit."""
    
    def get(self, request):
        return render(request, "accounts/profile.html", {
            "user": request.user,
            "telegram_connected": bool(request.user.telegram_id and request.user.telegram_verified),
        })
    
    def post(self, request):
        user = request.user
        
        # Update editable fields
        user.first_name = request.POST.get("first_name", user.first_name)
        user.last_name = request.POST.get("last_name", user.last_name)
        user.email = request.POST.get("email", user.email)
        user.bio = request.POST.get("bio", user.bio)
        
        # Handle avatar upload
        if "avatar" in request.FILES:
            user.avatar = request.FILES["avatar"]
        
        user.save()
        
        # Update preferences
        pref, _ = UserPreference.objects.get_or_create(user=user)
        pref.timezone = request.POST.get("timezone", pref.timezone)
        pref.language = request.POST.get("language", pref.language)
        pref.save()
        
        # Log the update
        AuditLog.log(
            action="profile_updated",
            user=user,
            object_type="user",
            object_id=user.id,
            metadata={"fields_updated": ["first_name", "last_name", "email", "bio", "avatar", "timezone", "language"]}
        )
        
        return redirect("profile")


@method_decorator(login_required, name="dispatch")
@method_decorator(check_banned, name="dispatch")
class ActivityLogView(View):
    """User activity log view."""
    
    def get(self, request):
        activities = AuditLog.objects.filter(
            user=request.user
        ).order_by("-created_at")
        
        return render(request, "accounts/activity.html", {
            "activities": activities
        })


@method_decorator(login_required, name="dispatch")
@method_decorator(check_banned, name="dispatch")
class NotificationsView(View):
    """User notifications view."""
    
    def get(self, request):
        from apps.notifications.models import Notification
        notifications = Notification.objects.filter(
            user=request.user
        ).order_by("-created_at")
        
        return render(request, "accounts/notifications.html", {
            "notifications": notifications
        })


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def telegram_connect(request):
    """
    Connect Telegram account to user profile.
    Verifies Telegram widget hash and stores telegram_id.
    """
    user = request.user
    
    data = request.data
    
    # Required fields from Telegram widget
    check_hash = data.get("hash")
    telegram_id = data.get("id")
    username = data.get("username", "")
    
    if not check_hash or not telegram_id:
        return Response(
            {"error": "Missing required fields"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Verify Telegram hash
    bot_token = settings.TELEGRAM_BOT_TOKEN
    if not bot_token:
        return Response(
            {"error": "Telegram bot not configured"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    
    # Create data_check_string
    data_fields = []
    for key in ["auth_date", "first_name", "id", "last_name", "photo_url", "username"]:
        if key in data and data[key]:
            data_fields.append(f"{key}={data[key]}")
    data_fields.sort()
    data_check_string = chr(10).join(data_fields)
    
    # Calculate secret key
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    
    # Calculate hash
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256
    ).hexdigest()
    
    if calculated_hash != check_hash:
        return Response(
            {"error": "Invalid Telegram hash"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Check if telegram_id is already connected to another user
    existing_user = User.objects.filter(
        telegram_id=telegram_id
    ).exclude(id=user.id).first()
    
    if existing_user:
        return Response(
            {"error": "This Telegram account is already connected to another user"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Store Telegram info
    user.telegram_id = telegram_id
    user.telegram_username = username
    user.telegram_verified = True
    user.save()
    
    # Log the connection
    AuditLog.log(
        action="telegram_connected",
        user=user,
        object_type="user",
        object_id=user.id,
        metadata={"telegram_id": telegram_id, "telegram_username": username}
    )
    
    return Response({
        "success": True,
        "telegram_id": telegram_id,
        "telegram_username": username,
        "telegram_verified": True
    })


class UserMeAPIView(APIView):
    """Get current user info."""
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)


class UserProfileAPIView(APIView):
    """Get or update user profile."""
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request):
        serializer = UserProfileSerializer(request.user)
        return Response(serializer.data)
    
    def patch(self, request):
        user = request.user
        
        # Update user fields
        allowed_fields = ["first_name", "last_name", "email", "bio"]
        for field in allowed_fields:
            if field in request.data:
                setattr(user, field, request.data[field])
        
        # Handle avatar
        if "avatar" in request.FILES:
            user.avatar = request.FILES["avatar"]
        
        user.save()
        
        # Update preferences
        pref_fields = ["timezone", "language", "notifications_enabled"]
        pref, _ = UserPreference.objects.get_or_create(user=user)
        for field in pref_fields:
            if field in request.data:
                setattr(pref, field, request.data[field])
        pref.save()
        
        # Log update
        AuditLog.log(
            action="profile_updated",
            user=user,
            object_type="user",
            object_id=user.id,
            metadata={"source": "api"}
        )
        
        serializer = UserProfileSerializer(user)
        return Response(serializer.data)


class UserActivityAPIView(APIView):
    """Get user activity log."""
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request):
        activities = AuditLog.objects.filter(
            user=request.user
        ).order_by("-created_at")[:50]
        
        data = [{
            "id": str(a.id),
            "action": a.action,
            "object_type": a.object_type,
            "object_id": a.object_id,
            "metadata": a.metadata,
            "created_at": a.created_at.isoformat(),
        } for a in activities]
        
        return Response(data)
