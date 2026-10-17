# URL-first image workflow for TradeThesis micro-courses

**Runtime repository:** `ZaphodBeeblebrox12/test`  
**Runtime branch:** `feature/tradethesis-academy`  
**Source repository:** `ZaphodBeeblebrox12/prototypeweb`  
**Source branch:** `my-new-branch`

## The intended workflow

1. Author image references in the lesson Markdown as `![meaningful alt text](https://...)`, or use a local course-owned file when it exists. Multiple Markdown image references in one lesson are supported.
2. Run `python manage.py import_microcourse --manifest PATH_TO_MANIFEST --dry-run`, then stage the import. The importer creates one stable asset slot per distinct source image reference.
3. In the administrator review, HTTP(S) source images are rendered as temporary defaults and labelled **Using URL for now**. Missing local files without an HTTP(S) source are labelled **Needs upload** and block publication.
4. Replace an image during review using **Review / replace** on its import asset. The upload remains attached to the stable asset slot and is applied to the live media asset when that import is published.
5. For an already-published lesson, use **Academy → Lessons → Add image** to attach one or more additional figures. Select the Learning Room tab, add a URL and/or upload, and provide alt text. Each addition is saved as a unique `CourseMediaAsset` and referenced from lesson Markdown via `asset://<uuid>`.
6. Replace an existing figure later through **Academy → Course media assets**. The upload overrides the source URL while preserving that URL in the record. All uses of the same asset ID update together.
7. Verify the rendered lesson and image permissions before release. Paid lesson figures are served through the Academy protected media view when uploaded; do not expose the private media storage directory through a public static route.

## Permissions, validation and history

- All add/replace actions are served through Django Admin and re-check `is_course_content_admin`; `is_staff` alone is not enough. Learners and ordinary staff are denied.
- New figures use CSRF-protected POST requests. Uploads are decoded and validated by Pillow, limited to 10 MB, and restricted to JPEG, PNG or WebP; SVG is not accepted.
- A figure addition adds its stable media ID to the lesson's learner-safe `content_json.asset_ids`, updates the Markdown image reference, creates a content revision and writes an audit event.
- The import review keeps external source URLs on records. An uploaded file takes precedence when rendered; the original URL remains recorded for provenance/fallback.
- The feature is code-ready only when CI passes. Uploaded images require a durable shared `ACADEMY_PRIVATE_MEDIA_ROOT` in the hosting environment; a transient filesystem is not suitable for production media.

## Source inventory recorded at the integration checkpoint

The current Bollinger Bands manifest has 7 modules and 30 lessons. Source inspection found 62 Markdown image references: 32 HTTP(S) image examples inside Module 5 and 30 local SVG references under `assets/student-assets/svg/` whose files are still missing. The 32 linked images can render from their URLs now; the missing 30 figure files will show as unresolved slots and block publication until replaced or supplied. This is deliberate: don't silently substitute unrelated images or pretend the course has finished figures.
