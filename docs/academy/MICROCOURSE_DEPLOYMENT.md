# TradeThesis micro-course deployment workflow

## Goal

Create one reliable path from approved course sources to a reviewed, database-backed course. Use ZaphodBeeblebrox12/prototypeweb on my-new-branch. Do not import from main, which contains obsolete course material. Django runtime changes belong in ZaphodBeeblebrox12/test on feature/tradethesis-academy.

## Workflow

1. Author student content in course Markdown and update that course's schema-version-1 course-manifest.json. Keep module and lesson slugs stable across edits. Markdown lesson headings must match manifest source IDs and titles exactly.
2. Run python manage.py import_microcourse --manifest PATH_TO_MANIFEST --dry-run. Resolve structural errors. Missing or unsupported images are reported as review warnings; the course can be staged but cannot be published until every referenced image is supplied.
3. Run the same command without --dry-run. This creates a review snapshot. It does not create, edit or publish the live Course, CourseModule or Lesson records.
4. Sign into Django Admin as a superuser or a user whose role is explicitly Admin. Open Academy → Course imports → Preview course. Review every module and lesson in the shared Learning Room design.
5. Replace missing or unsuitable images under Course import assets with valid JPEG, PNG or WebP. The server validates decoded format and enforces a 10 MB limit. Asset replacement controls are not available to ordinary staff or learners.
6. Publish only after the preview is satisfactory and the publish validation passes. The administrator must click Approve and publish course. Publishing applies the staged snapshot in one database transaction, creates revisions for changed existing lesson content, and marks the staged import as published.
7. Configure the intended CourseOffer separately in the existing commerce admin if this is a paid course. Import/publish does not set prices, activate offers, create entitlements or alter access duration.
8. For later edits, import a new snapshot and review it again. The current published version remains live while a newer snapshot is awaiting review. Stable module/lesson slugs are used to update existing records instead of creating duplicates; existing UUIDs, enrolments, offers and progress rows are preserved.

## Example

From an environment with both checkouts available, run:

- python manage.py import_microcourse --manifest ../prototypeweb/course-source/understand-bollinger-bands/course-manifest.json --dry-run
- python manage.py import_microcourse --manifest ../prototypeweb/course-source/understand-bollinger-bands/course-manifest.json

The exact relative path depends on the checkout locations. The import command must run in an environment connected to the intended Academy database and persistent media storage if the review is intended for that environment.

## Manifest shape

A manifest has schema_version 1, course metadata, a notebook pattern, and ordered modules. Each module points to one Markdown source file and lists source IDs, stable lesson slugs, titles, order, durations and release days. The source Markdown groups lessons under headings such as “Lesson 1.1 — Title”. Figures are discovered from Markdown image references and assigned deterministic IDs derived from course slug plus normalized asset key.

Do not edit generated preview JavaScript to change lesson content. Edit the Markdown and manifest, then re-import. A repeated import with the same content hash reuses the existing staged record.

## Security and release notes

- Shared lesson text and image edits require an Admin role or Django superuser; is_staff alone is not sufficient.
- Lesson Markdown containing active script/style/iframe/object/embed markup, event handlers or executable URLs is rejected. Rendered HTML is sanitised with a tag and attribute allowlist.
- The publish action requires a CSRF-protected POST and revalidates the staged content and all referenced image assets on the server.
- The media delivery route checks admin access or learner entitlement to a published lesson referencing that image. Do not expose the Academy media storage directory through an unprotected public static route.
- Production must be configured with durable shared media storage. The current settings use Django's default storage backend; that storage must point to durable storage in the hosting environment before image replacements are relied on in production.
- Import/publish changes the course content and publication state only. It does not activate checkout, create pricing, or deploy code.

## Validation status

The command's dry run checks source files, source IDs, stable slugs, unique module/lesson positions and image availability. Automated tests cover repeat imports, staged update behaviour, stable lesson IDs, progress preservation, missing-image publish denial, CSRF failure, unsafe markup rejection and ordinary-staff denial. Run the Academy test suite and deployment browser checks before release.
