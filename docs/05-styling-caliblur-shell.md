# 05 — Styling: caliBlur Shell Port

> **Status:** Shipped (merged on `main`). This doc is the design + implementation record. Implementation lived on `style-caliblur-shell` over four commits (`a6d59df` planning doc → `d321a61` shell port → `436fc42` main-area alignment → `e5beaf8` book card alignment) and merged at `7ef5837`.

## Context

The tracker already vendored every caliBlur stylesheet from the live CWN container (`style.css`, `caliBlur.css`, `caliBlur_override.css`, `cwa.css`) and rendered pages with `body.blur` so the dark-theme colors applied. What was missing was the **DOM scaffold** those stylesheets expect.

caliBlur is a layout-coupled theme: a single `grep` for its load-bearing selectors finds **454 hits** in `caliBlur.css` alone. The big ones:

- `body.blur` + a page-name class (`body.serieslist`, `body.authorlist`, `body.me`, …)
- `.container-fluid > .row-fluid > .col-sm-2` (sidebar) + `.col-sm-10` (main)
- `#main-nav` (right navbar `ul`) and `#scnd-nav` (sidebar `ul`)
- `.profileDrop`, `.home-btn`, `.plexBack`
- `.col-sm-10.content-expanded` is the **scrolling element** — caliBlur explicitly does *not* scroll on `window`
- `#books > .cover > a` (and variants) is the **book card** hook — without it, no hover overlay, no glyph button, no caliBlur typography

The pre-port `layout.html` had none of those. Result: colors right, layout wrong.

## Decision

Port only the **structural shell** of CWN's `layout.html`, not the whole file. CWN's `layout.html` is 781 lines, ~75% of which is CWN-specific functionality (admin, upload, refresh, drag-merge, duplicate-notifier, …). We replicate the DOM caliBlur targets and leave the rest behind.

This keeps:
- A small, readable `layout.html` we own
- Decoupling from CWN's role/feature gates
- A clean override surface for tracker-specific UI

…at the cost of a one-time port plus a refresh whenever caliBlur ships material DOM changes (rare in practice).

## What we shipped

### Vendored assets

| File | Source path in CWN container | Purpose |
|---|---|---|
| `app/static/js/cwa/caliBlur.js` | `/app/calibre-web-automated/cps/static/js/caliBlur.js` | Off-canvas drawer, scroll behavior, profile dropdown interactions, search-focus toggle |
| `app/static/js/cwa/intention.min.js` | `/app/calibre-web-automated/cps/static/js/libs/intention.min.js` | Powers `intent in-standard-append="…"` on the sidebar — moves it into the navbar on mobile |
| `app/static/js/cwa/jquery.visible.min.js` | `/app/calibre-web-automated/cps/static/js/libs/jquery.visible.min.js` | caliBlur.js dependency |
| `app/static/css/cwa/book_organizer.css` | `/app/calibre-web-automated/cps/static/css/book_organizer.css` | Cover-badge styles (`.cover-badges`, `.cover-badge`, `.cover-badge-read`) and the Phase 8 sort/multi-select bar styles |
| `app/static/css/cwa/images/caliblur/*.{webp,png}` | `/app/calibre-web-automated/cps/static/css/images/caliblur/` | Background noise + light/dark blur textures referenced by `caliBlur.css` |

### Layout scaffold

| Element | DOM | Why it's load-bearing |
|---|---|---|
| Body | `<body class="<page> blur">` | Triggers theme; per-page class drives variant rules |
| Hidden CSRF | `<input type="hidden" name="csrf_token">` | `caliBlur.js` + `cwaFetch` helper read it |
| Navbar | `.navbar.navbar-default.navbar-static-top` | Fixed-top dark bar |
| Toggle | `.navbar-header > .navbar-toggle` | Mobile hamburger; position overridden in `caliBlur_override.css` |
| Brand | `.navbar-brand` | Brand link |
| Home / Back | `.home-btn` + `.plexBack` (anchor-only) | Plex-style nav buttons — caliBlur paints them via CSS pseudo-elements |
| Search slot | `.navbar-form.navbar-left > input#query` | `id="query"` is load-bearing — `caliBlur.js` binds focus/focusout on `input#query` to toggle `form.search-focus` (white focus background + orange arrow button). Wired to `tracker.search`. |
| Right nav | `ul.nav.navbar-nav.navbar-right#main-nav` | Holds profile dropdown |
| Profile dropdown | `.dropdown > a.profileDrop` + `.dropdown-menu.profileDropli` | Avatar circle + menu (Library link + Logout) |
| Sidebar column | `.row-fluid > .col-sm-2 > nav.navigation` | Off-canvas drawer container |
| Sidebar list | `ul.list-unstyled#scnd-nav` | All sidebar items go here, with `.nav-head` headers |
| Main column | `.col-sm-10.content-expanded` | Scrolling content area |
| Flash row | `.row-fluid.text-center > .alert.alert-<level>` | caliBlur fixes alerts as toasts |

