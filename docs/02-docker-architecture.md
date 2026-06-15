---
created: 2026-06-12
modified: 2026-06-14
---
# Calibre Reading Tracker — Docker & Project Architecture

```table-of-contents
```

> **Status (2026-06-14):** the project directory structure, request/response flow, and volume-mount layout below are accurate against the current code. The actual `Dockerfile`, `docker-compose.yml`, and Unraid template are still forward-looking — they ship in Phase 10. Until then, local development uses fixture databases under `tests/fixtures/` (see the project `README.md`).

## Container Strategy

The tracker runs as its own Docker container on your Unraid server, alongside the existing Calibre and CWA containers. It shares volumes with both — read-only access to the Calibre library, and read access to CWA's app database for auth.

```mermaid
graph TB
    subgraph Unraid Host
        subgraph Volumes
            LIB["/mnt/user/media/books<br/>Calibre Library + metadata.db"]
            CWDB["/mnt/user/appdata/calibre-web-nextgen/config/app.db<br/>CWN Users + Sessions"]
            TDB["/mnt/user/appdata/calibre-tracker/tracker.db<br/>Tracker Data"]
            TCFG["/mnt/user/appdata/calibre-tracker/config"]
        end

        subgraph Docker Containers
            CAL["📚 Calibre<br/>(Desktop App Container)<br/>Port: 8080"]
            CWA["🌐 Calibre Web NextGen<br/>(Flask / Python)<br/>Port: 8083"]
            TRK["📖 Calibre Tracker<br/>(Flask / Python)<br/>Port: 8084"]
        end

        subgraph Reverse Proxy
            NP["Nginx Proxy Manager<br/>or Traefik"]
        end
    end

    Browser["👤 Browser"]

    LIB -- "read/write" --> CAL
    LIB -- "read-only" --> CWA
    LIB -- "read-only" --> TRK
    CWDB -- "read-only" --> TRK
    TDB -- "read/write" --> TRK

    Browser --> NP
    NP --> CWA
    NP --> TRK

    style TRK fill:#2d6a4f,color:#fff
    style LIB fill:#1b4332,color:#fff
    style TDB fill:#1b4332,color:#fff
```

## Prerequisite: CWN must use host-readable bind mounts

The tracker reads CWN's `app.db` (auth) and Calibre's `metadata.db` (library) from the same host paths CWN mounts into its container. This only works if CWN's `/config` and `/calibre-library` are bound to **host directories the tracker container can also mount**.

If CWN's compose uses VM-internal bind mounts — e.g. on macOS Docker Desktop, a literal `- /config:/config` resolves inside the Docker VM, not on the host — the tracker has nothing to read on the host side. Migrate CWN's compose to host paths before deploying the tracker:

```yaml
# CWN docker-compose.yml — host-readable paths
volumes:
  - /mnt/user/appdata/calibre-web-nextgen/config:/config
  - /mnt/user/media/books:/calibre-library
  - /mnt/user/appdata/calibre-web-nextgen/ingest:/cwa-book-ingest
```

(For local macOS development, swap `/mnt/user/...` for a host path like `~/calibre-web/config`.) Migrate any existing data with `docker cp <container>:/config/. <host-path>/config/` before recreating the container.

## Volume Mounts

| Host Path | Container Path | Mode | Purpose |
|---|---|---|---|
| `/mnt/user/media/books` | `/calibre-library` | `ro` | Calibre `metadata.db` + cover images |
| `/mnt/user/appdata/calibre-web-nextgen/config/app.db` | `/cwa/app.db` | `ro` | CWN user/session data for auth |
| `/mnt/user/appdata/calibre-tracker` | `/config` | `rw` | `tracker.db` + app config + logs |

> **Adjust host paths** to match your actual Unraid share layout. The appdata path for Calibre Web NextGen may differ from the upstream CWA path — check your container's actual `/config` mount. The important thing is that `metadata.db` and `app.db` are mounted read-only — the tracker should never write to either.

## `docker-compose.yml`

While Unraid's Apps tab handles container creation via templates, keeping a `docker-compose.yml` alongside your project is useful for local development and documentation.

