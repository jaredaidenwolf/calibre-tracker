"""Shared pytest fixtures for the tracker test suite."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from flask import Flask

from app import create_app
from app.calibre.models import reset_engine_cache

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
CALIBRE_LIBRARY = FIXTURE_DIR / "calibre-library"
CALIBRE_DB = CALIBRE_LIBRARY / "metadata.db"


@pytest.fixture(scope="session", autouse=True)
def _ensure_fixture_db() -> None:
    """Build the fixture metadata.db on demand if it isn't present."""
    if not CALIBRE_DB.exists():
        from tests.fixtures.build_metadata_fixture import build

        build()


@pytest.fixture()
def app(tmp_path: Path) -> Flask:
    """A Flask app configured against the fixture Calibre library."""
    os.environ["TRACKER_DB_PATH"] = str(tmp_path / "tracker.db")
    app = create_app("test")
    app.config.update(
        CALIBRE_DB_PATH=str(CALIBRE_DB),
        CALIBRE_LIBRARY_PATH=str(CALIBRE_LIBRARY),
    )
    reset_engine_cache()
    yield app
    reset_engine_cache()


@pytest.fixture()
def app_context(app: Flask):
    """Push an app context so code that calls ``current_app`` works."""
    with app.app_context():
        yield app