### Body page classes

Each tracker route sets a `tracker-*` class on `<body>` via `{% block body_class %}` so caliBlur's `body.serieslist` / `body.me` / etc. rules don't collide:

- `body.tracker-dashboard` — Overview
- `body.tracker-status-list` — per-status views
- `body.tracker-book` — book detail
- `body.tracker-search` — search results

### Sidebar

Items live in `app/tracker/nav.py` as `SidebarItem` / `SidebarSection` dataclasses, exposed to templates via a context processor (`tracker_sidebar_sections`, `tracker_sidebar_url`, `tracker_sidebar_active`). The `components/sidebar.html` partial renders the standard caliBlur `#scnd-nav` markup.

Current sections + items:

- **My Reading**: Overview, All, Currently reading, Want to read, Finished, Did not finish
- **Quotes & Notes**: Quotes (placeholder — Phase 7)
- **Shelves**: Shelves (placeholder — Phase 8)
- **Stats**: Reading stats, Goals (placeholders — Phase 9)

Status items point at `tracker.status_list` with the status as a URL kwarg; placeholders render as inert anchors with a `title=` tooltip.

### Per-status list views

Built `GET /list/<status>` (`tracker.status_list`) on top of the shell:

- `/list/all` — every logged book (excluding rereads), freshest-first
- `/list/reading|want_to_read|read|dnf|re_reading` — single status

Single-section pages so they CAN use `.discover` (see "Discoveries" below) and get caliBlur's fixed page-title banner. The dashboard caps each section at 5 books and links here for the full list.

### Book card markup

Adopted **CWN's `index.html` markup verbatim** — `book session` + `id="books"` on the Bootstrap column wrapper, with `.cover > a.book-cover-link > .img > img` and `.meta > .title / .author` children. caliBlur's CSS hits the right selectors and paints cover hover overlay, orange glyph button, 13px Open Sans typography, and the 180px-with-flex-wrap responsive grid for free.

The `components/book_card.html` partial only renders the `.cover` and `.meta` children; the parent template owns the column wrapper.

### Cover badges

Mirror CWN's `image.cover_badges` macro:

```html
<div class="cover-badges">
  <span class="cover-badge cover-badge-status badge-{status}" title="…">
    <span class="glyphicon glyphicon-{icon}"></span>
    <span class="cover-badge-status-label">{label}</span>
  </span>
</div>
```

Base styling (bottom-left, pill shape, backdrop blur, 11px) comes from `book_organizer.css`. The `.badge-{status}` modifier (shared with the book-detail page) tints the background:

