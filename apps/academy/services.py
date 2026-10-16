"""Server-side course access, release timing, entitlement and progress rules."""
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    Course, CourseEntitlement, CourseEntitlementEvent, CourseOffer, Lesson, LessonProgress,
)


def get_active_entitlement(user, course, at=None):
    if not getattr(user, "is_authenticated", False):
        return None
    at = at or timezone.now()
    return (
        CourseEntitlement.objects.filter(
            user=user, course=course, status=CourseEntitlement.Status.ACTIVE, starts_at__lte=at
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=at))
        .select_related("course", "offer", "payment_intent").first()
    )


def lesson_release_at(entitlement, lesson):
    """Day 1 unlocks at grant time; day 2 unlocks 24 hours later."""
    return entitlement.starts_at + timedelta(days=max(lesson.release_day - 1, 0))


def can_access_lesson(user, lesson, at=None):
    """The authoritative lesson gate; hiding a link in the UI is not access control."""
    at = at or timezone.now()
    lesson = Lesson.objects.select_related("module__course").get(pk=lesson.pk)
    course = lesson.module.course
    if not lesson.is_published or not lesson.module.is_published or course.status == Course.Status.DRAFT:
        return False
    if lesson.is_preview and course.status == Course.Status.PUBLISHED:
        return True
    entitlement = get_active_entitlement(user, course, at)
    return bool(entitlement and lesson_release_at(entitlement, lesson) <= at)


def grant_course_entitlement(*, user, offer, payment_intent=None, granted_by=None,
                             source=CourseEntitlement.Source.PURCHASE, reason=""):
    """Grant course access; a repeat delivery of the same payment is a no-op.

    The unique user/course database constraint is the final duplicate-row guard.
    A permanent entitlement is never shortened by a later timed grant.
    """
    if not isinstance(offer, CourseOffer):
        raise TypeError("offer must be a CourseOffer")
    if source == CourseEntitlement.Source.PURCHASE:
        if payment_intent is None:
            raise ValidationError("A purchase grant requires a verified PaymentIntent.")
        if offer.plan_id and payment_intent.plan_id != offer.plan_id:
            raise ValidationError("PaymentIntent does not match this course offer.")
    if offer.course.status == Course.Status.DRAFT:
        raise ValidationError("Cannot grant access to a draft course.")
    now = timezone.now()
    requested_expiry = now + timedelta(days=offer.access_duration_days) if offer.access_duration_days else None

    with transaction.atomic():
        entitlement, created = CourseEntitlement.objects.get_or_create(
            user=user, course=offer.course,
            defaults={
                "offer": offer, "payment_intent": payment_intent, "source": source,
                "status": CourseEntitlement.Status.ACTIVE, "starts_at": now,
                "expires_at": requested_expiry, "granted_by": granted_by,
            },
        )
        if created:
            event_type = CourseEntitlementEvent.EventType.GRANTED
        else:
            entitlement = CourseEntitlement.objects.select_for_update().get(pk=entitlement.pk)
            if payment_intent and entitlement.payment_intent_id == payment_intent.pk and entitlement.has_access_at(now):
                return entitlement, False
            was_active = entitlement.has_access_at(now)
            if was_active and entitlement.expires_at is None:
                return entitlement, False
            if was_active:
                final_expiry = None if requested_expiry is None else max(entitlement.expires_at or now, requested_expiry)
                event_type = CourseEntitlementEvent.EventType.EXTENDED
            else:
                final_expiry = requested_expiry
                event_type = CourseEntitlementEvent.EventType.REINSTATED if entitlement.status == CourseEntitlement.Status.REVOKED else CourseEntitlementEvent.EventType.GRANTED
            entitlement.offer = offer
            entitlement.payment_intent = payment_intent
            entitlement.source = source
            entitlement.status = CourseEntitlement.Status.ACTIVE
            entitlement.starts_at = now if not was_active else entitlement.starts_at
            entitlement.expires_at = final_expiry
            entitlement.revoked_at = None
            entitlement.revocation_reason = ""
            entitlement.granted_by = granted_by
            entitlement.save(update_fields=[
                "offer", "payment_intent", "source", "status", "starts_at", "expires_at",
                "revoked_at", "revocation_reason", "granted_by", "updated_at",
            ])

        CourseEntitlementEvent.objects.create(
            entitlement=entitlement, payment_intent=payment_intent, actor=granted_by,
            event_type=event_type, reason=reason,
            details={"source": source, "offer_id": str(offer.pk),
                     "expires_at": entitlement.expires_at.isoformat() if entitlement.expires_at else None},
        )
        return entitlement, True


def revoke_course_entitlement_for_payment(payment_intent, *, reason):
    """Revoke only when this payment is the current grant source.

    A refund for an older purchase must not revoke a later valid repurchase.
    Call this only from the existing verified refund/chargeback service.
    """
    offer = CourseOffer.objects.filter(plan_id=payment_intent.plan_id).select_related("course").first()
    if offer is None:
        return False
    with transaction.atomic():
        entitlement = CourseEntitlement.objects.select_for_update().filter(
            user_id=payment_intent.user_id, course_id=offer.course_id,
            payment_intent_id=payment_intent.pk, status=CourseEntitlement.Status.ACTIVE,
        ).first()
        if entitlement is None:
            return False
        entitlement.status = CourseEntitlement.Status.REVOKED
        entitlement.revoked_at = timezone.now()
        entitlement.revocation_reason = reason[:240]
        entitlement.save(update_fields=["status", "revoked_at", "revocation_reason", "updated_at"])
        CourseEntitlementEvent.objects.create(
            entitlement=entitlement, payment_intent=payment_intent,
            event_type=CourseEntitlementEvent.EventType.REVOKED, reason=reason,
            details={"payment_intent_id": str(payment_intent.pk)},
        )
        return True


def update_lesson_progress(*, user, lesson, status=None, response_json=None, seconds_delta=0, at=None):
    """Persist progress only if the learner can access this lesson right now."""
    at = at or timezone.now()
    if not can_access_lesson(user, lesson, at):
        raise PermissionDenied("You do not have access to this lesson.")
    if seconds_delta < 0:
        raise ValidationError("seconds_delta cannot be negative.")
    if status is not None and status not in LessonProgress.Status.values:
        raise ValidationError("Invalid lesson progress status.")
    if response_json is not None and not isinstance(response_json, dict):
        raise ValidationError("response_json must be an object.")
    progress, _created = LessonProgress.objects.get_or_create(user=user, lesson=lesson)
    if status is not None:
        progress.status = status
        if status in (LessonProgress.Status.IN_PROGRESS, LessonProgress.Status.COMPLETED) and not progress.started_at:
            progress.started_at = at
        if status == LessonProgress.Status.COMPLETED:
            progress.completed_at = progress.completed_at or at
    if response_json is not None:
        progress.response_json = response_json
    progress.time_spent_seconds += seconds_delta
    progress.last_seen_at = at
    progress.save()
    return progress
