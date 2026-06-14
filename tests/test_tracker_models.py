"""Phase 3 — tracker models, migration, and integration tests.

Covers the model/migration acceptance criteria from
``docs/04-implementation-plan.md``:

* ``flask db upgrade`` creates every table in a fresh ``tracker.db``.
* All seven indexes from ``docs/01-data-model.md`` exist.
* ``User.cwn_import_completed`` defaults to ``False``.
* Creating a ``User`` + ``ReadingLog`` round-trips.
* Phase 2 + Phase 3 integration: ``user_loader`` resolves the user end-to-end.
* ``flask db downgrade base`` runs clean.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.auth.cwa_bridge import encode_cwa_session
from app.extensions import db
from app.tracker.models import (
    NOTE_TYPES,
    READING_STATUSES,
    Note,
    Quote,
    ReadingGoal,
    ReadingLog,
    ReadingSession,
    Shelf,
    ShelfBook,
    User,
)
from tests.conftest import CALIBRE_DB, CALIBRE_LIBRARY, CWA_DB, CWA_TEST_SECRET_KEY

REPO_ROOT = Path(__file__).resolve().parent.parent

# Index names required by docs/01-data-model.md.
REQUIRED_INDEXES: frozenset[str] = frozenset(
    {
        "idx_reading_log_user",
        "idx_reading_log_book",
        "idx_reading_log_status",
        "idx_quotes_user_book",
        "idx_notes_user_book",
        "idx_sessions_log",
        "idx_shelf_books",
    }
)


# ── Constant-vocabulary sanity ──────────────────────────────────────────────


def test_reading_statuses_match_doc():
    expected = frozenset({"want_to_read", "reading", "read", "dnf", "re_reading"})
    assert expected == READING_STATUSES


def test_note_types_match_doc():
    expected = frozenset({"general", "character", "plot", "theme", "reaction"})
    assert expected == NOTE_TYPES


# ── Model round-trips ───────────────────────────────────────────────────────


def test_user_defaults(app_context):
    user = User(cwa_user_id=42, username="alice")
    db.session.add(user)
    db.session.commit()

    fresh = db.session.get(User, user.id)
    assert fresh.cwn_import_completed is False
    assert fresh.timezone == "UTC"
    assert fresh.created_at is not None


def test_user_cwa_user_id_is_unique(app_context):
    db.session.add(User(cwa_user_id=1, username="alice"))
    db.session.commit()
    db.session.add(User(cwa_user_id=1, username="alice2"))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_reading_log_roundtrip(app_context):
    user = User(cwa_user_id=1, username="alice")
    db.session.add(user)
    db.session.commit()

    log = ReadingLog(
        user_id=user.id,
        calibre_book_id=1,
        status="reading",
        rating=8,
        review="So far so good.",
    )
    db.session.add(log)
    db.session.commit()

    fresh = db.session.query(ReadingLog).filter_by(user_id=user.id, calibre_book_id=1).one()
    assert fresh.status == "reading"
    assert fresh.rating == 8
    assert fresh.review == "So far so good."
    assert fresh.is_reread is False
    assert fresh.reread_count == 0


def test_current_status_for(app_context):
    user = User(cwa_user_id=1, username="alice")
    db.session.add(user)
    db.session.commit()

    # No log yet → None
    assert ReadingLog.current_status_for(user.id, 1) is None

    # First read
    db.session.add(ReadingLog(user_id=user.id, calibre_book_id=1, status="read"))
    db.session.commit()
    assert ReadingLog.current_status_for(user.id, 1) == "read"

    # A reread row must NOT clobber the canonical status.
    db.session.add(
        ReadingLog(
            user_id=user.id,
            calibre_book_id=1,
            status="re_reading",
            is_reread=True,
            reread_count=1,
        )
    )
    db.session.commit()
    assert ReadingLog.current_status_for(user.id, 1) == "read"


def test_other_models_persist(app_context):
    user = User(cwa_user_id=1, username="alice")
    db.session.add(user)
    db.session.commit()

    log = ReadingLog(user_id=user.id, calibre_book_id=1, status="reading")
    db.session.add(log)
    db.session.commit()

    db.session.add(
        ReadingSession(reading_log_id=log.id, pages_start=0, pages_end=50, duration_minutes=30)
    )
    db.session.add(Quote(user_id=user.id, calibre_book_id=1, quote_text="Hi"))
    db.session.add(Note(user_id=user.id, calibre_book_id=1, note_text="Note", note_type="reaction"))
    db.session.add(Shelf(user_id=user.id, name="Beach Reads", color_hex="#c9a96e"))
    db.session.add(ReadingGoal(user_id=user.id, year=2026, target_books=24))
    db.session.commit()

    assert ReadingSession.query.count() == 1
    assert Quote.query.count() == 1
    assert Note.query.count() == 1
    assert Shelf.query.count() == 1
    assert ReadingGoal.query.count() == 1


def test_shelf_book_unique_per_shelf(app_context):
    user = User(cwa_user_id=1, username="alice")
    db.session.add(user)
    db.session.commit()
    shelf = Shelf(user_id=user.id, name="Beach Reads")
    db.session.add(shelf)
    db.session.commit()

    db.session.add(ShelfBook(shelf_id=shelf.id, calibre_book_id=1))
    db.session.commit()
    db.session.add(ShelfBook(shelf_id=shelf.id, calibre_book_id=1))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_reading_goal_unique_per_year(app_context):
    user = User(cwa_user_id=1, username="alice")
    db.session.add(user)
    db.session.commit()

    db.session.add(ReadingGoal(user_id=user.id, year=2026, target_books=10))
    db.session.commit()
    db.session.add(ReadingGoal(user_id=user.id, year=2026, target_books=20))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


# ── Phase 2 + Phase 3 integration: user_loader end-to-end ───────────────────


def test_user_loader_resolves_logged_in_user(client, app_context):
    """A valid CWN cookie creates the User AND subsequent requests resolve
    that user via the Flask-Login user_loader."""
    cookie_payload = {
        "_user_id": "1",
        "_id": "alice-sk-active",
        "_random": "alice-rand",
    }
    cookie = encode_cwa_session(cookie_payload, secret_key=CWA_TEST_SECRET_KEY)
    client.set_cookie("session", cookie, domain="localhost")

    # Hit /health twice — first request creates the user; second proves
    # the user_loader can resolve them by primary key.
    client.get("/health")
    user = User.query.filter_by(cwa_user_id=1).one()
    assert user.username == "alice"

    # Manually invoke the user_loader the way Flask-Login would
    from app.auth.routes import load_user

    resolved = load_user(str(user.id))
    assert resolved is not None
    assert resolved.id == user.id
    assert resolved.cwa_user_id == 1


def test_user_loader_returns_none_for_garbage(app_context):
    from app.auth.routes import load_user

    assert load_user("not-an-int") is None
    assert load_user("99999") is None


# ── Migration end-to-end (acceptance criterion: indexes present) ────────────


@pytest.fixture()
def fresh_db_env(tmp_path: Path) -> dict[str, str]:
    """An environment with a brand-new tracker.db ready for `flask db ...`."""
    db_path = tmp_path / "tracker.db"
    env = os.environ.copy()
    env.update(
        {
            "FLASK_APP": "app:create_app",
            "FLASK_CONFIG": "dev",
            "TRACKER_DB_PATH": str(db_path),
            "CALIBRE_DB_PATH": str(CALIBRE_DB),
            "CALIBRE_LIBRARY_PATH": str(CALIBRE_LIBRARY),
            "CWA_DB_PATH": str(CWA_DB),
            "CWA_SECRET_KEY": "dummy",
            "TRACKER_SECRET_KEY": "dummy",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    env["_TRACKER_DB_PATH"] = str(db_path)
    return env


def _flask(cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "flask", *cmd],
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_migration_upgrade_creates_all_tables_and_indexes(fresh_db_env):
    result = _flask(["db", "upgrade"], fresh_db_env)
    assert result.returncode == 0, result.stderr
    db_path = fresh_db_env["_TRACKER_DB_PATH"]

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        expected_tables = {
            "users",
            "reading_log",
            "reading_sessions",
            "quotes",
            "notes",
            "shelves",
            "shelf_books",
            "reading_goals",
            "alembic_version",
        }
        assert expected_tables.issubset(tables), f"missing tables: {expected_tables - tables}"

        index_names = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        missing = REQUIRED_INDEXES - index_names
        assert not missing, f"missing required indexes: {missing}"
    finally:
        conn.close()


def test_migration_downgrade_runs_clean(fresh_db_env):
    upgrade = _flask(["db", "upgrade"], fresh_db_env)
    assert upgrade.returncode == 0, upgrade.stderr

    downgrade = _flask(["db", "downgrade", "base"], fresh_db_env)
    assert downgrade.returncode == 0, downgrade.stderr

    conn = sqlite3.connect(fresh_db_env["_TRACKER_DB_PATH"])
    try:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        # After downgrade, only alembic_version should remain.
        assert tables == {"alembic_version"}
    finally:
        conn.close()
