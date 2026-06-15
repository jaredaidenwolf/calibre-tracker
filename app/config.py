"""Application configuration loaded from environment variables.

Three config classes are provided: a base ``Config`` with shared defaults,
``DevConfig`` for local development, and ``ProdConfig`` for production.
"""

from __future__ import annotations

import os
from pathlib import Path


def _abs_path(value: str) -> str:
    """Resolve ``value`` to an absolute path, anchored at the process CWD.

    Flask-SQLAlchemy resolves relative ``sqlite://`` URIs against
    ``app.instance_path``, which is *not* the CWD — so a relative
    ``TRACKER_DB_PATH`` like ``./instance/tracker.db`` ends up at
    ``<instance_path>/instance/tracker.db`` (doubled) and silently
    fails to open. Normalizing here means the env var can be relative
    or absolute and the engine URI is always absolute.
    """
    return str(Path(value).resolve())


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable (truthy: 1, true, yes, on)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    """Base configuration shared across environments.

    All paths and secrets are sourced from environment variables so the
    container image carries no hardcoded paths or credentials.
    """

    SECRET_KEY: str = os.environ.get("TRACKER_SECRET_KEY", "dev-insecure-change-me")

    CALIBRE_DB_PATH: str = os.environ.get("CALIBRE_DB_PATH", "/calibre-library/metadata.db")
    CWA_DB_PATH: str = os.environ.get("CWA_DB_PATH", "/cwa/app.db")
    TRACKER_DB_PATH: str = _abs_path(os.environ.get("TRACKER_DB_PATH", "/config/tracker.db"))

    CALIBRE_LIBRARY_PATH: str = os.environ.get("CALIBRE_LIBRARY_PATH", "/calibre-library")

    CWA_SECRET_KEY: str = os.environ.get("CWA_SECRET_KEY", "")
    CWA_COOKIE_PREFIX: str = os.environ.get("CWA_COOKIE_PREFIX", "")
    CWA_BASE_URL: str = os.environ.get("CWA_BASE_URL", "/")

    AUTH_MODE: str = os.environ.get("AUTH_MODE", "cookie")

    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
    MAX_CONTENT_LENGTH: int = int(os.environ.get("MAX_CONTENT_LENGTH", 16 * 1024 * 1024))

    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False

    # ── Cookie names ────────────────────────────────────────────────────────
    # The tracker and Calibre-Web NextGen run on the same host (same domain
    # in production, same localhost in dev). Both default to Flask's cookie
    # name "session" and Flask-Login's "remember_token". When two apps on
    # the same host use the same cookie names, every response from one app
    # overwrites the other app's cookies — which means the tracker's own
    # session cookie clobbers CWN's signed session cookie on every request,
    # breaking the auto-auth flow.
    #
    # Naming them explicitly here keeps the two apps' cookie jars disjoint.
    SESSION_COOKIE_NAME: str = "tracker_session"
    REMEMBER_COOKIE_NAME: str = "tracker_remember"

    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:  # noqa: N802 — Flask convention
        return f"sqlite:///{self.TRACKER_DB_PATH}"

    TESTING: bool = False
    DEBUG: bool = False


class DevConfig(Config):
    """Development configuration."""

    DEBUG: bool = True


class ProdConfig(Config):
    """Production configuration."""

    DEBUG: bool = False


class TestConfig(Config):
    """Test configuration — in-memory tracker DB by default."""

    TESTING: bool = True
    DEBUG: bool = False
    SECRET_KEY: str = "test-secret-key"
    CWA_SECRET_KEY: str = "test-cwa-secret-key"
    WTF_CSRF_ENABLED: bool = False

    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:  # noqa: N802 — Flask convention
        path = os.environ.get("TRACKER_DB_PATH")
        return f"sqlite:///{_abs_path(path)}" if path else "sqlite:///:memory:"


_CONFIG_MAP: dict[str, type[Config]] = {
    "dev": DevConfig,
    "development": DevConfig,
    "prod": ProdConfig,
    "production": ProdConfig,
    "test": TestConfig,
    "testing": TestConfig,
}


def get_config(name: str | None = None) -> Config:
    """Resolve a config name to an instance.

    Falls back to the ``FLASK_CONFIG`` env var, then ``DevConfig``.
    """
    chosen = name or os.environ.get("FLASK_CONFIG", "dev")
    cls = _CONFIG_MAP.get(chosen.lower(), DevConfig)
    return cls()
