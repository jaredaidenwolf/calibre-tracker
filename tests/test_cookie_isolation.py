"""Regression tests for cookie-name isolation from Calibre-Web NextGen.

Both apps default to Flask's cookie name ``session`` and Flask-Login's
``remember_token``. Since they share a host (same domain in production,
same localhost in dev), the tracker setting either of those names on a
response would clobber CWN's cookie in the browser's cookie jar, breaking
the auto-auth flow on the next request.

These tests pin the tracker's cookie names so the collision cannot
silently come back.
"""

from __future__ import annotations

from app.auth.cwa_bridge import encode_cwa_session
from tests.conftest import CWA_TEST_SECRET_KEY


def _cookie_names(response) -> set[str]:
    """Pull cookie names out of every Set-Cookie header on a response."""
    names: set[str] = set()
    for header in response.headers.getlist("Set-Cookie"):
        name = header.split("=", 1)[0].strip()
        names.add(name)
    return names


# ── Config-level invariants ─────────────────────────────────────────────────


def test_session_cookie_is_renamed(app_context):
    assert app_context.config["SESSION_COOKIE_NAME"] == "tracker_session"


def test_remember_cookie_is_renamed(app_context):
    assert app_context.config["REMEMBER_COOKIE_NAME"] == "tracker_remember"


# ── Response-level invariants ───────────────────────────────────────────────


def test_health_response_sets_no_conflicting_cookies(client):
    """A request to /health must not Set-Cookie under CWN's names."""
    resp = client.get("/health")
    names = _cookie_names(resp)
    assert "session" not in names, f"tracker is still issuing CWN's 'session' cookie: {names}"
    assert "remember_token" not in names, (
        f"tracker is still issuing CWN's 'remember_token': {names}"
    )


def test_redirect_to_login_does_not_clobber_cwn_cookie(client):
    """Hitting an authed route with no cookie redirects to login, but the
    flash message must land in the tracker's OWN cookie — not 'session'."""
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 303)
    names = _cookie_names(resp)
    assert "session" not in names
    assert "tracker_session" in names  # the flash goes here


def test_successful_login_sets_only_tracker_cookies(client):
    """A valid CWN cookie that triggers login_user must only Set-Cookie
    under tracker names, never CWN names."""
    cookie = encode_cwa_session(
        {"_user_id": "1", "_id": "alice-sk-active", "_random": "alice-rand"},
        secret_key=CWA_TEST_SECRET_KEY,
    )
    client.set_cookie("session", cookie, domain="localhost")
    resp = client.get("/health")
    names = _cookie_names(resp)
    assert "session" not in names
    assert "remember_token" not in names
    # The tracker should have set its own session + remember cookies.
    assert "tracker_session" in names
    assert "tracker_remember" in names


# ── End-to-end: a CWN cookie survives a tracker roundtrip ───────────────────


def test_cwn_cookie_survives_tracker_response(client):
    """Set a CWN-style 'session' cookie, hit the tracker, and confirm the
    request a *second* request still carries that same CWN cookie. The
    earlier bug was that the tracker's Set-Cookie would overwrite it in
    the browser's cookie jar.
    """
    cwn_cookie = encode_cwa_session(
        {"_user_id": "1", "_id": "alice-sk-active", "_random": "alice-rand"},
        secret_key=CWA_TEST_SECRET_KEY,
    )
    client.set_cookie("session", cwn_cookie, domain="localhost")

    # First roundtrip — should authenticate Alice and write tracker cookies.
    client.get("/health")

    # The CWN cookie must still be present under name 'session'.
    jar = {key[2]: cookie.value for key, cookie in client._cookies.items()}
    assert "session" in jar, f"CWN's 'session' cookie was clobbered. Jar: {list(jar)}"
    assert jar["session"] == cwn_cookie, "CWN's 'session' cookie value was modified"
    # And the tracker should have laid down its own session.
    assert "tracker_session" in jar