```yaml
version: "3.9"

services:
  calibre-tracker:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: calibre-tracker
    restart: unless-stopped
    ports:
      - "8084:8084"
    volumes:
      # Calibre library — read-only
      - /mnt/user/media/books:/calibre-library:ro
      # CWA (NextGen) app.db for auth — read-only
      - /mnt/user/appdata/calibre-web-nextgen/config/app.db:/cwa/app.db:ro
      # Tracker persistent data
      - /mnt/user/appdata/calibre-tracker:/config:rw
    environment:
      - TRACKER_SECRET_KEY=${TRACKER_SECRET_KEY}
      - CALIBRE_DB_PATH=/calibre-library/metadata.db
      - CWA_DB_PATH=/cwa/app.db
      - TRACKER_DB_PATH=/config/tracker.db
      - LOG_LEVEL=INFO
      - TZ=America/New_York
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8084/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

## `Dockerfile`

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y \
    sqlite3 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY . .

# Create config directory (will be overridden by volume mount)
RUN mkdir -p /config

EXPOSE 8084

CMD ["gunicorn", "--bind", "0.0.0.0:8084", "--workers", "2", "--timeout", "60", "app:create_app()"]
```

## Unraid Community Apps Template

When packaging for the Unraid Apps tab, you'll need a template XML. Key fields:

```xml
<Container>
  <Name>calibre-tracker</Name>
  <Repository>your-dockerhub/calibre-tracker:latest</Repository>
  <Category>MediaApp:Books</Category>
  <WebUI>http://[IP]:[PORT:8084]/</WebUI>
  <Config Name="AppData" Target="/config" Default="/mnt/user/appdata/calibre-tracker" Mode="rw" Type="Path"/>
  <Config Name="Calibre Library" Target="/calibre-library" Default="/mnt/user/media/books" Mode="ro" Type="Path"/>
  <Config Name="CWA App DB" Target="/cwa/app.db" Default="/mnt/user/appdata/calibre-web-nextgen/config/app.db" Mode="ro" Type="Path"/>
  <Config Name="Port" Target="8084" Default="8084" Type="Port"/>
  <Config Name="Secret Key" Target="TRACKER_SECRET_KEY" Type="Variable"/>
  <Config Name="Timezone" Target="TZ" Default="America/New_York" Type="Variable"/>
</Container>
```

## Project Directory Structure

```
calibre-tracker/
│
├── app/
│   ├── __init__.py              # create_app() factory
│   ├── extensions.py            # db, login_manager, etc.
│   ├── config.py                # Config classes (dev/prod)
│   │
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── routes.py            # /login, /logout (delegates to CWA session)
│   │   └── cwa_bridge.py        # Reads CWA app.db to validate users/sessions
│   │
│   ├── calibre/
│   │   ├── __init__.py
│   │   └── models.py            # Read-only ORM models for metadata.db
│   │
│   ├── tracker/
│   │   ├── __init__.py
│   │   ├── models.py            # SQLAlchemy models for tracker.db
│   │   ├── routes.py            # Main tracker views
│   │   └── stats.py             # Reading stats / goal calculations
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1.py                # JSON API endpoints (for future HTMX / JS use)
│   │
│   ├── templates/
│   │   ├── base.html            # Extends caliBlur! layout
│   │   ├── auth/
│   │   │   └── login.html
│   │   ├── tracker/
│   │   │   ├── dashboard.html
│   │   │   ├── book_detail.html
│   │   │   ├── shelves.html
│   │   │   ├── quotes.html
│   │   │   └── stats.html
│   │   └── components/
│   │       ├── book_card.html
│   │       ├── rating_stars.html
│   │       └── progress_bar.html
│   │
│   └── static/
│       ├── css/
│       │   └── tracker.css      # Extensions to caliBlur! — DO NOT override base
│       ├── js/
│       │   └── tracker.js
│       └── img/
│           └── tracker-logo.svg
│
├── migrations/                  # Alembic migrations for tracker.db
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 001_initial_schema.py
│
├── tests/
│   ├── test_auth.py
│   ├── test_reading_log.py
│   └── conftest.py
│
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

## Request / Response Flow

```mermaid
sequenceDiagram
    participant Browser
    participant Tracker as Calibre Tracker :8084
    participant CWA_DB as CWA app.db (read-only)
    participant CAL_DB as metadata.db (read-only)
    participant TRK_DB as tracker.db (read/write)

    Browser->>Tracker: GET /dashboard (with session cookie)
    Tracker->>CWA_DB: Validate session token → get cwa_user_id
    CWA_DB-->>Tracker: User record
    Tracker->>TRK_DB: Get or create tracker user by cwa_user_id
    TRK_DB-->>Tracker: Tracker user + reading log entries
    Tracker->>CAL_DB: Fetch book metadata for log entries (title, author, cover path)
    CAL_DB-->>Tracker: Book records
    Tracker-->>Browser: Rendered dashboard HTML

    Browser->>Tracker: POST /book/42/log (status=read, rating=8, review="...")
    Tracker->>CWA_DB: Validate session
    CWA_DB-->>Tracker: OK
    Tracker->>TRK_DB: INSERT/UPDATE reading_log
    TRK_DB-->>Tracker: OK
    Tracker-->>Browser: 200 / redirect
