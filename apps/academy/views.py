"""Authenticated learner views for TradeThesis Academy."""
import json

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST
from django.utils import timezone

from .models import Course, CourseEntitlement, CourseModule, CourseOffer, Lesson, LessonProgress
from .services import can_access_lesson, get_active_entitlement, lesson_release_at, update_lesson_progress


def _active_entitlements(user, at=None):
    at = at or timezone.now()
    return (CourseEntitlement.objects.filter(
        user=user, status=CourseEntitlement.Status.ACTIVE, starts_at__lte=at,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=at)))


@login_required
def academy_home(request):
    """Member landing page: purchased courses, progress, and available offers."""
    entitlements = list(_active_entitlements(request.user).select_related("course"))
    entitlements_by_course = {ent.course_id: ent for ent in entitlements}
    courses_by_id = {course.pk: course for course in Course.objects.filter(status=Course.Status.PUBLISHED)}
    for entitlement in entitlements:
        courses_by_id.setdefault(entitlement.course_id, entitlement.course)

    course_cards = []
    for course in sorted(courses_by_id.values(), key=lambda c: (c.position, c.title.casefold())):
        entitlement = entitlements_by_course.get(course.pk)
        total = Lesson.objects.filter(module__course=course, module__is_published=True, is_published=True).count()
        done = LessonProgress.objects.filter(
            user=request.user, lesson__module__course=course, status=LessonProgress.Status.COMPLETED,
        ).count()
        course_cards.append({
            "course": course, "entitlement": entitlement,
            "total_lessons": total, "completed_lessons": done,
            "progress_percent": round(done * 100 / total) if total else 0,
        })

    offers = CourseOffer.objects.filter(
        is_active=True, course__status=Course.Status.PUBLISHED,
        plan__is_active=True, plan__is_hidden=False,
    ).select_related("course", "plan").order_by("position", "name")
    offers = [o for o in offers if o.is_saleable and o.course_id not in entitlements_by_course]
    return render(request, "academy/home.html", {
        "course_cards": course_cards, "offers": offers,
    })


@login_required
def course_detail(request, course_slug):
    course = get_object_or_404(Course, slug=course_slug)
    entitlement = get_active_entitlement(request.user, course)
    if course.status != Course.Status.PUBLISHED and entitlement is None:
        from django.http import Http404
        raise Http404

    lesson_queryset = Lesson.objects.filter(is_published=True).order_by("position", "title")
    modules = CourseModule.objects.filter(course=course, is_published=True).order_by(
        "position", "title"
    ).prefetch_related(Prefetch("lessons", queryset=lesson_queryset))
    lesson_ids = [lesson.pk for module in modules for lesson in module.lessons.all()]
    progress_by_lesson = {
        p.lesson_id: p for p in LessonProgress.objects.filter(user=request.user, lesson_id__in=lesson_ids)
    }
    module_rows = []
    now = timezone.now()
    for module in modules:
        rows = []
        for lesson in module.lessons.all():
            allowed = can_access_lesson(request.user, lesson, now)
            release_at = lesson_release_at(entitlement, lesson) if entitlement else None
            rows.append({
                "lesson": lesson, "accessible": allowed,
                "progress": progress_by_lesson.get(lesson.pk),
                "release_at": release_at,
            })
        if rows:
            module_rows.append({"module": module, "lessons": rows})

    offer = CourseOffer.objects.filter(
        course=course, is_active=True, plan__is_active=True, plan__is_hidden=False,
    ).select_related("plan", "course").first()
    if offer is not None and not offer.is_saleable:
        offer = None
    completed = LessonProgress.objects.filter(
        user=request.user, lesson__module__course=course,
        status=LessonProgress.Status.COMPLETED,
    ).count()
    total = Lesson.objects.filter(module__course=course, module__is_published=True, is_published=True).count()
    return render(request, "academy/course_detail.html", {
        "course": course, "entitlement": entitlement, "offer": offer,
        "module_rows": module_rows, "completed_lessons": completed,
        "total_lessons": total,
        "progress_percent": round(completed * 100 / total) if total else 0,
    })


@login_required
def lesson_detail(request, course_slug, lesson_id):
    lesson = get_object_or_404(
        Lesson.objects.select_related("module__course"),
        pk=lesson_id, module__course__slug=course_slug,
        is_published=True, module__is_published=True,
    )
    course = lesson.module.course
    if not can_access_lesson(request.user, lesson):
        raise PermissionDenied("You do not have access to this lesson yet.")
    entitlement = get_active_entitlement(request.user, course)
    progress = LessonProgress.objects.filter(user=request.user, lesson=lesson).first()
    # Instructor materials are deliberately not added to this learner context.
    return render(request, "academy/lesson.html", {
        "course": course, "lesson": lesson,
        "module": lesson.module, "entitlement": entitlement, "progress": progress,
    })


@login_required
@require_POST
def lesson_progress(request, lesson_id):
    lesson = get_object_or_404(Lesson.objects.select_related("module__course"), pk=lesson_id)
    raw_response = request.POST.get("response_json", "")
    response_json = None
    if raw_response:
        try:
            response_json = json.loads(raw_response)
        except (TypeError, ValueError):
            return JsonResponse({"detail": "response_json must contain valid JSON."}, status=400)
    try:
        seconds_delta = int(request.POST.get("seconds_delta", "0"))
        progress = update_lesson_progress(
            user=request.user, lesson=lesson,
            status=request.POST.get("status") or None,
            response_json=response_json,
            seconds_delta=seconds_delta,
        )
    except PermissionDenied as exc:
        return JsonResponse({"detail": str(exc)}, status=403)
    except (ValidationError, ValueError) as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    return JsonResponse({
        "ok": True, "status": progress.status,
        "completed_at": progress.completed_at.isoformat() if progress.completed_at else None,
        "time_spent_seconds": progress.time_spent_seconds,
    })
