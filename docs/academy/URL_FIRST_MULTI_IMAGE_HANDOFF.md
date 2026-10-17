# URL-first, multi-image course workflow — handoff

**Updated:** 17 October 2026  
**Purpose:** Continue rapid micro-course Django integration in a new chat. This is an implementation checkpoint, not a release approval.

## 1. Two repositories, two responsibilities

### Course source and template contract
- Repository: `ZaphodBeeblebrox12/prototypeweb`
- Branch: `my-new-branch`
- Latest head verified at handoff: `5d0750b7147f6326a3ce0ac759c30075c0bef6b5`
- The course Markdown modules and `course-manifest.json` remain the authoring source of truth.
- Shared template contract: `course-source/COURSE_TEMPLATE_STANDARD.md`
- Manifest contract: `course-source/COURSE_MANIFEST_STANDARD.md`
- Bollinger course: `course-source/understand-bollinger-bands/`
- Integration brief: `course-integration/MICROCOURSE_TEMPLATE_DJANGO_HANDOFF.md`

The source branch advanced after the earlier `512fe654d9efdc42d399f44587029a1e9aa8f82e` checkpoint; the current branch head must always be rechecked at the start of the next session. Do not merge or deploy this branch.

### Django runtime and image workflow
- Repository: `ZaphodBeeblebrox12/test`
- Branch: `feature/tradethesis-academy`
- Latest head verified at handoff: `a83b6eb20f40aa342790f2b7180b746b1cda1522`
- This head includes the URL-first/multiple-image work in its ancestry, followed by separate member-dashboard preview/documentation commits. Recheck branch history before editing because parallel work may continue.
- Latest runtime commit message: `Document member dashboard template starting point`.

Relevant image-work commits in this branch's history:
- `e5072ed93063b88a823db6cbae589a8055002220` — support URL-first and multiple lesson images.
- `bcf9d6c8a3b06343a667626d4497057d90df7610` — add upload replacement and focused image-slot tests; add focused test stage to CI.
- `44f0f6795bbd7d8c577727c0863a4266d03a1714` — correct test import ordering for Ruff.

## 2. Intended administrator experience

The goal is that external HTTP(S) image links render immediately as temporary defaults. Later, an authorised administrator can upload their own images without rewriting the lesson Markdown. A lesson can have any number of independently managed images across the Learning Room tabs.

Implemented on the runtime branch:
- Existing importer detects Markdown image references and converts them into stable `asset://<uuid>` references.
- Original HTTP(S) URLs are retained in `CourseImportAsset.original_url` and `CourseMediaAsset.original_url`.
- An uploaded file overrides the URL at render time; the original URL is retained for provenance/fallback.
- Course import review labels figures as **Uploaded replacement**, **Using URL for now**, or **Needs upload**, and renders URL-backed images.
- Django Admin → Academy → Lessons has an **Add image** action for adding another figure to a lesson. The form accepts a URL, upload, or both; asks for alt text and a target Learning Room tab; and can be used repeatedly for multiple images.
- Django Admin → Academy → Course media assets allows an administrator to upload a replacement for an existing asset.
- Adding a figure creates a lesson content revision and an audit entry.
- Content editing uses `is_course_content_admin`: Django superusers or users explicitly assigned the Admin role. `is_staff` alone must not grant permission.
- Upload validation uses Pillow and allows JPEG, PNG, or WebP up to 10 MB. SVG uploads are rejected. Uploaded teaching files use private Academy storage and the protected media route.
- No database changes were needed for the additional lesson-figure authoring action; it reuses the existing `CourseMediaAsset`, `Lesson.content_markdown`, and `Lesson.content_json.asset_ids` structures.

Files introduced/changed for this capability include:
- `apps/academy/forms.py`
- `apps/academy/admin.py`
- `templates/admin/academy/lesson_add_figure.html` (new)
- `templates/academy/course_review.html`
- `tests/academy/test_course_import_workflow.py`
- `.github/workflows/ci.yml`
- `docs/academy/IMAGE_SLOT_WORKFLOW.md`
- `docs/academy/MICROCOURSE_DEPLOYMENT.md`

## 3. Bollinger Bands image inventory

The latest checked manifest has **7 modules and 30 lessons**. Across the seven Markdown sources there are **62 image references**:
- **32 remote HTTP(S) historical-chart references** in Module 5, which can render now as temporary defaults.
- **30 local SVG figure references** under `assets/student-assets/svg/`, whose files were confirmed absent from the source branch tree at the time of this audit.

