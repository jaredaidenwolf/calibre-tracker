# Phase 8 checkpoint — pause for surgery (2026-06-19)

A snapshot of where the project is, what's in flight, and what to pick
up first on return. Written during a development pause; the
implementation summaries in `docs/04-implementation-plan.md` remain the
canonical phase plan.

## State of main

`main` is at `e4af7bc` (Phase 7 merge). 165 tests green.

Everything through **Phase 7 — Quotes & Notes** is merged, pushed, and
shipped:

- Phase 6 dashboard + book detail page (incl. multi-attempt reading
  log, edit/amend split, CWN-link + reload-metadata action buttons,
  CWN-style tooltips, cover hover info-icon).
- Phase 7 quotes + notes with per-book tabs, global `/quotes` index,
  inline favourite/spoiler/edit/delete actions, hash-anchored redirects.

## In-flight work (this is what's still open)

Branch: **`phase-8-shelves`** at `9342aa3`, pushed to origin. **Not
merged into main yet** — there are open UX issues from the latest review
round that should be settled before merge.

What's done on the branch:

- **Service layer** (`app/tracker/service.py`):
  - `create_shelf`, `update_shelf`, `delete_shelf` with per-user
    unique shelf names, `#RRGGBB` colour validation, free-text
    description.
  - `add_book_to_shelf` (idempotent + auto-increments `sort_order`),
    `remove_book_from_shelf` (idempotent), `move_book_in_shelf`
    (`"up"` / `"down"` neighbour swap).
  - New `ShelfValidationError`. `_clean_text` refactored to take an
    `error_cls` so shelves and annotations raise their own types.
- **Routes** (`app/tracker/routes.py`):
  - `GET /shelves`, `GET /shelves/new`, `POST /shelves/new`,
    `GET /shelf/<id>`, `GET/POST /shelf/<id>/edit`,
    `POST /shelf/<id>/delete`.
  - Membership: `POST /shelf/<id>/add-book`,
    `POST /shelf/<id>/remove-book`, `POST /shelf/<id>/move-book`,
    `POST /book/<id>/add-to-shelf`.
  - `GET /shelf/<id>/order` — dedicated reorder list view.
  - `GET /shelves/reorder` — placeholder page (see outstanding work).
  - `book_detail` passes `all_shelves` + `book_shelf_ids` so the
    action bar can render add/remove dropdowns.
