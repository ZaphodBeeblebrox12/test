"""Public versioned policy pages (replace the static placeholders)."""
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .models import PolicyVersion


def _display(policy_type):
    return dict(PolicyVersion.PolicyType.choices).get(policy_type, policy_type)


@require_GET
def policy_page(request, policy_type: str):
    """Serve the ACTIVE version of a policy, listing all versions."""
    version = PolicyVersion.active(policy_type)
    versions = (PolicyVersion.objects
                .filter(policy_type=policy_type)
                .order_by("-effective_from", "-created_at"))
    return render(request, "policies/policy_detail.html", {
        "policy_type": policy_type,
        "version": version,
        "versions": versions,
        "policy_type_display": _display(policy_type),
    })


@require_GET
def policy_version_page(request, policy_type: str, version: str):
    """Serve a HISTORICAL version verbatim - exactly what the customer saw.
    Linked from the dispute admin ('Terms accepted') and version lists."""
    pv = PolicyVersion.objects.filter(policy_type=policy_type,
                                      version=version).first()
    return render(request, "policies/policy_detail.html", {
        "policy_type": policy_type,
        "version": pv,
        "versions": PolicyVersion.objects.filter(policy_type=policy_type)
                        .order_by("-effective_from"),
        "policy_type_display": _display(policy_type),
        "historical": True,
    })


# ── Named wrappers: keep legacy route names working (terms/refund/risk) ──
def terms_page(request):
    return policy_page(request, "terms")


def refund_page(request):
    return policy_page(request, "refund")


def risk_page(request):
    return policy_page(request, "risk")


def privacy_page(request):
    return policy_page(request, "privacy")