```

## Environment Variables

| Variable | Required? | Example | Description |
|---|---|---|---|
| `TRACKER_SECRET_KEY` | **yes** | `openssl rand -hex 32` | The tracker's own Flask session signing key. **Different from `CWA_SECRET_KEY`** — the tracker signs its own Flask-Login session cookie. |
| `CWA_SECRET_KEY` | **yes** | matches CWN's `SECRET_KEY` | CWN's Flask `SECRET_KEY`. Used **only** to verify CWN's signed session cookie. Set the same value in CWN's container env (it doesn't read from `app.db`'s `flask_settings` once `SECRET_KEY` is in env). |
| `CALIBRE_DB_PATH` | **yes** | `/calibre-library/metadata.db` | Path to Calibre's `metadata.db` (read-only). |
| `CALIBRE_LIBRARY_PATH` | recommended | `/calibre-library` | Root of the Calibre library — used to resolve cover paths and (in Phase 6) stream them via `/cover/<id>`. Defaults to the parent of `CALIBRE_DB_PATH`. |
| `CWA_DB_PATH` | **yes** | `/cwa/app.db` | Path to CWN's `app.db` (read-only). |
| `TRACKER_DB_PATH` | **yes** | `/config/tracker.db` | The tracker's own writable SQLite database. |
| `CWA_COOKIE_PREFIX` | optional | `""` (empty) | Mirrors CWN's `COOKIE_PREFIX` env var. Cookie name = `f"{prefix}session"`. Confirm with `docker exec calibre-web printenv COOKIE_PREFIX`. |
| `CWA_BASE_URL` | optional | `http://localhost:8083` | Where the tracker's "Library" nav link and `/auth/logout` redirect to. |
| `AUTH_MODE` | optional | `cookie` (default) or `form` | `cookie` = Scenario A (ride CWN's signed cookie). `form` = Scenario B fallback (bcrypt check against CWN's `user` table). |
| `FLASK_CONFIG` | optional | `dev` (default) / `prod` / `test` | Config profile selector. |
| `FLASK_APP` | dev only | `app:create_app` | Required when running `flask run` or `flask db …`. |
| `TZ` | optional | `America/New_York` | Container timezone. |
| `LOG_LEVEL` | optional | `INFO` | Python logging level. |
| `MAX_CONTENT_LENGTH` | optional | `16777216` | Max upload size (16 MiB, for future cover uploads). |

> **Note:** `TRACKER_SECRET_KEY` and `CWA_SECRET_KEY` should be **different** values. They serve different purposes: `CWA_SECRET_KEY` verifies CWN's cookie; `TRACKER_SECRET_KEY` signs the tracker's own Flask-Login session.

> **`SECRET_KEY` on the CWN side.** CWN reads `SECRET_KEY` from env first, then falls back to a row in `app.db`'s `flask_settings.flask_session_key` (auto-generated on first boot). The bridge assumes env-set — make sure to add `- SECRET_KEY=<your_value>` to CWN's compose and restart it once. Existing CWN sessions are invalidated by the change (a single re-login fixes it).

## Calibre Web NextGen — Relevant Environment Variables

These variables belong to the **NextGen container** (not your tracker), but are worth knowing because they affect how the auth bridge and reverse proxy behave.

| Variable | Default | Relevance to Tracker |
|---|---|---|
| `COOKIE_PREFIX` | `""` | If set, the session cookie name becomes `{prefix}session` rather than `session`. Your `cwa_bridge.py` must read this same prefix. |
| `TRUSTED_PROXY_COUNT` | `1` | Must be set correctly for session protection to work behind your reverse proxy. Misconfiguration causes forced re-logins. |
| `NETWORK_SHARE_MODE` | `false` | Set to `true` if your Calibre library is on an NFS/SMB share (common on Unraid). Disables SQLite WAL mode and switches inotify to polling. Has no direct effect on the tracker, but affects `metadata.db` locking behaviour. |

> **`COOKIE_PREFIX` is the most tracker-relevant setting.** Check its value in your NextGen container — if it's set to anything other than empty string, update `cwa_bridge.py`'s cookie lookup from `request.cookies.get("session")` to `request.cookies.get(f"{prefix}session")`.
