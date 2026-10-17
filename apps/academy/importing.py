"""Manifest validation, idempotent staging, review checks and atomic publishing."""
import hashlib
import json
import re
import uuid
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files import File
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from PIL import Image, UnidentifiedImageError

from apps.audit.models import AuditLog

from .content_rendering import validate_markdown_source
from .models import (
    Course,
    CourseContentRevision,
    CourseImport,
    CourseImportAsset,
    CourseMediaAsset,
    CourseModule,
    Lesson,
)

User = get_user_model()
_LESSON_HEADING = re.compile(
    r"^##\s+Lesson\s+(?P<source_id>\d+\.\d+)\s*[—–-]\s*(?P<title>.+?)\s*$",
    re.MULTILINE,
)
_IMAGE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<path>[^)\s]+)(?:\s+[^)]*)?\)")
_ALLOWED_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
_MAX_IMAGE_BYTES = 10 * 1024 * 1024


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _stable_asset_id(course_slug, asset_key):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "tradethesis-course:" + course_slug + ":" + asset_key))


def _safe_source_path(package_root, source, label):
    if not isinstance(source, str) or not source.strip():
        raise ValueError(f"{label} must be a non-empty relative path.")
    path = (package_root / source).resolve()
    if path != package_root.resolve() and package_root.resolve() not in path.parents:
        raise ValueError(f"{label} escapes the course package: {source}")
    if not path.is_file():
        raise ValueError(f"{label} does not exist: {source}")
    return path


def _split_module_markdown(source, source_path):
    matches = list(_LESSON_HEADING.finditer(source))
    if not matches:
        raise ValueError(f"No headings such as '## Lesson 1.1 — Title' found in {source_path}.")
    lessons = {}
    for index, match in enumerate(matches):
        source_id = match.group("source_id")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        body = source[match.end():end].strip()
        if source_id in lessons:
            raise ValueError(f"Duplicate source lesson identifier {source_id} in {source_path}.")
        if not body:
            raise ValueError(f"Lesson {source_id} in {source_path} has no content.")
        lessons[source_id] = {"title": match.group("title").strip(), "content": body}
    return lessons


def _inspect_image(path):
    if not path.is_file():
        return None, "source image is missing"
    if path.stat().st_size > _MAX_IMAGE_BYTES:
        return None, "source image exceeds 10 MB"
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image_format = image.format
            image.load()
        if image_format not in _ALLOWED_FORMATS:
            return None, "unsupported image format; export it as JPEG, PNG or WebP"
        return {"format": image_format, "sha256": _sha(path.read_bytes())}, None
    except (UnidentifiedImageError, OSError, ValueError):
        return None, "file is not a valid supported image"


