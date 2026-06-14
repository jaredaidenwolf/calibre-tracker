"""Shared pytest fixtures for the tracker test suite."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from flask import Flask

from app import create_app
from app.calibre.models import reset_engine_cache
from app.extensions import db

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
CALIBRE_LIBRARY = FIXTURE_DIR / "calibre-library"
CALIBRE_DB = CALIBRE_LIBRARY / "metadata.db"
CWA_DIR = FIXTURE_DIR / "cwa"
CWA_DB = CWA_DIR / "app.db"

CWA_TEST_SECRET_KEY = "test-cwa-secret-key"


@pytest.fixture(scope="session", autouse=True)
def _ensure_fixture_dbs() -> None:
    """Build the fixture databases on demand if they aren't present."""
    if not CALIBRE_DB.exists():
        from tests.fixtures.build_metadata_fixture import build as build_calibre

        build_calibre()
    if not CWA_DB.exists():
        from tests.fixtures.build_cwa_fixture import build as build_cwa

        build_cwa()


@pytest.fixture()
def cwa_db_path(tmp_path: Path) -> Path:
    """A per-test copy of the fixture CWA app.db so tests can mutate it."""
    target = tmp_path / "app.db"
    shutil.copy(CWA_DB, target)
    return target


@pytest.fixture()
def app(tmp_path: Path, cwa_db_path: Path) -> Flask:
    """A Flask app configured against the fixture databases."""
    os.environ["TRACKER_DB_PATH"] = str(tmp_path / "tracker.db")
    app = create_app("test")
    app.config.update(
        CALIBRE_DB_PATH=str(CALIBRE_DB),
        CALIBRE_LIBRARY_PATH=str(CALIBRE_LIBRARY),
        CWA_DB_PATH=str(cwa_db_path),
        CWA_SECRET_KEY=CWA_TEST_SECRET_KEY,
        CWA_COOKIE_PREFIX="",
        AUTH_MODE="cookie",
    )
    reset_engine_cache()
    with app.app_context():
        db.create_all()
    yield app
    reset_engine_cache()


@pytest.fixture()
def app_context(app: Flask):
    """Push an app + test request context.

    A request context is included so template helpers like ``url_for``
    work inside :func:`render_template` calls. The context is short-lived
    (scoped to the fixture) and isn't shared with any ``test_client``
    request you make inside the test — each ``client.get(...)`` still
    pushes its own request context on top.
    """
    with app.test_request_context():
        yield app


@pytest.fixture()
def client(app: Flask):
    """A Flask test client."""
    return app.test_client()


@pytest.fixture(autouse=True)
def _push_request_context():
    """Override pytest-flask's autouse ``_push_request_context``.

    pytest-flask pushes a single ``app.test_request_context()`` for the
    whole test whenever ``app`` is in scope. That outer context is
    shared by every ``client.open(...)`` call, which leaks Flask-Login's
    cached ``g._login_user`` across separate test clients (e.g. switching
    from Alice to Bob mid-test would otherwise see Bob render with
    Alice's identity). Tests that need a request or app context push
    their own explicitly via ``app_context`` or ``with app.app_context()``.
    """
    yield