Do not invent unrelated image URLs or silently replace the missing figures with stock visuals. The admin review should continue to label missing local assets as requiring a replacement, and publication should remain blocked until each genuinely missing local figure is supplied. Review rights/permissions for third-party historical chart images before public release.

## 4. CI and test status at handoff

Latest relevant Actions run for runtime head `a83b6eb20f40aa342790f2b7180b746b1cda1522`:
- [CI run 39044846160](https://github.com/ZaphodBeeblebrox12/test/actions/runs/39044846160)
- Ruff and Black stages passed.
- The focused micro-course image-slot regression stage passed: **13 passed** on the run inspected.
- The full suite reported **662 passed, 1 skipped, 1 failed** on Python 3.12; Python 3.11 was cancelled after the matrix failure.
- The failing test was `tests/pricing/test_admin_base_price.py::test_global_plus_geo_overrides_save`. The same test also failed on the pre-image-feature baseline commit `f07aacc6424256a41fa329936a5f2d034c1f48e9` (657 passed, 1 skipped, 1 failed), so the current evidence points to an existing, unrelated pricing failure rather than the image feature. Keep it visible; do not claim the overall CI is green.
- The logs also show the periodic scheduler trying to query `jobs_periodicjob` before that table exists and occasional SQLite database-lock warnings during tests.
- The mypy command is configured as non-blocking and logged 18 errors across 8 files during the latest run; these have not been resolved as part of the image feature.

## 5. Latest engineering checkpoint — Learning Room tab routing

**Checkpoint date:** 17 October 2026  
**Runtime branch:** `feature/tradethesis-academy`  
**Latest implementation commit at CI verification:** `3671cdf901788e411cdda911aa535c335341f721`  
**CI run:** [39046300112](https://github.com/ZaphodBeeblebrox12/test/actions/runs/39046300112)

### Defect and correction

The add-image workflow emits `Additional concept figure`, `Additional chart examples` and `Practice figure`. The original parser classified any heading containing `figure` as Chart examples before checking practice-related words; moving the generic practice check earlier was insufficient for “Additional concept figure”. The parser now recognises the three admin-generated placement headings explicitly before applying the existing generic heading heuristics. Existing Markdown structure and shared template standard remain unchanged.

### Regression coverage and verified outcomes

`tests/academy/test_course_import_workflow.py::test_admin_added_images_render_only_in_the_selected_learning_room_tab` posts one image through the real Django Admin add-image endpoint for each of the three placement options, then verifies:
- all three independent assets and source URLs are retained;
- each image caption and URL appears in its selected rendered tab, and its URL is absent from the other two tabs;
- three content revisions and audit events are recorded.

The existing multiple-image test continues to cover adding multiple images to the same tab. The existing focused workflow test also covers upload replacement precedence/original URL retention, ordinary staff denial, CSRF, and URL-first preview.

On run `39046300112`, **both Python 3.11 and Python 3.12 passed Ruff, Black, the configured mypy step, and the focused micro-course image-slot stage**. The focused stage passed on both versions with the new regression included.

The **full-suite outcome was not green**: Python 3.11 finished with **663 passed, 1 skipped, 1 failed**. The remaining failure is `tests/pricing/test_admin_base_price.py::test_global_plus_geo_overrides_save`; the response contains required `PlanFeature` inline fields `position` and `text` that the test did not submit. This failure also existed on the pre-image-feature baseline (commit `f07aacc6424256a41fa329936a5f2d034c1f48e9`) and is outside this image-routing change. Python 3.12's full-suite step was cancelled by the matrix after the Python 3.11 failure, so there is no full-suite result for 3.12 in this run.

### Remaining verification gaps

The GitHub connector supports source edits and Actions inspection, but it does not provide a local checkout/runtime or interactive browser session in this workflow. A browser review of the course-import preview and published lesson has therefore **not** been performed. Render environment settings for `ACADEMY_PRIVATE_MEDIA_ROOT` are not readable from the available workflow, so durable shared storage is **not verified**. Do not rely on production image uploads until that setting is confirmed against durable shared storage.

## 6. Production storage and release boundaries

Before depending on uploads in production, configure `ACADEMY_PRIVATE_MEDIA_ROOT` to durable shared storage. A transient container filesystem is not suitable for persistent course images. No Render deployment or public course release has been approved as part of this work.

No source-template merge, runtime merge, or Render deployment has been performed in this workstream. The next chat should fetch the latest heads first, inspect changes since this note, fix the tab-placement issue, run the focused image tests and then the full runtime CI. Keep the known pricing failure separate unless explicitly expanding scope.