def inspect_manifest(manifest_path):
    """Read the canonical manifest and authored Markdown without changing Django."""
    manifest_path = Path(manifest_path).resolve()
    if not manifest_path.is_file():
        raise ValueError(f"Manifest not found: {manifest_path}")
    package_root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported manifest schema_version; expected 1.")
    course = manifest.get("course")
    modules_manifest = manifest.get("modules")
    if not isinstance(course, dict) or not isinstance(modules_manifest, list) or not modules_manifest:
        raise ValueError("Manifest requires a course object and a non-empty modules list.")

    required_course = ("title", "slug")
    for field in required_course:
        if not str(course.get(field, "")).strip():
            raise ValueError(f"Manifest course.{field} is required.")
    if slugify(course["slug"]) != course["slug"]:
        raise ValueError("course.slug must be a stable lowercase slug.")

    payload = {
        "schema_version": 1,
        "course": {
            "title": str(course["title"]).strip(),
            "slug": course["slug"],
            "summary": str(course.get("summary", "")).strip(),
            "description": str(course.get("description", "")).strip(),
            "position": int(course.get("position", 0)),
        },
        "notebook": manifest.get("notebook") or {
            "title": "Your Trading Notebook",
            "prompts": [
                {"label": "Observe", "text": "Describe the evidence visible in the chart."},
                {"label": "Interpret", "text": "State one possible reading and a reasonable alternative."},
                {"label": "Test", "text": "Name the next evidence that would strengthen or weaken your reading."},
            ],
        },
        "modules": [],
        "assets": [],
        "manifest_version": str(manifest.get("manifest_version", "1")),
    }
    warnings = []
    asset_sources = {}
    asset_records = {}
    seen_module_slugs = set()
    seen_module_positions = set()
    seen_lesson_slugs = set()
    seen_lesson_ids = set()

    for module_index, module in enumerate(modules_manifest, start=1):
        if not isinstance(module, dict):
            raise ValueError(f"Module entry {module_index} must be an object.")
        module_slug = str(module.get("slug", "")).strip()
        module_title = str(module.get("title", "")).strip()
        module_position = int(module.get("position", module_index))
        if not module_slug or slugify(module_slug) != module_slug or not module_title:
            raise ValueError(f"Module {module_index} needs a title and stable lowercase slug.")
        if module_slug in seen_module_slugs or module_position in seen_module_positions:
            raise ValueError(f"Duplicate module slug or position: {module_slug} / {module_position}.")
        seen_module_slugs.add(module_slug)
        seen_module_positions.add(module_position)

        source_path = _safe_source_path(package_root, module.get("source"), f"module {module_slug} source")
        source_text = source_path.read_text(encoding="utf-8")
        source_lessons = _split_module_markdown(source_text, source_path)
        lessons_manifest = module.get("lessons")
        if not isinstance(lessons_manifest, list) or not lessons_manifest:
            raise ValueError(f"Module {module_slug} must declare its lesson list.")

        module_payload = {
            "slug": module_slug,
            "title": module_title,
            "summary": str(module.get("summary", "")).strip(),
            "position": module_position,
            "lessons": [],
        }
        seen_lesson_positions = set()
        for lesson_index, item in enumerate(lessons_manifest, start=1):
            source_id = str(item.get("source_id", item.get("id", ""))).strip()
            if source_id not in source_lessons:
                raise ValueError(f"Lesson source id {source_id!r} is missing from {source_path}.")
            lesson_title = str(item.get("title", source_lessons[source_id]["title"])).strip()
            if lesson_title != source_lessons[source_id]["title"]:
                raise ValueError(
                    f"Title mismatch for {source_id}: manifest says {lesson_title!r}, "
                    f"Markdown says {source_lessons[source_id]['title']!r}."
                )
            lesson_slug = str(item.get("slug", "")).strip()
            if not lesson_slug or slugify(lesson_slug) != lesson_slug:
                raise ValueError(f"Lesson {source_id} needs a stable lowercase slug.")
            lesson_position = int(item.get("position", lesson_index))
            lesson_key = (module_slug, lesson_slug)
            global_lesson_key = (module_slug, source_id)
            if lesson_key in seen_lesson_slugs or global_lesson_key in seen_lesson_ids:
                raise ValueError(f"Duplicate lesson slug or source id: {lesson_key}.")
            if lesson_position in seen_lesson_positions:
                raise ValueError(f"Duplicate lesson position in module {module_slug}.")
            seen_lesson_slugs.add(lesson_key)
            seen_lesson_ids.add(global_lesson_key)
            seen_lesson_positions.add(lesson_position)

            content = source_lessons[source_id]["content"]

            def replace_image(match, source_id=source_id, source_path=source_path):
                alt = match.group("alt").strip() or "Course figure"
                raw_path = match.group("path").strip()
                if re.match(r"^https?://", raw_path, re.IGNORECASE):
                    warnings.append(f"{source_id}: external image reference is not imported: {raw_path}")
                    return "**Image requires a course-owned asset: " + alt.replace("**", "") + "**"
                asset_path = (source_path.parent / raw_path).resolve()
                if asset_path != package_root.resolve() and package_root.resolve() not in asset_path.parents:
                    raise ValueError(f"Image reference escapes course package in lesson {source_id}: {raw_path}")
                asset_key = asset_path.relative_to(package_root.resolve()).as_posix()
                image_info, image_error = _inspect_image(asset_path)
                stable_id = _stable_asset_id(course["slug"], asset_key)
                if asset_key not in asset_records:
                    asset_records[asset_key] = {
                        "asset_key": asset_key,
                        "stable_asset_id": stable_id,
                        "alt_text": alt,
                        "source_path": asset_key,
                        "available_in_source": bool(image_info),
                        "source_error": image_error or "",
                        "sha256": image_info["sha256"] if image_info else "",
                    }
                    if image_info:
                        asset_sources[asset_key] = asset_path
                    else:
                        warnings.append(f"{source_id}: {asset_key}: {image_error or 'asset unavailable'}")
                elif not asset_records[asset_key]["alt_text"] and alt:
                    asset_records[asset_key]["alt_text"] = alt
                return "![" + alt.replace("]", "") + "](asset://" + stable_id + ")"

            content = _IMAGE.sub(replace_image, content)
            validate_markdown_source(content)
            asset_ids = sorted({
                match.group(1) for match in re.finditer(r"asset://([0-9a-fA-F-]{36})", content)
            })
            notebook = item.get("notebook") or payload["notebook"]
            module_payload["lessons"].append({
                "source_id": source_id,
                "slug": lesson_slug,
                "title": lesson_title,
                "summary": str(item.get("summary", "")).strip(),
                "position": lesson_position,
                "duration_minutes": max(1, int(item.get("duration_minutes", 15))),
                "release_day": max(1, int(item.get("release_day", 1))),
                "is_preview": bool(item.get("is_preview", False)),
                "format": str(item.get("format", "reading")),
                "content_markdown": content.strip(),
                "content_json": {"schema_version": 1, "source_id": source_id, "asset_ids": asset_ids, "notebook": notebook},
            })
        extra_sources = sorted(set(source_lessons) - {str(item.get("source_id", item.get("id", ""))) for item in lessons_manifest})
        if extra_sources:
            raise ValueError(f"Module {module_slug} has Markdown lessons missing from the manifest: {', '.join(extra_sources)}.")
        payload["modules"].append(module_payload)

    payload["assets"] = sorted(asset_records.values(), key=lambda item: item["asset_key"])
    # Include asset hashes in the content hash. Files uploaded later to resolve placeholders do not mutate source identity.
    return payload, asset_sources, warnings


