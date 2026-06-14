"""Auth routes and the CWN session-cookie before-request hook.

The hook validates a CWN-signed session cookie on every request, looks
up (or creates) the matching tracker :class:`User`, and logs them into
the tracker's Flask-Login session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flask import Blueprint, current_app, redirect, request, url_for
from flask_login import current_user, login_user, logout_user

from ..extensions import db, login_manager
from ..tracker.models import User
from .cwa_bridge import authenticate_cwa_credentials, validate_cwa_session

if TYPE_CHECKING:
    from flask import Flask
    from werkzeug.wrappers import Response

auth_bp = Blueprint("auth", __name__)


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    """Flask-Login user_loader — resolves a tracker user by primary key."""
    try:
        pk = int(user_id)
    except (TypeError, ValueError):
        return None
    return db.session.get(User, pk)


def _get_or_create_tracker_user(cwa_user: dict) -> User:
    """Find the tracker user for a CWN account, creating one on first sight.

    The lookup key is ``cwa_user_id`` — the tracker never mutates CWN's
    user table, it only mirrors the identity.
    """
    tracker_user = User.query.filter_by(cwa_user_id=cwa_user["id"]).first()
    if tracker_user is None:
        tracker_user = User(
            cwa_user_id=cwa_user["id"],
            username=cwa_user["name"],
            display_name=cwa_user.get("name"),
        )
        db.session.add(tracker_user)
        db.session.commit()
    return tracker_user


def load_user_from_cwa_cookie() -> None:
    """``before_request`` hook: ride CWN's signed session cookie.

    Skips entirely if the request is already authenticated, or if
    ``AUTH_MODE`` is ``"form"`` (Scenario B fallback). Respects CWN's
    ``COOKIE_PREFIX`` env var via the ``CWA_COOKIE_PREFIX`` config key.
    """
    if current_app.config.get("AUTH_MODE", "cookie") != "cookie":
        return
    if current_user.is_authenticated:
        return

    prefix = current_app.config.get("CWA_COOKIE_PREFIX", "") or ""
    cookie_name = f"{prefix}session"
    cwa_cookie = request.cookies.get(cookie_name)
    if not cwa_cookie:
        return

    cwa_user = validate_cwa_session(cwa_cookie)
    if not cwa_user:
        return

    tracker_user = _get_or_create_tracker_user(cwa_user)
    login_user(tracker_user, remember=True)


@auth_bp.post("/login")
def login() -> Response:
    """Scenario B form-auth: only active when ``AUTH_MODE == "form"``.

    The form submits ``username`` and ``password`` against CWN's bcrypt
    hashes. Used when same-domain cookie sharing isn't possible.
    """
    if current_app.config.get("AUTH_MODE", "cookie") != "form":
        return redirect(url_for("health"))  # form auth disabled

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    cwa_user = authenticate_cwa_credentials(username, password)
    if not cwa_user:
        return redirect(url_for("auth.login_page", error="1"))

    tracker_user = _get_or_create_tracker_user(cwa_user)
    login_user(tracker_user, remember=True)
    next_url = request.args.get("next") or "/"
    return redirect(next_url)


@auth_bp.get("/login")
def login_page() -> Response:
    """Placeholder login page — real template ships with Phase 5."""
    return (
        "<form method='post'><input name='username'><input name='password' type='password'><button>Login</button></form>",
        200,
    )


@auth_bp.route("/logout")
def logout() -> Response:
    """Clear the tracker session and bounce to CWN's logout endpoint."""
    logout_user()
    cwa_base = current_app.config.get("CWA_BASE_URL", "/") or "/"
    target = cwa_base.rstrip("/") + "/logout" if cwa_base != "/" else "/"
    return redirect(target)


def register_auth(app: Flask) -> None:
    """Register the auth blueprint and before-request hook on ``app``."""
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.before_request(load_user_from_cwa_cookie)
    login_manager.login_view = "auth.login_page"
