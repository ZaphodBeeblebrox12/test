"""
Debug views for accounts.
"""
from django.http import JsonResponse
from django.contrib.admin.views.decorators import staff_member_required

from apps.accounts.models import User


@staff_member_required
def debug_users_list(request):
    """List all users for debugging."""
    users = User.objects.all().values(
        'id', 'username', 'email', 'telegram_id', 'telegram_username',
        'is_banned', 'is_staff', 'is_superuser', 'date_joined'
    )[:100]
    return JsonResponse({
        'users': list(users),
        'count': User.objects.count()
    })
