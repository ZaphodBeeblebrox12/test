"""Policy publishing lifecycle + acceptance recording."""
import hashlib
import logging

from django.conf import settings as dj_settings
from django.db import transaction
from django.utils import timezone

from .models import PolicyAcceptance, PolicyVersion

logger = logging.getLogger(__name__)

# Policies presented via ONE customer checkbox and recorded individually
# behind the scenes (granular backend evidence, minimal friction UX):
#   [x] I agree to the Terms of Service, including the Refund & Cancellation
#       Policy and Risk Disclosure.
# The refund type IS the combined "Refund & Cancellation Policy" document.
CHECKOUT_POLICY_TYPES = (
    PolicyVersion.PolicyType.TERMS,
    PolicyVersion.PolicyType.REFUND,
    PolicyVersion.PolicyType.RISK,
)


def publish(policy_version: PolicyVersion, actor=None) -> None:
    """DRAFT/ARCHIVED -> ACTIVE atomically (single active per type).

    Never edits content; only the status pointer moves. Sets published_at on
    first publication."""
    with transaction.atomic():
        PolicyVersion.objects.filter(
            policy_type=policy_version.policy_type,
            status=PolicyVersion.Status.ACTIVE,
        ).exclude(pk=policy_version.pk).update(
            status=PolicyVersion.Status.ARCHIVED)
        policy_version.status = PolicyVersion.Status.ACTIVE
        if policy_version.published_at is None:
            policy_version.published_at = timezone.now()
        policy_version.save(update_fields=["status", "published_at"])
    logger.info("policy published: %s by %s", policy_version, actor)


def active_versions(policy_types=CHECKOUT_POLICY_TYPES):
    """Map {policy_type: PolicyVersion} for the checkout flow."""
    return {pt: PolicyVersion.active(pt) for pt in policy_types}


def session_ref_for_request(request) -> str:
    key = request.session.session_key or ""
    salt = getattr(dj_settings, "RISK_DEVICE_SALT",
                   getattr(dj_settings, "SECRET_KEY", ""))
    return hashlib.sha256(f"{salt}:{key}".encode()).hexdigest()


def record_acceptance(user, policy_version: PolicyVersion, request=None,
                      context=PolicyAcceptance.Context.CHECKOUT,
                      checkout_evidence=None,
                      session_ref: str = "") -> PolicyAcceptance:
    """Write-once acceptance of an exact, immutable policy version."""
    ip, ua = "", ""
    if request is not None:
        ip = (request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
              or request.META.get("REMOTE_ADDR") or "")
        ua = request.META.get("HTTP_USER_AGENT", "")[:2000]
    if not session_ref and request is not None:
        session_ref = session_ref_for_request(request)
    return PolicyAcceptance.objects.create(
        user=user, policy_version=policy_version, context=context,
        accepted_at=timezone.now(),
        ip_address=ip or "0.0.0.0", user_agent=ua, session_ref=session_ref,
        checkout_evidence=checkout_evidence,
    )


def record_checkout_acceptances(user, request, checkout_evidence=None):
    """Record affirmative acceptance of ALL checkout policies at the agreement
    moment. Returns (acceptances, missing_types). Missing types are logged
    loudly - never fabricated."""
    acceptances, missing = [], []
    for pt in CHECKOUT_POLICY_TYPES:
        version = PolicyVersion.active(pt)
        if version is None:
            missing.append(pt)
            logger.error("No ACTIVE %s policy version - acceptance NOT recorded", pt)
            continue
        acceptances.append(record_acceptance(
            user, version, request,
            context=PolicyAcceptance.Context.CHECKOUT,
            checkout_evidence=checkout_evidence))
    return acceptances, missing
