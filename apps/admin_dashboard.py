"""P3d: lightweight operational dashboard on the Django admin index.

Purpose: when an admin opens Django Admin, immediately see whether anything
needs attention.  Read-only, based entirely on existing models, one aggregate
query per metric (no N+1, no caching layer, no new persistence).  Metrics the
current user lacks model permission for are omitted.  Rendered into the admin
index via templates/admin/dashboard.html.
"""
import datetime

from django.contrib import admin
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone



def _has_model_perm(request, app_label, model_name, perm="view"):
    return request.user.has_perm(f"{app_label}.{perm}_{model_name}")


def _changelist(app_label, model_name, query=""):
    base = reverse(f"admin:{app_label}_{model_name}_changelist")
    return f"{base}?{query}" if query else base


def _subscription_dashboard_context(request):
    from apps.bot_integration.models import BotAccessAudit
    from apps.jobs.models import Job
    from apps.payments.models import PaymentIntent
    from apps.subscriptions.models import Subscription
    """Permission-filtered metrics; each is one COUNT/aggregate query."""
    ctx = {}
    now = timezone.now()
    soon = now + datetime.timedelta(days=7)
    recent = now - datetime.timedelta(days=7)

    if _has_model_perm(request, "subscriptions", "subscription"):
        ctx["subscriptions"] = {
            "active": Subscription.objects.filter(
                is_active=True, status=Subscription.Status.ACTIVE).count(),
            "expiring_soon": Subscription.objects.filter(
                is_active=True, status=Subscription.Status.ACTIVE,
                expires_at__gt=now, expires_at__lte=soon).count(),
            "recently_expired": Subscription.objects.filter(
                status=Subscription.Status.EXPIRED,
                expires_at__gte=recent).count(),
            "recently_canceled": Subscription.objects.filter(
                canceled_at__gte=recent).count(),
            "links": {
                "expiring_soon": _changelist(
                    "subscriptions", "subscription",
                    "is_active__exact=1&status__exact=active"
                    "&expires_at__gte=&expires_at__lte="),
            },
        }
        # Trend: signups per day for the last 14 days (single grouped query).
        trend = (
            Subscription.objects.filter(
                started_at__gte=now - datetime.timedelta(days=14))
            .annotate(day=TruncDate("started_at"))
            .values("day").annotate(total=Count("id")).order_by("day")
        )
        ctx["recent_signups"] = list(trend)

    if _has_model_perm(request, "payments", "paymentintent"):
        ctx["payments"] = {
            "pending": PaymentIntent.objects.filter(
                status=PaymentIntent.Status.PENDING).count(),
            "failed": PaymentIntent.objects.filter(
                status=PaymentIntent.Status.FAILED).count(),
            "recent_success": PaymentIntent.objects.filter(
                status=PaymentIntent.Status.SUCCESS,
                created_at__gte=recent).count(),
            "links": {
                "pending": _changelist("payments", "paymentintent",
                                       "status__exact=pending"),
                "failed": _changelist("payments", "paymentintent",
                                      "status__exact=failed"),
            },
        }

    if _has_model_perm(request, "bot_integration", "botaccessaudit"):
        ctx["telegram"] = {
            "recent_failed": BotAccessAudit.objects.filter(
                status="failed", created_at__gte=recent).count(),
            "links": {
                "failed": _changelist("bot_integration", "botaccessaudit",
                                      "status__exact=failed"),
            },
        }

    if _has_model_perm(request, "jobs", "job"):
        ctx["jobs"] = {
            "pending": Job.objects.filter(status=Job.STATUS_PENDING).count(),
            "failed": Job.objects.filter(
                status=Job.STATUS_FAILED,
                updated_at__gte=recent).count(),
            "stalled": Job.objects.filter(
                attempts__gte=3,
                status__in=[Job.STATUS_RUNNING, Job.STATUS_PENDING]).count(),
            "links": {
                "failed": _changelist("jobs", "job", "status__exact=failed"),
            },
        }
    return ctx


def operational_dashboard(request):
    # Model imports are deferred to request time: this module is imported
    # from apps/__init__.py during app loading, before the registry is ready.
    return render(request, "admin/dashboard.html",
                  _subscription_dashboard_context(request))