- **Sidebar** (`app/tracker/nav.py` + `app/templates/components/sidebar.html`):
  - Shelves section dynamic — one item per user shelf, then
    "+ Create a Shelf" (CWN's `.create-shelf` class), then "Reorder
    Shelves".
  - `SidebarItem.css_class` field added so caliBlur's plex-icons `+`
    pseudo-element renders for the create link.
  - Template skips the inline glyphicon for create-shelf items so
    only one "+" shows.
- **Book detail action bar:**
  - Add-to-shelf and Remove-from-shelf dropdowns, both placed *after*
    the trash icon. Remove only renders when the book is on a shelf.
  - `id="add-to-shelf"` toggle picks up CWN's plex-icons "list" glyph.
  - Menu uses `id="add-to-shelves"` to defeat `caliBlur.js:439`'s
    empty-`<ul>` check (which adds `.empty-ul { pointer-events: none }`
    and was killing the click). Without this rename, the dropdown is
    dead.
  - Toggle sized 44×44 via `!important` overrides on caliBlur's stock
    50×60 + `color: transparent` styling.
  - New "Shelves:" metadata chip after ISBN listing the shelves the
    book is on, each clickable to that shelf.
- **Shelf detail page** (`shelf_detail.html`):
  - Body class includes `shelf` so caliBlur paints the fixed banner h2
    + `.shelf-btn-group` at `top:60px right:0`.
  - Action buttons: Edit (`[href*=edit]`), Reorder (`[href*=order]`),
    Delete (`[data-target="#DeleteShelfDialog"]`). All three trigger
    caliBlur's stock plex-icons paint. **No Download** per the parity
    request.
  - Sort / multi-select / settings toolbar styled as `.tracker-toolbar`
    (matches dashboard / `/quotes`). Controls are disabled placeholders
    pending the data work below.
  - Books in CWN-style grid via `components/book_card.html`.
- **Reorder view** (`shelf_order.html`): list of books with up/down
  arrows + remove button; reachable from the shelf detail page.
- **`/shelves/reorder`** (`shelves_reorder.html`): placeholder page;
  see outstanding work.

Tests: 37 new in `tests/test_shelves.py` (service CRUD, per-user authz,
idempotency, multi-shelf membership, manual ordering, route round-trips,
sidebar wiring). **203 total green** on the branch.

## What's still open (these are the actual TODOs)

In rough priority order:

1. **Sort-order on `Shelf` model.** The "Reorder Shelves" sidebar link
   currently goes to a placeholder page (`/shelves/reorder`). Persisting
   shelf order needs a new `sort_order: int` column on `Shelf`, a
   Flask-Migrate migration, and POST endpoints to swap sort_order with
   the neighbour (same pattern as `move_book_in_shelf` for books). The
   sidebar query then orders by `sort_order` instead of `name`.

2. **Shelf detail toolbar controls.** The sort / multi-select / settings
   buttons render but are `disabled`. Wiring them in:
   - **Sort**: add `book_sort_order` (or similar) preference on
     `Shelf` — "manual", "title", "author", "date added".
     `shelf_detail` route orders books accordingly.
   - **Multi-select**: client-side checkboxes that enable a "Remove
     selected" or "Move selected" bulk action. Needs a small bit of JS;
     should follow CWN's interaction model.
   - **Settings**: per-user view mode (grid vs list) and per-shelf
     defaults. Lowest priority — could defer indefinitely.

3. **`Shelf.is_public` cleanup.** The column exists from Phase 2 but
   the user explicitly said skip it for now. Either drop it via
   migration or leave it dormant — fine either way, just don't surface
   it.

4. **Open visual / UX issues from the latest review round** that
   weren't resolved before the pause. Hard-reload `/shelves` and a few
   shelves to spot-check after merge: there may be alignment / spacing
   gaps inherited from the CWN parity work that need polish, especially
   on the shelf detail page where caliBlur's `body.shelf` rules and our
   `tracker-toolbar` overlap.

5. **Phase 8 acceptance criteria check.** Per
   `docs/04-implementation-plan.md`:
   - [x] Creating a shelf works.
   - [x] Adding books works.
   - [x] Removing books works.
   - [x] Reordering books works (via `/shelf/<id>/order`).
   - [x] Per-user isolation enforced (tests verify).
   - [x] A book can belong to multiple shelves (tests verify).
   - [ ] Reordering *shelves themselves* — blocked on item 1 above.

6. **Merge `phase-8-shelves` into `main`** once items 1–4 are
   either resolved or explicitly deferred.

## After Phase 8

Per `docs/04-implementation-plan.md`:

- **Phase 9 — Stats & Goals.** `app/tracker/stats.py`: books per
  year/month, average rating, longest streak, tag breakdown, rereads
  count. Annual reading goals.
- **Phase 10 — Dockerize & Deploy.** Container image, compose file,
  Unraid notes. (`docs/02-docker-architecture.md` has the deploy
  plan already.)

## Local state / housekeeping

- All 15 local branches are pushed to `origin` (verified via
  `git ls-remote --heads`). Working tree is clean except for this
  document and its companion commit.
- No stashes, no uncommitted work outside this checkpoint.
- Tests: `source .venv/bin/activate && python -m pytest tests/ -q`
  → 203 passed on `phase-8-shelves`, 165 passed on `main`.
- Branch layout: feature branches (`phase-N-...`) live alongside
  in-flight UI-polish branches (`style-...`). Old phase branches are
  archived on remote for reference; they're already merged into `main`
  and 0 commits ahead.

## Picking it up

1. `git checkout phase-8-shelves && git pull`.
2. Re-run the test suite to confirm clean baseline.
3. Read the **What's still open** section above and pick item 1 (it
   unblocks the sidebar link from being a placeholder) or item 4
   (visual polish) depending on energy.
4. When the branch feels shippable, merge into `main` with `--no-ff`
   following the pattern of past merge commits (see `git log` on
   `main`).