| Status | Glyphicon | Background |
|---|---|---|
| reading | `book` | `--tracker-reading` (#52a0e0) |
| want_to_read | `bookmark` | `--tracker-want` (#a052e0) |
| read | `check` | `--tracker-read` (#2c8b3a — CWN's `cover-badge-read` green) |
| dnf | `remove` | `--tracker-dnf` (#e05252) |
| re_reading | `refresh` | `--tracker-rereading` (#c9a96e) |

### Dashboard layout & spacing

Mirrors CWN's `index.html`:

- Single `<div class="discover"><h2>Overview</h2></div>` at the top so caliBlur paints the fixed page-title banner
- A stats strip styled like CWN's `.shelf-actions` (`padding: 1.5rem`, `background: rgba(32,44,53,.55)`, `border-radius: 4px`, `margin-bottom: 20px`)
- Per-status sections in tracker-owned `<section class="tracker-section">` wrappers (NOT `.discover` — see "Discoveries")

**Spacing math** (every horizontal value pulled from caliBlur.css or CWN's `index.html` `<style>` block):

| Element | Left from `.col-sm-10` edge | Right |
|---|---|---|
| Stats strip outer | 35px | 35px |
| Section content (`.row` start) | 35px (`section padding-left`) | 35px |
| First book column content | 50px (35 + 15 `.col` padding) | 50px |

The 35px figure on the strip and section padding matches CWN's `.discover` padding (`0 10px 0 20px`) plus `.shelf-actions` `margin-inline: 15px` — i.e. the position a CWN `.shelf-actions` ends up in.

Bootstrap's default `-15px` row gutter is stripped on `.tracker-section > .row.display-flex` to match `body.blur .discover .row { margin: 0 auto !important }` in caliBlur.

### Section header typography

Each per-status section header on the dashboard renders:

```
{Title} ({count})                              View all »
```

- Title: 1.4rem, 600 weight, `hsla(0, 0%, 100%, 0.85)`
- Count: parens, 1.4rem (matches title), white at 0.85 opacity
- View all link: 1.15rem, white at 0.85 opacity, always shown for any non-empty section
- A 1px bottom border separates the header from the grid

## Discoveries during implementation

These weren't in the original plan and shaped a lot of the final design:

1. **`.discover > h2` becomes a fixed top banner.** caliBlur turns every `.discover > h2` into a `position: fixed; top: 60px; height: 60px` banner pinned at the top of `.col-sm-10` (caliBlur.css:1974). It's beautiful for single-section pages (matches CWN's "Books" / "Authors" sticky header), but on a multi-section page like the dashboard every section's h2 would pile up at the same coordinates. The dashboard ended up using `.discover` *only* for the page title (`Overview`) and tracker-owned `<section class="tracker-section">` wrappers with inline headers for the per-status sections.

2. **Cover badges live in `book_organizer.css`, not the four CSS files we vendored first.** CWN's `image.cover_badges` macro outputs `<div class="cover-badges"><span class="cover-badge cover-badge-read">…` and the styling for those is in `book_organizer.css` (which also carries the upcoming sort/select toolbar). Vendored it as a fifth CSS file.

3. **caliBlur locks `.book` to 180px and `.cover img` to 150px, and that's the right answer.** Early on we tried to override these to make the book cards "responsive". Removing every width override (mid-iteration) revealed that caliBlur's intended responsive behavior is "180px cards with `flex-wrap: wrap`" — at typical viewports that gives 2 / 4 / 5-6 across naturally. Less code, identical to CWN.

4. **Bootstrap's `-15px` row gutter sneaks into multi-section pages.** caliBlur strips it via `body.blur .discover .row { margin: 0 auto !important }`, but our `<section class="tracker-section">` wrappers aren't `.discover`, so the gutter came back. Added a one-line override.

5. **Stats strip horizontal margin = nested CWN math.** CWN's `.shelf-actions` is `margin-inline: 15px` *inside* `.discover` (which is `padding: 0 10px 0 20px`). The visible result is a left offset of 35px and a right offset of 25px (later bumped to 35px for symmetry per user preference). Our tracker doesn't wrap the dashboard body in a `.discover`, so the margin values are pre-summed onto `.tracker-stats-strip` directly.

## What we deliberately did NOT port

Everything driven by CWN data the tracker doesn't have, or features we don't ship:

- `g.current_theme` conditionals (we're always caliBlur)
- `g.google_site_verification`, `g.allow_anonymous`, `cwa_settings`
- Server announcement banner (`#serverAnnouncementBanner`) + dismiss JS
- Library refresh button + `#message_library_refresh` poller + `cwaFetch` helper
- Upload form (`role_upload()`), Tasks (`top_tasks`), Admin (`top_admin`)
- `bookDetailsModal` stub
- Drag-drop merge modal + `drag-drop-merge.js`
- Duplicate notification modal + `duplicate-notifier.js`
- Book organizer confirm modal + `book_organizer.js` (the bar markup will come back in Phase 8; the JS won't)
- `custom_css` injection block
- Magic shelves, public shelves, shelf creation (Phase 8 will design its own)
- CWN pagination helper (`pagination`, `url_for_other_page`) — we'll add our own when a page needs it
- `scriptRoot`, `cwaUserData` JS globals
- `underscore-umd-min.js`, `context.min.js`, `plugins.js`, `compromise.min.js`, `readmore.min.js`, `main.js` — CWN content-page deps
- `CWA-profile-updater.js` — CWN profile sync

## Refresh playbook (when CWN updates)

The same caliBlur version is pinned across our vendored files. When CWN ships a new release worth tracking:

1. `docker cp calibre-web:/app/calibre-web-automated/cps/static/css/. /tmp/cwa-css/`
2. `diff -r app/static/css/cwa/ /tmp/cwa-css/` — review structural deltas. Pay attention to:
   - Selectors targeting `.book`, `.cover`, `#books`, `.meta` (book card shape)
   - Selectors targeting `.discover` or `.col-sm-10` (page layout)
   - New `:before` / `:after` pseudo-elements on covers (hover effects)
3. `docker cp` the changed CSS files into `app/static/css/cwa/`.
4. Re-run the dashboard + `/list/<status>` + book detail pages visually against the new container.
5. Commit with a `refs: cwa@<commit>` line so the upstream point is traceable.

Same playbook for vendored JS (`app/static/js/cwa/`) and the `app/static/css/cwa/images/caliblur/` asset directory.