def stage_course_import(*, manifest_path, payload, asset_sources, warnings, imported_by=None):
    """Persist a review-only package. It does not touch published Course/Module/Lesson rows."""
    manifest_path = Path(manifest_path).resolve()
    content_hash = _sha(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    course_meta = payload["course"]
    with transaction.atomic():
        record = CourseImport.objects.filter(course_slug=course_meta["slug"], content_hash=content_hash).first()
        if record is not None:
            return record, False, warnings
        CourseImport.objects.filter(
            course_slug=course_meta["slug"], status=CourseImport.Status.REVIEW
        ).update(status=CourseImport.Status.SUPERSEDED)
        record = CourseImport.objects.create(
            course_slug=course_meta["slug"],
            title=course_meta["title"],
            manifest_version=payload["manifest_version"],
            content_hash=content_hash,
            source_manifest=str(manifest_path),
            payload=payload,
            status=CourseImport.Status.REVIEW,
            imported_by=imported_by,
        )
        stage_warnings = list(warnings)
        for spec in payload["assets"]:
            asset = CourseImportAsset.objects.create(
                course_import=record,
                asset_key=spec["asset_key"],
                stable_asset_id=spec["stable_asset_id"],
                alt_text=spec["alt_text"],
                source_path=spec["source_path"],
                source_error=spec["source_error"],
            )
            source_file = asset_sources.get(spec["asset_key"])
            if source_file:
                ext = source_file.suffix.lower()
                safe_ext = _ALLOWED_FORMATS.get(Image.open(source_file).format)
                if not safe_ext or ext not in {safe_ext, ".jpeg" if safe_ext == ".jpg" else safe_ext}:
                    asset.source_error = "Source file extension does not match its decoded image format."
                    asset.save(update_fields=["source_error"])
                    stage_warnings.append(spec["asset_key"] + ": filename extension does not match the decoded image.")
                    continue
                with source_file.open("rb") as stream:
                    asset.file.save("asset" + safe_ext, File(stream), save=True)
                asset.source_error = ""
                asset.save(update_fields=["source_error"])
        return record, True, stage_warnings


def validate_import_for_publish(record):
    errors = []
    payload = record.payload or {}
    if not payload.get("modules"):
        errors.append("The course has no modules.")
    referenced_asset_ids = set()
    for module in payload.get("modules", []):
        if not module.get("lessons"):
            errors.append("Module " + str(module.get("title", "")) + " has no lessons.")
        for lesson in module.get("lessons", []):
            if not lesson.get("title") or not lesson.get("content_markdown", "").strip():
                errors.append("Lesson " + str(lesson.get("title", lesson.get("source_id", ""))) + " is missing a title or lesson text.")
            referenced_asset_ids.update(lesson.get("content_json", {}).get("asset_ids", []))
    staged = {str(asset.stable_asset_id): asset for asset in record.assets.all()}
    live = {str(asset.pk): asset for asset in CourseMediaAsset.objects.filter(pk__in=referenced_asset_ids)}
    for asset_id in sorted(referenced_asset_ids):
        stage_asset = staged.get(asset_id)
        live_asset = live.get(asset_id)
        staged_has_file = bool(stage_asset and stage_asset.file)
        live_has_file = bool(live_asset and live_asset.file)
        if not staged_has_file and not live_has_file:
            key = stage_asset.asset_key if stage_asset else asset_id
            errors.append("Missing course image " + key + ". Upload a valid JPEG, PNG or WebP in Course import assets before publishing.")
    return list(dict.fromkeys(errors))


def publish_course_import(record, *, user):
    """Atomically apply an approved import, matching by stable slugs and preserving learner rows."""
    errors = validate_import_for_publish(record)
    if errors:
        raise ValidationError(errors)
    with transaction.atomic():
        record = CourseImport.objects.select_for_update().get(pk=record.pk)
        if record.status != CourseImport.Status.REVIEW:
            raise ValidationError("Only a draft import awaiting review can be published.")
        payload = record.payload
        course_data = payload["course"]
        course = Course.objects.filter(slug=record.course_slug).first()
        if course is None:
            course = Course.objects.create(
                title=course_data["title"], slug=course_data["slug"],
                summary=course_data.get("summary", ""), description=course_data.get("description", ""),
                position=course_data.get("position", 0), status=Course.Status.DRAFT,
            )
        else:
            for index, module in enumerate(course.modules.order_by("position", "pk")):
                module.position = 30000 + index
                module.save(update_fields=["position"])
        course.title = course_data["title"]
        course.summary = course_data.get("summary", "")
        course.description = course_data.get("description", "")
        course.position = course_data.get("position", 0)
        course.save(update_fields=["title", "summary", "description", "position", "updated_at"])

        module_slugs = {item["slug"] for item in payload["modules"]}
        all_modules = list(course.modules.all())
        for index, module in enumerate(all_modules):
            module.position = 30000 + index
            module.is_published = False
            module.save(update_fields=["position", "is_published", "updated_at"])

        for module_index, module_data in enumerate(payload["modules"]):
            module, _created = CourseModule.objects.get_or_create(
                course=course, slug=module_data["slug"],
                defaults={
                    "title": module_data["title"], "description": module_data.get("summary", ""),
                    "position": 20000 + module_index, "is_published": False,
                },
            )
            existing_lessons = list(module.lessons.all())
            for index, lesson in enumerate(existing_lessons):
                lesson.position = 30000 + index
                lesson.is_published = False
                lesson.save(update_fields=["position", "is_published", "updated_at"])
            module.title = module_data["title"]
            module.description = module_data.get("summary", "")
            module.position = 20000 + module_index
            module.is_published = False
            module.save(update_fields=["title", "description", "position", "is_published", "updated_at"])

            lesson_slugs = {item["slug"] for item in module_data["lessons"]}
            for lesson_index, lesson_data in enumerate(module_data["lessons"]):
                lesson, created = Lesson.objects.get_or_create(
                    module=module, slug=lesson_data["slug"],
                    defaults={
                        "title": lesson_data["title"], "position": 20000 + lesson_index,
                        "content_markdown": "", "is_published": False,
                    },
                )
                next_markdown = lesson_data["content_markdown"]
                next_json = lesson_data.get("content_json", {})
                next_fields = {
                    "title": lesson_data["title"],
                    "summary": lesson_data.get("summary", ""),
                    "position": lesson_data["position"],
                    "duration_minutes": lesson_data["duration_minutes"],
                    "release_day": lesson_data["release_day"],
                    "is_preview": lesson_data["is_preview"],
                    "format": lesson_data.get("format", "reading"),
                    "content_markdown": next_markdown,
                    "content_json": next_json,
                    "is_published": True,
                }
                changed_fields = [field for field, value in next_fields.items() if getattr(lesson, field) != value]
                if not created and changed_fields:
                    CourseContentRevision.objects.create(
                        lesson=lesson,
                        editor=user,
                        revision_type=CourseContentRevision.RevisionType.IMPORT,
                        source_import=record,
                        title_before=lesson.title,
                        summary_before=lesson.summary,
                        content_markdown_before=lesson.content_markdown,
                        content_json_before=lesson.content_json,
                        fields_changed=changed_fields,
                    )
                for field, value in next_fields.items():
                    setattr(lesson, field, value)
                lesson.save()
            # Rows removed from the source are kept (including their UUIDs and progress) but hidden.
            module.lessons.exclude(slug__in=lesson_slugs).update(is_published=False)
            module.position = module_data["position"]
            module.is_published = True
            module.save(update_fields=["position", "is_published", "updated_at"])
        course.modules.exclude(slug__in=module_slugs).update(is_published=False)

        for asset_data in payload.get("assets", []):
            asset_id = asset_data["stable_asset_id"]
            staged_asset = record.assets.filter(stable_asset_id=asset_id).first()
            media_asset = CourseMediaAsset.objects.filter(pk=asset_id).first()
            if media_asset is None:
                media_asset = CourseMediaAsset(
                    id=asset_id, course=course, asset_key=asset_data["asset_key"],
                    alt_text=asset_data.get("alt_text", ""), source_path=asset_data.get("source_path", ""),
                    updated_by=user,
                )
            else:
                media_asset.course = course
                media_asset.asset_key = asset_data["asset_key"]
                media_asset.alt_text = (staged_asset.alt_text if staged_asset and staged_asset.alt_text else asset_data.get("alt_text", "")) or media_asset.alt_text
                media_asset.source_path = asset_data.get("source_path", "")
                media_asset.updated_by = user
            if staged_asset and staged_asset.file:
                media_asset.file.name = staged_asset.file.name
            media_asset.save()

        course.status = Course.Status.PUBLISHED
        course.save(update_fields=["status", "updated_at"])
        record.target_course = course
        record.status = CourseImport.Status.PUBLISHED
        record.reviewed_by = user
        record.published_at = timezone.now()
        record.save(update_fields=["target_course", "status", "reviewed_by", "published_at"])
        CourseImport.objects.filter(
            course_slug=record.course_slug, status=CourseImport.Status.REVIEW
        ).exclude(pk=record.pk).update(status=CourseImport.Status.SUPERSEDED)
        AuditLog.log(
            action="academy_course_published",
            user=user,
            object_type="course",
            object_id=course.pk,
            metadata={"course_slug": course.slug, "course_import_id": str(record.pk), "content_hash": record.content_hash},
        )
        return course
