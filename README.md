# Calibre Reading Tracker

A self-hosted reading tracker that runs as a companion to
[Calibre-Web NextGen][cwn] (CWN). Think StoryGraph or Goodreads, minus the
social features and minus the lock-in — your library lives in Calibre and the
tracker just *adds* a reading log on top of it: status, dates, rating, review,
rereads, quotes, notes, shelves, and goals.

The tracker reads book metadata from Calibre's `metadata.db` (read-only), reads
user identity from CWN's `app.db` (read-only — so an existing CWN login signs
you straight in), and writes its own reading data to a separate `tracker.db`
that you own outright.

> **Project status — 2026-06-14:** Phases 0–6 of the implementation plan are
> complete. The MVP is shippable: log in, see your books grouped by status,
> click into a book, set status / rating / review / dates. Phases 7–10 (quotes
> & notes, shelves, stats & goals, Docker packaging) are pending. See
> [`docs/04-implementation-plan.md`](docs/04-implementation-plan.md) for the
> roadmap and per-phase progress.

## Table of contents

- [What it does](#what-it-does)
- [Architecture at a glance](#architecture-at-a-glance)
- [Running it (development)](#running-it-development)
- [Configuration](#configuration)
- [Calibre-Web NextGen setup](#calibre-web-nextgen-setup)
- [Optional CWN navbar override](#optional-cwn-navbar-override)
- [Contributing](#contributing)
- [Project layout](#project-layout)
- [Further reading](#further-reading)

## What it does

**Done (Phases 0–6):**

- **Rides your CWN session.** A user already logged into Calibre-Web NextGen
  is transparently authenticated against the tracker — no separate password.
  The bridge validates CWN's signed cookie *and* cross-checks the
  `user_session` table on every request, so a remote logout from CWN
  immediately invalidates outstanding tracker sessions.
- **Reads Calibre, never writes it.** `metadata.db` and CWN's `app.db` are
  opened in SQLite `mode=ro`. Writes would be rejected at the driver layer
  even if a bug tried to issue one.
- **Mirrors your CWN read history once.** On first login the tracker imports
  any books you've already marked read in CWN (`book_read_link.read_status = 1`)
  as `status='read'` rows in your reading log — no dates or rating, just so
  you don't start from zero. The import is one-shot, additive (never
  overwrites pre-existing tracker data), and gated by a `cwn_import_completed`
  flag.
- **Dashboard.** Books grouped into Currently Reading / Want to Read / Recently
  Finished / Did Not Finish, plus a quick-stats strip (books read this year,
  currently reading, want to read, average rating).
- **Book detail.** Cover, metadata, and a form for status / rating / dates /
  review. Status transitions auto-fill `started_at` and `finished_at` when
  you leave them blank. Manual dates are never clobbered. Rereads insert a
  new row instead of overwriting the original first-read.
- **Search.** Title / author / ISBN search against your Calibre library, with
  per-result links into the book detail page (and a status badge for books
  already in your log).
- **caliBlur theme parity.** The tracker vendors CWN's `caliBlur` /
  `caliBlur_override` / `cwa` / `style` stylesheets and serves them
  alongside a tracker-native `layout.html` shell — same fonts, colors,
  Bootstrap 3 navbar, `body.blur` class — so pages look like CWN's. The
  tracker's own CSS only *extends* caliBlur (custom properties, status
  badges, star ratings, progress bars) and never overrides base variables.

**Coming next (Phases 7–10):**

- Phase 7 — quotes & notes per book, plus a global quotes view.
- Phase 8 — user-defined shelves beyond the core statuses.
- Phase 9 — stats & annual reading goals (dashboard widget + dedicated page).
- Phase 10 — `Dockerfile`, `docker-compose.yml`, Unraid template, deploy docs.

## Architecture at a glance

```
                ┌────────────────────────────────────────────────────┐
                │                  Reverse proxy                     │
                │   (Nginx Proxy Manager / Traefik — same domain)    │
                └──────────────────────┬─────────────────────────────┘
                                       │
        ┌──────────────────────────────┼──────────────────────────────┐
        │                              │                              │
        ▼                              ▼                              ▼
   /tracker/…                      / (CWN UI)                     /static/…
        │                              │                              │
        │  shared session cookie       │
        │  (CWN signed it; tracker     │
        │   verifies it)               │
┌───────▼─────────┐            ┌───────▼─────────┐
│  Tracker (Flask)│            │   Calibre-Web   │
│   :8084         │            │    NextGen      │
│                 │            │    :8083        │
└───┬──────┬──────┘            └────┬────────────┘
    │      │                        │
    │      │  read-only             │  read-write
    │      ▼                        ▼
    │  ┌────────────┐         ┌────────────┐
    │  │ app.db     │◄────────│  app.db    │
    │  │ (CWN auth) │         │ (CWN data) │
    │  └────────────┘         └────────────┘
    │
    │  read-only                    read-only
    │       │                            │
    │       ▼                            ▼
    │  ┌─────────────┐             ┌────────────┐
    │  │ metadata.db │◄────────────│ Calibre    │ (desktop / Calibre-Web write)
    │  │ (Calibre)   │             │  library   │
    │  └─────────────┘             └────────────┘
    │
    │  read-write
    ▼
┌────────────┐
│ tracker.db │ (the only DB the tracker writes to)
└────────────┘
```

Three SQLite databases, three roles. The full data model is in
[`docs/01-data-model.md`](docs/01-data-model.md); the docker / volume /
reverse-proxy story is in
[`docs/02-docker-architecture.md`](docs/02-docker-architecture.md); the
auth bridge and theming choices are in
[`docs/03-auth-and-theming.md`](docs/03-auth-and-theming.md).

## Running it (development)

**Prerequisites**
- Python 3.12+ (Phase 10's Docker image will pin 3.12; locally 3.12–3.14 work)
- A running Calibre-Web NextGen container if you want to test the auth bridge
  end-to-end (otherwise the fixture databases under `tests/fixtures/` are
  enough for everything else).

**Set up the venv and install dependencies**

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

**Configure your `.env`**

Copy the template and fill in the secrets:

```bash
cp .env.example .env
$EDITOR .env
```

The two required secret values:

```dotenv
# Generate with: openssl rand -hex 32
TRACKER_SECRET_KEY=<your-generated-value>

# Must match the SECRET_KEY env var in your Calibre-Web NextGen container
CWA_SECRET_KEY=<must-match-cwn>
```

For local development against the bundled fixtures, the other paths can stay
as the defaults that ship in `.env.example`. See [Configuration](#configuration)
below for the full env-var table.

**Apply migrations and boot the dev server**

```bash
mkdir -p instance
.venv/bin/flask db upgrade
.venv/bin/flask run
```

Then `curl http://localhost:5000/health` should return `{"status":"ok"}`. To
hit the dashboard you'll need a CWN session cookie — either set up a local
CWN container with the same `SECRET_KEY`, or write a one-off script that
mints a cookie via `app.auth.cwa_bridge.encode_cwa_session` (test code does
this — see `tests/test_dashboard.py::_login`).

**Run the tests**

```bash
.venv/bin/pytest -q
```

Should print `124 passed`. The full suite hits sub-second wall-clock —
nothing in CI is slow, the fixtures are tiny.

**Linting / formatting**

`ruff` config lives in `pyproject.toml`. The standard cycle:

```bash
.venv/bin/ruff check .
.venv/bin/ruff format .
```

Run both before each commit.

## Configuration

Every knob is an environment variable. Full table:

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `TRACKER_SECRET_KEY` | yes | — | The tracker's own Flask session signing key. Generate with `openssl rand -hex 32`. |
| `CWA_SECRET_KEY` | yes (for cookie auth) | empty | CWN's `SECRET_KEY`, used to verify CWN's signed session cookie. Must match the value in CWN's container. |
| `CALIBRE_DB_PATH` | yes | `/calibre-library/metadata.db` | Read-only path to Calibre's `metadata.db`. |
| `CALIBRE_LIBRARY_PATH` | recommended | parent of `CALIBRE_DB_PATH` | Library root, used to resolve and stream covers via `/cover/<id>`. |
| `CWA_DB_PATH` | yes | `/cwa/app.db` | Read-only path to CWN's `app.db`. |
| `TRACKER_DB_PATH` | yes | `/config/tracker.db` | The tracker's own writable SQLite database. |
| `CWA_COOKIE_PREFIX` | no | `""` | Mirrors CWN's `COOKIE_PREFIX` env var (cookie name = `f"{prefix}session"`). Confirm with `docker exec calibre-web printenv COOKIE_PREFIX`. |
| `CWA_BASE_URL` | no | `/` | Where the "Library" nav link and `/auth/logout` redirect to. |
| `AUTH_MODE` | no | `cookie` | `cookie` (ride CWN's session) or `form` (bcrypt fallback against CWN's `user` table). |
| `FLASK_CONFIG` | no | `dev` | `dev` / `prod` / `test`. |
| `FLASK_APP` | dev only | — | Set to `app:create_app` for `flask run` / `flask db …`. |
| `LOG_LEVEL` | no | `INFO` | Python logging level. |
| `MAX_CONTENT_LENGTH` | no | `16777216` | Max upload size (16 MiB; reserved for future cover uploads). |
| `TZ` | no | container default | Container timezone. |

`.env.example` carries the same set as a template.

## Calibre-Web NextGen setup

One change is required on the CWN side: add a `SECRET_KEY` to the CWN
container's environment so both apps can agree on it.

```yaml
# in your calibre-web-nextgen docker-compose.yml
services:
  calibre-web:
    image: ghcr.io/new-usemame/calibre-web-nextgen:latest
    environment:
      # ... your existing env ...
      - SECRET_KEY=<your-generated-value>   # same value as CWA_SECRET_KEY in the tracker .env
```

Then restart CWN once:

```bash
docker compose up -d calibre-web
```

Existing CWN sessions are invalidated by the key change — log in to CWN once
after the restart and you're good. The tracker now uses the same key to
verify CWN's signed session cookie.

You do **not** need to expose `app.db` to the network or run any special
patches on the CWN side; the tracker reads it through a Docker bind mount
in `mode=ro`.

## Optional CWN navbar override

CWN supports per-deployment template overrides in `/config/templates/`.
This repo ships `cwa-override/templates/layout.html` — a copy of CWN's
current `layout.html` with one extra `<li>` in the navbar linking to
`/tracker/`. Install it (optional, doesn't break anything):

```bash
# Unraid
mkdir -p /mnt/user/appdata/calibre-web-nextgen/config/templates
cp cwa-override/templates/layout.html /mnt/user/appdata/calibre-web-nextgen/config/templates/layout.html
docker restart calibre-web
```

See [`cwa-override/README.md`](cwa-override/README.md) for refresh
instructions when CWN ships a new `layout.html`.

## Contributing

The project is being built phase-by-phase against
[`docs/04-implementation-plan.md`](docs/04-implementation-plan.md). If you
want to contribute, the most useful starting points are:

1. Read the plan and the three companion docs (`01-data-model.md`,
   `02-docker-architecture.md`, `03-auth-and-theming.md`).
2. Pick up the next phase in the plan, or fix a real bug you hit
   running the MVP.
3. Branch per phase: `phase-N-short-name` (or `fix-…` / `docs-…` /
   `chore-…`).
4. Conventional Commits style for commit messages
   (`feat:`, `fix:`, `chore:`, `test:`, `docs:`).
5. Type hints on all functions; docstrings on all public functions and
   modules; tests live in `tests/` and run against fixture databases.
6. `ruff check .` and `ruff format .` before each commit. CI doesn't
   exist yet but the pre-commit hooks will when we wire them up.
7. Read-only safety: any code path touching `metadata.db` or `app.db`
   must use a `mode=ro` SQLite URI. The fixtures verify this; new code
   should too.
8. Per-user scoping: every reading-log / quote / note / shelf / goal
   query must filter by `current_user.id`. Never trust `book_id`
   ownership from a URL.

The `tests/fixtures/` directory ships a small `metadata.db` and `app.db`
with two users and a handful of books — they're enough to run the full
suite without needing a live CWN.

## Project layout

```
calibre-tracker/
├── app/                              # The Flask application
│   ├── __init__.py                   # create_app() factory
│   ├── config.py                     # Config / DevConfig / ProdConfig / TestConfig
│   ├── extensions.py                 # db, login_manager, migrate, csrf singletons
│   │
│   ├── auth/
│   │   ├── cwa_bridge.py             # Read-only CWN session validation
│   │   └── routes.py                 # before_request hook, /auth/login, /auth/logout
│   │
│   ├── calibre/
│   │   ├── models.py                 # Read-only ORM mapping of metadata.db
│   │   └── repository.py             # BookDTO + get_book / get_books / search / cover_path
│   │
│   ├── tracker/
│   │   ├── models.py                 # User, ReadingLog, ReadingSession, Quote, Note,
│   │   │                             #   Shelf, ShelfBook, ReadingGoal
│   │   ├── service.py                # validation, upsert, rereads, CWN import gate
│   │   └── routes.py                 # /, /book/<id>, /search, /cover/<id>, JSON CRUD
│   │
│   ├── templates/
│   │   ├── layout.html               # Tracker-native caliBlur shell
│   │   ├── base.html                 # Adds tracker.css + nav
│   │   ├── components/               # book_card, rating_stars, progress_bar
│   │   └── tracker/                  # dashboard, book_detail, search
│   │
│   └── static/
│       └── css/
│           ├── tracker.css           # Tracker's own extensions
│           └── cwa/                  # Vendored CWN CSS (verbatim copies)
│
├── migrations/                       # Alembic; only manages tracker.db
│   └── versions/
│       └── 6a8b4ac91bd6_initial_schema.py
│
├── cwa-override/                     # Optional CWN navbar override
│   ├── README.md
│   └── templates/layout.html
│
├── tests/
│   ├── conftest.py                   # App / client / fixture-DB fixtures
│   ├── fixtures/                     # Small metadata.db, app.db, on-disk covers
│   │   ├── build_metadata_fixture.py
│   │   └── build_cwa_fixture.py
│   └── test_*.py                     # 124 tests at MVP cut line
│
├── docs/
│   ├── 01-data-model.md
│   ├── 02-docker-architecture.md
│   ├── 03-auth-and-theming.md
│   └── 04-implementation-plan.md
│
├── .env.example
├── pyproject.toml                    # ruff + pytest config
├── requirements.txt
├── requirements-dev.txt
└── README.md (this file)
```

## Further reading

- [`docs/01-data-model.md`](docs/01-data-model.md) — full schema for all three
  databases, ERDs, soft-FK rules, and indexes.
- [`docs/02-docker-architecture.md`](docs/02-docker-architecture.md) — volume
  mounts, container layout, env var reference, request/response flow.
- [`docs/03-auth-and-theming.md`](docs/03-auth-and-theming.md) — the CWN
  session bridge mechanics and the caliBlur theming approach.
- [`docs/04-implementation-plan.md`](docs/04-implementation-plan.md) — the
  phased plan, with merged-commit hashes and acceptance criteria checked off
  per phase.

[cwn]: https://github.com/new-usemame/calibre-web-nextgen
