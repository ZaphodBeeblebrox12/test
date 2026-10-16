# TradeThesis Academy — Django Compatibility Audit

**Status:** Implementation in progress on `feature/tradethesis-academy`; draft PR opened. Course models, learner routes, one-time course checkout integration, and initial tests are present; CI and integration validation remain in progress.  
**Branch:** `feature/tradethesis-academy`  
**Base:** `main` at `846ac33de7ae9fb2fba4c918e2a4484a396ffe6c`  
**Purpose:** Determine the fastest safe path to connect the existing TradeThesis website to accounts, payment, access control, and course delivery without rewriting the existing commerce application.

## Executive recommendation

Use the existing Django application as the backend, subject to a short compatibility and regression pass. Add a dedicated `apps.academy` domain for courses and learner state. Reuse the current user model, payment-intent ledger, provider adapters, signature-verified webhook intake, durable job queue, policies/evidence, and admin conventions where they pass tests.

Do **not** reuse subscription activation as the definition of course access. The existing successful-payment path creates or extends a `Subscription`; education products need distinct entitlements with their own rules (lifetime access, scheduled course release, and time-limited included community access). The implementation must have one source of truth for payment status while keeping Academy entitlements separate from bot/subscription permissions.

The existing frontend can remain visually unchanged. A Django-hosted member and course area on a dedicated `learn.` subdomain is a practical launch path; use full-page navigation between the marketing site and learning area rather than introducing cross-origin API authentication in the first release. Domain names and deployment target remain to be confirmed.

## Audited repository facts

### Existing assets worth reusing

- Custom user model in `apps/accounts/models.py`, including email, account status, and optional Telegram/Discord identity.
- Django Allauth login/sign-up URLs in `config/urls.py`; account views and mandatory email verification are already present.
- `apps/payments/models.py` provides a single `PaymentIntent` financial record with amount/currency snapshots, provider references, refunds and dispute/chargeback fields.
- `apps/payments/providers.py` creates hosted Stripe Checkout sessions and Razorpay orders and supports server-side status verification.
- `apps/payments/webhook_views.py` verifies provider signatures, persists events idempotently and enqueues processing. `apps/payments/webhook_processor.py` routes captured-payment/refund/dispute events into shared lifecycle services.
- `apps/evidence/models.py` has immutable `CheckoutEvidence` and `PaymentAuthentication` records that reference `PaymentIntent`. New checkout flows must not bypass the terms/refund/risk agreement capture requirements.
- `apps/jobs` provides a durable database-backed job queue. It can support idempotent course release and mail work, provided the deployed worker/scheduler actually runs.
- `apps/subscriptions/models.py` supports global and geo-specific prices, products, plan prices, coupon/referral provenance and price snapshots.
- Existing admin, policy, audit, notification, deployment and runtime test conventions are reusable.

### Existing coupling and constraints

1. **Payment activation is subscription-shaped.** `activate_paid_subscription()` creates/extends a `Subscription`, sends subscription-oriented notices, and emits billing events. The customer-return confirmation view and webhook processor both call this path.
2. **Checkout evidence is one-to-one with PaymentIntent.** Keeping course checkout on the same payment-intent/evidence ledger minimizes duplicate compliance and financial ledgers.
3. **Provider adapter assumptions are subscription-shaped.** Stripe checkout names the item as a subscription and includes `payment_intent_id` metadata. These should be made commerce-product aware without weakening verification.
4. **Refund and chargeback code changes subscription access.** It must also revoke or suspend the appropriate course/community entitlement according to the recorded purchase terms. The exact business policy needs to be explicit; do not silently infer refunds mean every access grant expires.
5. **Plan pricing only models monthly, quarterly and yearly intervals.** Trading Path is one-time priced. Do not disguise a one-time sale as a monthly plan; add a clearly represented one-time interval or introduce a dedicated price source that the payment service can resolve.
6. **Static preview is not authentication.** `prototypeweb/site/member-access.html` and `member-area.html` are review UI only; they do not provide authenticated, server-enforced access.
7. **Paid course content must not live in a public static path or public Git repository.** Put lesson bodies in authenticated database records or private object storage; serve media using expiring/signed access or an authenticated endpoint. Keep instructor answer keys separate from learner payloads.
8. **Production setup requires attention.** Current `requirements.txt` pins `Django>=4.2.0,<5.0` and `django-allauth==0.57.0`. Django 4.2 security support ended in April 2026. The project needs a planned supported-version upgrade with migrations and full regression tests; it should not be upgraded blindly in the same change as all Academy features.
9. **The deployment notes describe operational constraints.** PostgreSQL is required on ephemeral-filesystem hosts; email verification requires working SMTP; job workers and media-storage persistence need explicit deployment choices.
10. The root tree includes a tracked `db.sqlite3`. Inspect and classify it for actual customer data/secrets before production use or any repository visibility change. Do not copy it into Academy fixtures or commits.

