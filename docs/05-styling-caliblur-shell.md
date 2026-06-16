# 05 — Styling: caliBlur Shell Port

> **Status:** Plan. Implementation lives on branch `style-caliblur-shell`.

## Context

The tracker already vendors every caliBlur stylesheet from the live CWN container (`style.css`, `caliBlur.css`, `caliBlur_override.css`, `cwa.css`) and renders pages with `body.blur` so the dark-theme colors apply. What's missing is the **DOM scaffold** those stylesheets expect.

caliBlur is a layout-coupled theme: a single `grep` for its load-bearing selectors finds **454 hits** in `caliBlur.css` alone. The big ones:

- `body.blur` + a page-name class (`body.serieslist`, `body.authorlist`, `body.me`, …)
- `.container-fluid > .row-fluid > .col-sm-2` (sidebar) + `.col-sm-10` (main)
- `#main-nav` (right navbar `ul`) and `#scnd-nav` (sidebar `ul`)
- `.profileDrop`, `.home-btn`, `.plexBack`
- `.col-sm-10.content-expanded` is the **scrolling element** — caliBlur explicitly does *not* scroll on `window`

Our current `app/templates/layout.html` is a minimal Bootstrap 3 shell with no sidebar, no `#main-nav`, no `#scnd-nav`. Result: colors right, layout wrong.

## Decision

Port only the **structural shell** of CWN's `layout.html`, not the whole file. CWN's `layout.html` is 781 lines, ~75% of which is CWN-specific functionality (admin, upload, refresh, drag-merge, duplicate-notifier, …). We replicate the DOM caliBlur targets and leave the rest behind.

This keeps:
- A small, readable `layout.html` we own
- Decoupling from CWN's role/feature gates
- A clean override surface for tracker-specific UI

