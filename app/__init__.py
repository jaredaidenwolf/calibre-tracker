"""Flask application factory.

The factory pattern lets us build separate app instances for production,
development, and tests without depending on import-time global state.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import Flask, jsonify

from .config import Config, get_config
from .extensions import csrf, db, login_manager, migrate


def create_app(config: str | Config | None = None) -> Flask:
    """Construct and return a configured Flask application.

    Args:
        config: Either a config name (``"dev"``, ``"prod"``, ``"test"``),
            a :class:`Config` instance, or ``None`` to resolve from the
            ``FLASK_CONFIG`` environment variable.
    """
    app = Flask(__name__)

    cfg = config if isinstance(config, Config) else get_config(config)
    _apply_config(app, cfg)

    logging.basicConfig(level=app.config.get("LOG_LEVEL", "INFO"))

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    _register_routes(app)
    _register_auth(app)
    _register_tracker(app)

    return app


def _register_auth(app: Flask) -> None:
    """Wire the CWN auth bridge — kept in its own function for testability."""
    from .auth.routes import register_auth

    register_auth(app)


def _register_tracker(app: Flask) -> None:
    """Wire the tracker blueprint (reading-log routes)."""
    from .tracker.routes import register_tracker

    register_tracker(app)


def _apply_config(app: Flask, cfg: Config) -> None:
    """Copy attributes off the config object onto ``app.config``."""
    for key in dir(cfg):
        if key.isupper():
            value: Any = getattr(cfg, key)
            app.config[key] = value


def _register_routes(app: Flask) -> None:
    """Register top-level routes that don't belong to a blueprint."""

    @app.get("/health")
    def health() -> Any:
        """Simple liveness check used by Docker healthcheck."""
        return jsonify({"status": "ok"}), 200
