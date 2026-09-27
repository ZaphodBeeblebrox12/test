from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import services
from .models import Dispute


def _visible(user):
    return Dispute.objects.select_related(
        "payment_intent__user", "payment_intent__plan", "assigned_to")


@staff_member_required
def dashboard(request):
    qs = _visible(request.user)
    now = timezone.now()
    soon = now + timezone.timedelta(hours=72)
    live = [Dispute.Status.OPENED, Dispute.Status.EVIDENCE_PREP,
            Dispute.Status.READY_FOR_REVIEW]
    buckets = {
        "open": qs.filter(status__in=[Dispute.Status.OPENED,
                                      Dispute.Status.EVIDENCE_PREP]).count(),
        "needs_response": qs.filter(status__in=live).count(),
        "due_soon": qs.filter(evidence_due_at__lte=soon,
                              status__in=live).count(),
        "won": qs.filter(status=Dispute.Status.WON).count(),
        "lost": qs.filter(status=Dispute.Status.LOST).count(),
    }
    rows = qs.order_by("evidence_due_at")
    f = request.GET.get("f")
    if f == "due_soon":
        rows = rows.filter(evidence_due_at__lte=soon, status__in=live)
    elif f:
        rows = rows.filter(status=f)
    return render(request, "disputes/dashboard.html",
                  {"buckets": buckets, "rows": rows[:100], "filter": f,
                   "now": now})


@staff_member_required
def detail(request, pk):
    dispute = get_object_or_404(_visible(request.user), pk=pk)
    intent = dispute.payment_intent
    plan_type = None
    try:
        from .services import build_sections
        secs = build_sections(dispute)
        if "plan_type" in secs:
            plan_type = secs["plan_type"]
    except Exception:
        pass
    ctx = {
        "d": dispute, "intent": intent, "user": intent.user,
        "plan_type": plan_type,
        "now": timezone.now(),
        "timeline": services.build_timeline(dispute),
        "readiness": services.evaluate_readiness(dispute),
        "agreement": services.agreement_sentence(intent),
        "packages": dispute.packages.all(),
        "events": dispute.events.all(),
        "can_resolve": request.user.has_perm("disputes.change_dispute"),
    }
    if dispute.reason_category in (Dispute.ReasonCategory.FRAUD,
                                   Dispute.ReasonCategory.UNRECOGNIZED):
        ctx["ce3"] = services.ce3_matches(dispute)
    return render(request, "disputes/detail.html", ctx)


@require_POST
@permission_required("disputes.change_dispute", raise_exception=True)
def add_note(request, pk):
    d = get_object_or_404(Dispute, pk=pk)
    note = request.POST.get("note", "").strip()
    if note:
        services._log(d, "note", actor=request.user, note=note)
    return redirect("disputes:detail", pk=pk)


@require_POST
@permission_required("disputes.change_dispute", raise_exception=True)
def generate(request, pk):
    d = get_object_or_404(Dispute, pk=pk)
    pkg = services.generate_package(d, actor=request.user)
    messages.success(request, f"Package {str(pkg.pk)[:8]} generated "
                              f"({pkg.readiness['state']}).")
    return redirect("disputes:detail", pk=pk)


@require_POST
@permission_required("disputes.change_dispute", raise_exception=True)
def submit_latest(request, pk):
    d = get_object_or_404(Dispute, pk=pk)
    pkg = d.packages.first()
    if not pkg:
        messages.error(request, "Generate a package first.")
    else:
        services.submit_package(pkg, actor=request.user,
                                via=request.POST.get("via", "dashboard"),
                                reference=request.POST.get("reference", ""))
        messages.success(request, "Submission recorded. Submit the same "
                                  "content via the PSP dashboard/API.")
    return redirect("disputes:detail", pk=pk)


@require_POST
@permission_required("disputes.change_dispute", raise_exception=True)
def resolve(request, pk):
    d = get_object_or_404(Dispute, pk=pk)
    try:
        services.resolve_manually(d, request.POST.get("status"),
                                  actor=request.user,
                                  note=request.POST.get("note", ""))
        messages.success(request, f"Dispute marked {request.POST.get('status')}.")
    except (ValueError, TypeError) as exc:
        messages.error(request, str(exc))
    return redirect("disputes:detail", pk=pk)