…at the cost of one-time effort to enumerate the scaffold and a refresh whenever caliBlur ships material DOM changes (rare in practice — the shell hasn't shifted in years).

## What gets ported (load-bearing)

| Element | DOM | Why caliBlur needs it |
|---|---|---|
| Body | `<body class="<page> blur">` | Triggers theme; per-page class drives variant rules |
| Hidden CSRF | `<input type="hidden" name="csrf_token">` | caliBlur.js + cwa helpers read it; keep even if we don't use it from JS |
| Navbar | `.navbar.navbar-default.navbar-static-top` | Fixed-top dark bar |
| Toggle | `.navbar-header > .navbar-toggle` | Mobile hamburger position is overridden in `caliBlur_override.css` |
| Brand | `.navbar-brand` | Branding slot |
| Home / Back | `.home-btn` + `.plexBack` (anchor-only) | Plex-style nav buttons — caliBlur paints them via CSS |
| Search slot | `.navbar-form.navbar-left` | Optional; we may use it later for book search |
| Left nav | `ul.nav.navbar-nav` | Holds primary top-nav items |
| Right nav | `ul.nav.navbar-nav.navbar-right#main-nav` | caliBlur paints user dropdown here |
| Profile dropdown | `.dropdown > a.profileDrop` + `.dropdown-menu.profileDropli` | Avatar circle + menu |
| Sidebar column | `.row-fluid > .col-sm-2 > nav.navigation` | Off-canvas drawer container |
| Sidebar list | `ul.list-unstyled#scnd-nav` | All sidebar items go here, with `.nav-head` headers |
| Main column | `.col-sm-10.content-expanded` | Scrolling content area |
| Flash row | `.row-fluid.text-center > .alert.alert-<level>` | caliBlur fixes alerts as toasts |

## What gets adapted (tracker-specific)

| What | Tracker rendering |
|---|---|
| `<title>` | `Reading Tracker | <page>` |
| Brand | `Reading Tracker` linking to `/` |
| Home button | Points to `CWA_BASE_URL` (library) |
| Sidebar `nav-head` | `My Reading`, `Quotes`, `Shelves`, `Stats` |
| Sidebar items | Currently reading / Want to read / Finished / DNF (per status), then placeholders for phases 7-9 |
| Right nav | Profile dropdown with `Account` (placeholder), `Logout`, and a `Library` link back to CWN |
| Body page class | One of `tracker-dashboard`, `tracker-book`, `tracker-search` — used for per-page CSS hooks if needed |

## What gets dropped

Everything driven by CWN data the tracker doesn't have, or features we don't ship:

- `g.current_theme` conditionals (we're always caliBlur)
- `g.google_site_verification`, `g.allow_anonymous`, `cwa_settings`
- Server announcement banner (`#serverAnnouncementBanner`) + dismiss JS
- Library refresh button + `#message_library_refresh` poller + `cwaFetch` helper
- Upload form (`role_upload()`), Tasks (`top_tasks`), Admin (`top_admin`)
- `bookDetailsModal` stub
- Drag-drop merge modal (`#book-merge-modal`) + `drag-drop-merge.js`
- Duplicate notification modal + `duplicate-notifier.js`
- Book organizer confirm modal + `book_organizer.js`
- `custom_css` injection block
- Magic shelves, public shelves, shelf creation (Phase 8 will design its own)
- CWN pagination helper (`pagination`, `url_for_other_page`) — we'll add our own if needed in Phase 7+
- `scriptRoot`, `cwaUserData` JS globals

## JS dependencies

Required for the shell to behave (off-canvas sidebar, dropdown, search form collapse):

| File | Source | Why |
|---|---|---|
| `jquery.min.js` | CDN (already) | Bootstrap 3 + caliBlur.js dependency |
| `bootstrap.min.js` | CDN (already) | Dropdown, collapse |
| `jquery.visible.min.js` | vendor from container | caliBlur.js dependency |
| `intention.min.js` | vendor from container | Powers the `intent in-standard-append` attribute that moves the sidebar into the navbar on mobile |
| `caliBlur.js` | vendor from container | Off-canvas drawer, scroll behavior, profile dropdown interactions |

Vendored to `app/static/js/cwa/`. The `cwa.css`/`caliBlur.css` paths already use the `cwa/` prefix; mirror that for JS so the vendoring story is consistent.

We intentionally **skip**:

- `underscore-umd-min.js`, `context.min.js`, `plugins.js`, `compromise.min.js`, `readmore.min.js`, `main.js` — used only by CWN's content pages
- `book_organizer.js`, `duplicate-notifier.js`, `drag-drop-merge.js`, uploads / progress — admin features
- `CWA-profile-updater.js` — CWN profile sync

If a tracker page later needs one of these (e.g. `readmore.min.js` to truncate long quotes), we vendor it then.

## Migration steps

1. **Vendor JS:** `docker cp` `caliBlur.js`, `jquery.visible.min.js`, `intention.min.js` into `app/static/js/cwa/`.
2. **Rewrite `layout.html`:**
   - Replace the current minimal shell with the structural ports above.
   - Use Jinja blocks: `head_extras`, `body_class`, `navbar_brand`, `sidebar`, `main`, `js`.
3. **Build `app/templates/components/sidebar.html`:** Tracker's `#scnd-nav` partial. Drive the items from a small Python helper (`tracker.nav.sidebar_items()`) so future phases can append without touching templates.
4. **Update `base.html`:** Remove the old `navbar_primary` block (its job moves into the sidebar). Add `body_class` defaulting to a sensible value. Mount `tracker.css` last so it overrides caliBlur where intended.
5. **Per-page templates** (`dashboard.html`, `book_detail.html`, `search.html`):
   - Drop any standalone `.container-fluid` wrappers — they now live in `layout.html`.
   - Add per-page `{% set page = "tracker-dashboard" %}` so `body class` is set correctly.
   - Verify content lives inside `.col-sm-10.content-expanded`.
6. **Visual QA loop:** load each tracker page beside the same area of CWN, verify:
   - Sidebar in same column, same width, same item styling.
   - Navbar height + spacing matches.
   - Mobile drawer slides in/out from the left.
   - Body scrolls within `.col-sm-10.content-expanded`, not the window.
7. **Mobile QA:** hamburger on the left, brand center, drawer open/close, alert toasts position above content.
8. **Regression:** `pytest -q` — all 130 tests stay green (template macros for `book_card.html` etc. shouldn't notice).

## Acceptance criteria

- `/` renders with sidebar on the left, main content on the right, navbar pinned top.
- No 404s in the dev server log on first load.
- No JS console errors.
- Mobile (Chrome devtools 375 × 800): hamburger toggle opens an off-canvas drawer; tapping outside or the toggle closes it.
- `body` carries `blur` + a per-page class on every tracker route.
- All 130 tests pass.
- `tracker.css` continues to apply (status badges, star ratings, book cards on dashboard).

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| `caliBlur.js` references DOM nodes we don't render (`#bookDetailsModal`, `#top_tasks`, …) and console-errors. | Inspect the file before vendoring; either guard with feature detection or stub the missing nodes as hidden empty containers. |
| `intention.min.js`'s `intent` attribute syntax is undocumented; we may misuse it. | Mirror CWN's exact attribute string verbatim. If it misbehaves, drop both files and accept the simpler-but-different mobile behavior. |
| caliBlur ships a structural change in an upstream version. | The vendored CSS is pinned to the container we tested against. Refresh is a manual `docker cp` + visual diff against this doc's "What gets ported" table. |
| `body.<page>` class set incorrectly leaks CWN-specific rules. | Pick `tracker-*` page names (no overlap with CWN's `body.me`, `body.serieslist`, …). |
| Tracker pages grow horizontal scroll because `.col-sm-10` is narrower than the old `.container-fluid.tracker-main`. | Audit `dashboard.html` for any element with hard widths; rely on `.col-sm-10`'s percentage width instead. |

## Out of scope

- Per-card / per-section caliBlur grid rules (already in `tracker.css` and out of this doc's scope).
- Login page styling — separate concern; the tracker doesn't render a login page in cookie mode anyway.
- Replacing Bootstrap 3 with anything modern. Coupling to BS3 is inherited from caliBlur and isn't worth fighting now.

## Refresh playbook (when CWN updates)

1. `docker cp calibre-web:/app/calibre-web-automated/cps/static/css/. /tmp/cwa-css/` — full snapshot.
2. `diff -r app/static/css/cwa/ /tmp/cwa-css/` — review structural deltas.
3. `docker cp` the changed CSS files into `app/static/css/cwa/`.
4. Re-run the "Visual QA loop" above against the new container.
5. Commit with a `refs: cwa@<commit>` line so the upstream point is traceable.

The same playbook applies to vendored JS — `app/static/js/cwa/`.
