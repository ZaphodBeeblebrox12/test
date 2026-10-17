"""Authenticated learner views for TradeThesis Academy."""
import json
import mimetypes

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Prefetch, Q
from django.http import FileResponse, JsonResponse
from django.urls import reverse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (
    Course,
    CourseEntitlement,
    CourseMediaAsset,
    CourseModule,
    CourseOffer,
    Lesson,
    LessonProgress,
)
from .content_rendering import build_lesson_sections
from .permissions import is_course_content_admin
from .services import (
    can_access_lesson,
    get_active_entitlement,
    lesson_release_at,
    update_lesson_progress,
)


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
        plan__is_active=True,
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
        course=course, is_active=True, plan__is_active=True,
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
    asset_ids = lesson.content_json.get("asset_ids", []) if isinstance(lesson.content_json, dict) else []
    media_assets = CourseMediaAsset.objects.filter(course=course, pk__in=asset_ids, file__gt="")
    asset_urls = {
        str(asset.pk): reverse("academy:media-asset", args=[asset.pk])
        for asset in media_assets
    }
    outline_modules = []
    modules = CourseModule.objects.filter(course=course).prefetch_related("lessons").order_by("position", "title")
    now = timezone.now()
    for module_row in modules:
        rows = []
        for outline_lesson in module_row.lessons.all():
            if not outline_lesson.is_published:
                continue
            rows.append({
                "lesson": outline_lesson,
                "accessible": can_access_lesson(request.user, outline_lesson, now),
                "current": outline_lesson.pk == lesson.pk,
                "progress": LessonProgress.objects.filter(user=request.user, lesson=outline_lesson).first(),
            })
        if rows and module_row.is_published:
            outline_modules.append({"module": module_row, "lessons": rows})
    notebook = lesson.content_json.get("notebook", {}) if isinstance(lesson.content_json, dict) else {}
    return render(request, "academy/lesson.html", {
        "course": course, "lesson": lesson, "module": lesson.module,
        "entitlement": entitlement, "progress": progress,
        "lesson_sections": build_lesson_sections(lesson.content_markdown, asset_urls),
        "notebook": notebook, "outline_modules": outline_modules,
    })


@login_required
def media_asset(request, asset_id):
    """Deliver teaching images only to admins or learners allowed to view a referencing lesson."""
    asset = get_object_or_404(CourseMediaAsset.objects.select_related("course"), pk=asset_id)
    if not asset.file:
        from django.http import Http404
        raise Http404
    allowed = is_course_content_admin(request.user)
    if not allowed:
        for related_lesson in Lesson.objects.filter(
            module__course=asset.course, module__is_published=True, is_published=True,
        ).select_related("module__course"):
            asset_ids = related_lesson.content_json.get("asset_ids", []) if isinstance(related_lesson.content_json, dict) else []
            if str(asset.pk) in asset_ids and can_access_lesson(request.user, related_lesson):
                allowed = True
                break
    if not allowed:
        raise PermissionDenied("You do not have access to this course image.")
    response = FileResponse(
        asset.file.open("rb"),
        content_type=mimetypes.guess_type(asset.file.name)[0] or "application/octet-stream",
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


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
