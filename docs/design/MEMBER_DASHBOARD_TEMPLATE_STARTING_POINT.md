# Member Dashboard Template — Starting Point

**Repository:** `ZaphodBeeblebrox12/test`  
**Branch:** `feature/tradethesis-academy`  
**Purpose:** Preserve the approved TradeThesis Member Desk design direction as the starting point for the Django member dashboard redesign. This is a reference and static prototype, not a production-connected template.

## Starting-point preview
Open [`member-dashboard-template-preview.html`](./member-dashboard-template-preview.html) in a browser. It uses the light, warm TradeThesis palette, compact editorial layout, member navigation, a continue-learning panel, progress/account summaries, course rows, and quick-access shortcuts. The basic sidebar interactions are demonstrative; all visible names, counts, progress and account states are sample data.

## Existing Django implementation to preserve
- **Member/user dashboard:** `templates/accounts/dashboard.html` (currently extends `base.html`). This is the integration target for a future implementation.
- **Django admin operational overview:** `templates/admin/dashboard.html`. This is a separate admin-specific panel and is not what this member-dashboard preview replaces.
- **Shared Django layout:** `templates/base.html`.

Do not replace the existing templates wholesale with the static preview. The existing user dashboard contains real Django context and actions. Preserve and map its live data and behaviour—including account status, Telegram access/connect states, subscriptions/purchases, billing, profile and connected-account functionality—into the redesigned layout. Do not substitute the sample preview values for backend data.

## Design and integration guidance
1. Use this preview as the visual starting point: light theme, warm paper background, restrained forest green, sage, terracotta and muted gold, compact information hierarchy, clear learning continuation and account shortcuts, and responsive mobile navigation.
2. Before integration, inspect the current dashboard view/context and related tests. Map actual fields, permission checks and URLs into the new layout; don't invent live metrics.
3. Keep learner/member navigation distinct from Django admin navigation, while maintaining a coherent TradeThesis visual language.
4. Keep demo-only actions clearly separated from working Django actions. Add/adjust tests for dashboard rendering, navigation targets, account states, permissions and mobile layout.
5. **No deployment or merge is implied by this design note.** Continue work on `feature/tradethesis-academy`; do not merge this into `main`.

**Status:** Starting-point reference added. Not integrated into Django views/templates yet, not production-ready, and not deployment-ready.