## Proposed boundaries

### Keep using current applications

- `accounts.User` for identity and login.
- `payments.PaymentIntent`, `WebhookEvent`, provider signature verification, durable webhook jobs and payment evidence for payment records.
- Existing payment providers and price-resolution services after they are generalized/tested for one-time course offers.
- Existing refund, chargeback, policy-version, audit and notification systems, with course-entitlement hooks added and tested.
- Existing PostgreSQL and deployment stack where operational checks pass.

### Add `apps.academy`

Initial domain models should cover:

- **Course** — title, stable slug, draft/published/archived status, learner-facing summary and ordering.
- **CourseModule** — ordered group of lessons within a course.
- **Lesson** — module, stable slug/order, lesson format, learner content stored outside public static assets, and publication status.
- **CourseOffer** — sellable offer mapped to the existing pricing/commerce configuration; captures which course(s) it grants and its access policy. It must distinguish one-time price from subscription billing.
- **CourseEntitlement** — user, course or access bundle, grant source/order, grant time, optional expiry, and revocation state. A `NULL` expiry represents genuinely permanent access. Do not use a generic subscription row as the only access check.
- **LessonProgress** — user, lesson, status, timestamps, and structured exercise/assessment state. Enforce uniqueness for one user's progress record per lesson.
- **LessonRelease` (or equivalent release policy)** — supports day-relative releases and idempotent unlock times for drip courses; does not delete or shorten lifetime access when a learner falls behind.

Keep course content and entitlements separate from billing history: payment evidence answers “what was purchased and paid?”, while entitlements answer “what can this user access now?”.

## Proposed purchase lifecycle

1. Customer signs in or registers through the existing account system.
2. Backend loads the active offer and resolves price/currency server-side. Client-supplied amount is never trusted.
3. Create a pending payment record and required immutable checkout evidence before handing the customer to Stripe/Razorpay.
4. Provider success redirects are only a status check. Only server-side provider verification or a valid signed webhook can claim a payment as successful.
5. Successful payment handling is atomic and idempotent: record payment success once, grant the matching Academy entitlement once, schedule releases/notifications via durable jobs, and emit an audit/event record.
6. A duplicate webhook or a return/webhook race must not create duplicate enrolments, extend access unexpectedly, or send duplicate release messages.
7. Failed/cancelled/unknown payments do not grant access. Refund and dispute events follow explicitly defined access-revocation rules.
8. All lesson HTML, markdown, answer keys, downloads and learner-specific progress are served after a server-side entitlement check. Hiding a link or route in JavaScript is not access control.

**Implementation decision still to make after focused code review:** whether to map CourseOffer to an existing Plan/PlanPrice (least payment-schema churn, but requires an explicit one-time interval and a strongly separated entitlement activation branch) or to add an optional offer reference to PaymentIntent (cleaner product semantics but wider compatibility changes because existing views/admin/evidence assume a Plan). Do not commit either approach until the payment/evidence/refund call sites have been traced and regression tests identified. Avoid creating a parallel payment/order ledger unless integration constraints prove reuse infeasible.

## Version and deployment work

- Target a currently supported Django release, preferably Django 5.2 LTS as the conservative migration target at this audit date, with a compatible maintained django-allauth release and compatible dependency pins.
- Upgrade separately from the first Academy model/feature commit. Preserve the existing Python-version matrix and run lint, formatting, type checking, migrations, Django deploy checks, and the full pytest suite.
- Confirm the correct production settings module and PostgreSQL migration path; verify `createsuperuser`, email verification, password recovery and SMTP.
- Keep secrets out of Git and rotate any credential found in a tracked file/history.
- Decide persistent private object storage for lesson images/downloads and a deployment strategy for the durable job worker/scheduler. Do not rely on ephemeral local disk for paid media or learner files.

Official version references:
- Django supported releases: https://www.djangoproject.com/download/
- Django 5.2 release notes: https://docs.djangoproject.com/en/5.2/releases/5.2/
- django-allauth release notes: https://docs.allauth.org/en/latest/release-notes/

## Work plan and acceptance gates

### A0 — Baseline and compatibility
- [ ] Capture the current branch SHA and use a clean work branch.
- [ ] Inspect project settings selection, dependency constraints, migrations, checkout consent capture, payment confirmation, webhook processing, refunds/chargebacks and existing tests.
- [ ] Reproduce the existing test baseline locally/CI before edits. The base commit message reports a green 135-test suite, but that result has not been independently rerun in this audit.
- [ ] Confirm database file/data classification and locate any credentials in tracked files/history.
- [ ] Select and test supported Django/allauth versions in a separate compatibility change.

### A1 — Academy domain
- [ ] Add `apps.academy`, migrations and admin registrations.
- [ ] Add Course, Module, Lesson, Offer, Entitlement and Progress/Release models.
- [ ] Register the app in settings; add factory and model tests.
- [ ] Keep premium lesson content out of public repository paths.

### A2 — Course purchase integration
- [ ] Decide commerce model based on call-site audit (no duplicate payment ledger).
- [ ] Support one-time course pricing without fake monthly billing semantics.
- [ ] Connect the existing consent/evidence capture flow.
- [ ] Ensure customer return and webhook use one atomic, idempotent payment-activation service.
- [ ] Add regression coverage for duplicate events and return/webhook races.

### A3 — Secure learning experience
- [ ] Add authenticated course list, detail, lesson, resource and progress endpoints/pages.
- [ ] Enforce ownership and entitlement checks in server code.
- [ ] Implement day-relative release scheduling and safe retry behaviour.
- [ ] Separate learner feedback from instructor answer keys.
- [ ] Test direct URL access as logged-out, logged-in non-purchaser, purchaser, expired/revoked user and instructor/admin.

### A4 — Website and launch gate
- [ ] Replace preview member/enrolment actions with full-page links to the learning app.
- [ ] Test mobile views and the actual course/quiz/resource experience.
- [ ] Test Stripe and Razorpay sandbox payments, amount/currency mismatch, invalid webhook signatures, failures, refunds, disputes and recovery.
- [ ] Verify PostgreSQL backups, SMTP, persistent object storage, HTTPS/cookies/CSRF, rate limiting and operational monitoring before live checkout.

## First vertical-slice acceptance criteria

The first production candidate should be Trading Path only:

- A visitor can sign up, verify email, log in and recover their password.
- A user can initiate the ₹999 one-time offer using server-resolved INR pricing.
- No browser redirect alone can grant access.
- Verified capture grants permanent Trading Path access exactly once.
- One lesson unlocks according to the configured daily drip; a late learner can catch up without losing permanent course access.
- Lesson bodies and protected resources cannot be fetched by a non-entitled user through direct URLs.
- Progress persists across sessions/devices.
- The existing subscription and bot-access tests remain green.
- Refund/dispute policy for access is decided and implemented before live payment is enabled.

## Scope guardrails

- Do not redesign the marketing site as part of backend work.
- Do not grant bot permissions from course purchases.
- Do not alter production plans/customers or enable live payment keys from development branches.
- Do not commit secrets, live database exports, private lesson media or instructor answer keys.
- Keep every change on `feature/tradethesis-academy` until test gates pass and the user approves merge/deployment.


## Implementation log (16 October 2026)

- Added `apps.academy` models/admin for courses, modules, lessons, instructor-only materials, one-time offers, entitlements, entitlement events, and learner progress.
- Added the explicit `one_time` price interval for base and regional prices and dispatched mapped course offers through the existing payment-intent lifecycle rather than creating subscriptions.
- Added authenticated Academy overview, course, lesson and progress routes. Lesson bodies are only rendered after the server-side published/release/entitlement check.
- Added customer-return and webhook fulfilment handling for course purchases, plus refund/chargeback revocation hooks.
- Added unit tests for learner access, progress and idempotent payment fulfilment.
- CI initially could not run because the workflow omitted Ruff/Black/mypy from its installed tools. The feature branch installs these tools and limits static checks to Academy and touched integration files; the full pytest suite remains enabled.
- **Not launch-ready:** test results are still being collected; Django/allauth upgrade, production secrets/settings, persistent private content storage, course-source migration from the public static repo, content/interactive migration and live sandbox end-to-end tests remain gates.

## Cross-repo handoff: lesson editing, shared instructional media, and VPA discovery (17 October 2026)

**Source of the website-side requirement:** `ZaphodBeeblebrox12/prototypeweb`, branch `my-new-branch`, checkpoint [`189892f5c3d0c7e27ea8046022cc5411d430d05e`](https://github.com/ZaphodBeeblebrox12/prototypeweb/commit/189892f5c3d0c7e27ea8046022cc5411d430d05e). The static `Understand Bollinger Bands` preview now has deliberate related-course placements for the separate **Volume & Price Analysis** course, a visible non-purchasable development entry in the static catalogue, figure-count/label updates, and browser-local text/image editing prototypes. This is a handoff of requirements—not evidence that the website preview has been visually browser-tested or that the local edits are persistent.

### Integrate into the existing Academy model; do not create a second CMS

- `apps.academy.models.Lesson` already has `content_markdown` and `content_json`, and `LessonAdmin` already exposes these fields. Reuse them as the content source of truth. Keep this narrowly scoped to lesson copy, figure captions, structured practice content, and media references; do not introduce a general page builder or a parallel lesson-content store.
- Provide the minimum staff editing experience needed to edit learner-facing text and replace a figure without hand-editing HTML. Prefer stable content/block identifiers stored with the lesson content; do not make persisted identity depend on DOM order or the preview editor's generated IDs.
- Only render editing controls for authenticated staff with the appropriate course-content permission, and re-check that permission on every save. Use POST with CSRF protection, validate the submitted lesson/field IDs, and sanitise rendered content. A success message may appear only after the database transaction succeeds; a failed request must leave the UI showing a clear failure.
- The static prototype's browser-local drafts are for interaction testing only. They must not be described as shared saves or carried into production as a security boundary.

### Shared image replacement

- The Academy `Lesson` model does not appear to have a stable shared-media relation today. First check for an existing project-wide media/storage abstraction; reuse it if one exists. Otherwise, add only a small persistent instructional-media record with a stable asset ID, stored file, alt text, and updated-by/updated-at metadata.
- Let lesson content reference the stable asset ID so every use of that asset resolves to the same current image. A successful staff replacement must update the persistent file/reference and affect every rendered use, not only the current browser or lesson instance.
- Restrict uploads to validated JPEG/PNG/WebP images, enforce size limits and safe storage names, and use persistent storage appropriate for paid/private course content. The upload endpoint must enforce staff permission and CSRF server-side; hidden buttons and client-side flags are not authorization. Keep old assets recoverable until the replacement is verified.
- The public static preview references external images; do not assume all images have been downloaded, audited, or migrated. Track asset mapping during course-content migration and verify image readability/annotations before publishing learner content.

### Volume & Price Analysis course discovery

- Preserve the website-side course-discovery intent when connecting the Academy catalogue: learners studying relevant Bollinger Bands lessons—especially the price/indicator disagreement lesson in Module 5—should see a compact link to the separate **Volume & Price Analysis** outline.
- Keep that entry visibly **In development / Coming soon** until the course is actually ready. It must be searchable/discoverable but must not create an active `CourseOffer`, purchasable CTA, or checkout path prematurely. Keep course availability separate from payment/entitlement logic.
- The companion outline should cover, where supported by the planned curriculum, relative volume, buying/selling pressure, effort versus result, price/volume disagreement and confirmation, breakout failures/climaxes, Volume Profile, Point of Control, Value Area, high-/low-volume nodes, and MFI. Do not add every topic to every Bollinger lesson; use contextual lesson placements instead of keyword-only card injection.

### Acceptance checks before merge/release

1. An authorised staff editor can update existing lesson text/captions and receives a success state only after persistence; a save failure is visible and does not lose the original server content.
2. Learners, anonymous visitors and staff without the required permission cannot invoke the save/upload endpoints; CSRF and unsafe-content tests pass.
3. Replacing a shared asset changes every reference to that asset for subsequent page loads, while preserving alt text/metadata and private-content access checks.
4. The VPA discovery card and catalogue outline are visible/searchable, explicitly marked in development, and have no purchase action or active offer.
5. Test the real Django-rendered lesson on desktop and mobile, including chart readability, figure enlargement, navigation, and the existing progress/entitlement flow.

**Branching:** continue this work on `feature/tradethesis-academy`. Keep the website source branch separate; do not merge to `main`, deploy, or activate live checkout as part of this handoff note.

