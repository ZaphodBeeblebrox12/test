"""Server-side course access, release timing, entitlement and progress rules."""
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    Course,
    CourseEntitlement,
    CourseEntitlementEvent,
    CourseOffer,
    Lesson,
    LessonProgress,
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
        if not offer.plan_id or payment_intent.plan_id != offer.plan_id:
            raise ValidationError("PaymentIntent does not match this course offer.")
        if payment_intent.status != "success":
            raise ValidationError("Course access can be granted only after verified payment.")
        if payment_intent.amount <= 0:
            raise ValidationError("A zero-value or invalid payment cannot grant course access.")
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
            # A duplicate event for the original purchase must not restore
            # access after that purchase was refunded or disputed.
            if payment_intent and entitlement.payment_intent_id == payment_intent.pk:
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


@transaction.atomic
def activate_course_payment(payment_intent, offer):
    """Atomically claim a verified one-time payment and grant course access."""
    from apps.payments.models import PaymentIntent

    if not offer.plan_id or payment_intent.plan_id != offer.plan_id:
        raise ValidationError("PaymentIntent does not match this course offer.")
    price_source = payment_intent.plan_price or payment_intent.geo_plan_price
    if price_source is None or getattr(price_source, "interval", None) != "one_time":
        raise ValidationError("A course offer requires an immutable one-time price snapshot.")

    claimed = PaymentIntent.objects.filter(
        pk=payment_intent.pk, status=PaymentIntent.Status.PENDING,
    ).update(status=PaymentIntent.Status.SUCCESS)
    if not claimed:
        # A successful earlier claim is terminal here. Never re-grant after a
        # refund/chargeback merely because a duplicate capture event arrived.
        entitlement = CourseEntitlement.objects.filter(
            user_id=payment_intent.user_id, course_id=offer.course_id,
        ).first()
        return False, entitlement

    payment_intent.refresh_from_db()
    entitlement, created = grant_course_entitlement(
        user=payment_intent.user, offer=offer, payment_intent=payment_intent,
        source=CourseEntitlement.Source.PURCHASE,
        reason="Verified one-time course payment",
    )
    if created:
        from apps.events.models import record_event
        record_event(
            "academy.entitlement_granted",
            dedupe_key=f"academy.entitlement_granted:{payment_intent.pk}",
            user_id=payment_intent.user_id,
            object_ref=f"course_entitlement:{entitlement.pk}",
            payload={"payment_intent_id": str(payment_intent.pk),
                     "course_id": str(offer.course_id),
                     "course_slug": offer.course.slug,
                     "entitlement_id": str(entitlement.pk)},
        )
        transaction.on_commit(lambda intent_id=payment_intent.pk: _notify_course_purchase(intent_id))
    return created, entitlement


def _notify_course_purchase(payment_intent_id):
    """Best-effort receipt after the payment/enrolment transaction commits."""
    try:
        from apps.payments.models import PaymentIntent
        from apps.payments.notifications import notify_payment_succeeded
        intent = PaymentIntent.objects.select_related("user", "plan").get(pk=payment_intent_id)
        notify_payment_succeeded(intent, subscription=None)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("course purchase receipt failed for PaymentIntent %s", payment_intent_id)
