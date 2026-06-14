"""Phase 2 — auth bridge tests.

Covers the cookie decode/validate pipeline, the ``user_session`` cross
check, COOKIE_PREFIX handling, the bcrypt fallback, and the read-only
guarantee against ``app.db``.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from app.auth import cwa_bridge
from app.auth.cwa_bridge import (
    authenticate_cwa_credentials,
    check_cwa_user_session,
    cwa_db_connection,
    decode_cwa_session,
    encode_cwa_session,
    get_cwa_user_by_id,
    validate_cwa_session,
)
from tests.conftest import CWA_TEST_SECRET_KEY


def _make_cookie(payload: dict, secret: str = CWA_TEST_SECRET_KEY) -> str:
    return encode_cwa_session(payload, secret_key=secret)


@pytest.fixture()
def alice_cookie_payload() -> dict:
    return {
        "_user_id": "1",
        "_id": "alice-sk-active",
        "_random": "alice-rand",
    }


# ── decode_cwa_session ──────────────────────────────────────────────────────


def test_decode_valid_cookie_returns_payload(app_context, alice_cookie_payload):
    cookie = _make_cookie(alice_cookie_payload)
    assert decode_cwa_session(cookie) == alice_cookie_payload


def test_decode_tampered_cookie_returns_none(app_context, alice_cookie_payload):
    cookie = _make_cookie(alice_cookie_payload)
    tampered = cookie[:-2] + ("AA" if cookie[-2:] != "AA" else "BB")
    assert decode_cwa_session(tampered) is None


def test_decode_with_wrong_secret_returns_none(app_context, alice_cookie_payload):
    cookie = _make_cookie(alice_cookie_payload, secret="other-secret")
    assert decode_cwa_session(cookie) is None


def test_decode_with_empty_configured_secret_returns_none(app_context, alice_cookie_payload):
    app_context.config["CWA_SECRET_KEY"] = ""
    cookie = _make_cookie(alice_cookie_payload, secret="anything")
    assert decode_cwa_session(cookie) is None


# ── check_cwa_user_session ──────────────────────────────────────────────────


def test_check_session_accepts_active_row(app_context):
    assert check_cwa_user_session(1, "alice-sk-active", "alice-rand") is True


def test_check_session_rejects_unknown_user(app_context):
    assert check_cwa_user_session(999, "alice-sk-active", "alice-rand") is False


def test_check_session_rejects_unknown_session_key(app_context):
    assert check_cwa_user_session(1, "nope", "alice-rand") is False


def test_check_session_rejects_mismatched_random(app_context):
    assert check_cwa_user_session(1, "alice-sk-active", "wrong-rand") is False


def test_check_session_rejects_expired_row(app_context):
    # Bob's session row has expiry=1 (epoch), so any "now" > 1 should reject.
    assert check_cwa_user_session(2, "bob-sk-expired", "bob-rand", now=int(time.time())) is False


def test_check_session_treats_zero_expiry_as_no_expiry(app_context):
    # Alice's row has expiry=0; even far-future "now" must still pass.
    assert check_cwa_user_session(1, "alice-sk-active", "alice-rand", now=9999999999) is True


def test_check_session_rejects_empty_session_key(app_context):
    assert check_cwa_user_session(1, "", "alice-rand") is False
    assert check_cwa_user_session(1, None, "alice-rand") is False


# ── get_cwa_user_by_id ──────────────────────────────────────────────────────


def test_get_user_by_id(app_context):
    user = get_cwa_user_by_id(1)
    assert user is not None
    assert user["name"] == "alice"
    assert user["email"] == "alice@example.com"


def test_get_user_missing_returns_none(app_context):
    assert get_cwa_user_by_id(99999) is None


# ── validate_cwa_session (full pipeline) ────────────────────────────────────


def test_validate_full_pipeline_accepts_valid_cookie(app_context, alice_cookie_payload):
    cookie = _make_cookie(alice_cookie_payload)
    user = validate_cwa_session(cookie)
    assert user is not None and user["name"] == "alice"


def test_validate_rejects_cookie_without_user_id(app_context):
    cookie = _make_cookie({"_id": "alice-sk-active", "_random": "alice-rand"})
    assert validate_cwa_session(cookie) is None


def test_validate_rejects_cookie_for_deleted_session_row(app_context, cwa_db_path):
    # Simulate a remote logout: delete Alice's user_session row.
    with sqlite3.connect(cwa_db_path) as conn:
        conn.execute("DELETE FROM user_session WHERE user_id = 1")
        conn.commit()
    cookie = _make_cookie({"_user_id": "1", "_id": "alice-sk-active", "_random": "alice-rand"})
    assert validate_cwa_session(cookie) is None


def test_validate_rejects_expired_session(app_context):
    cookie = _make_cookie({"_user_id": "2", "_id": "bob-sk-expired", "_random": "bob-rand"})
    assert validate_cwa_session(cookie) is None


# ── authenticate_cwa_credentials (form fallback) ────────────────────────────


def test_form_auth_accepts_correct_password(app_context):
    user = authenticate_cwa_credentials("alice", "alice-pw")
    assert user is not None and user["id"] == 1


def test_form_auth_rejects_wrong_password(app_context):
    assert authenticate_cwa_credentials("alice", "wrong") is None


def test_form_auth_rejects_unknown_user(app_context):
    assert authenticate_cwa_credentials("nobody", "any") is None


def test_form_auth_rejects_empty(app_context):
    assert authenticate_cwa_credentials("", "") is None


# ── Read-only guarantee against app.db ──────────────────────────────────────


def test_cwa_connection_rejects_writes(app_context):
    with cwa_db_connection() as conn, pytest.raises(sqlite3.OperationalError) as exc:
        conn.execute("UPDATE user SET name = 'mutated' WHERE id = 1")
    assert "readonly" in str(exc.value).lower() or "read-only" in str(exc.value).lower()


# ── End-to-end through the Flask request lifecycle ──────────────────────────


def test_valid_cookie_logs_user_in_via_before_request(app_context, client, alice_cookie_payload):
    """A request carrying a valid CWN cookie ends with a logged-in tracker user."""
    cookie = _make_cookie(alice_cookie_payload)
    client.set_cookie("session", cookie, domain="localhost")
    # Trigger the before_request hook against /health (which doesn't itself care)
    resp = client.get("/health")
    assert resp.status_code == 200

    from app.tracker.models import User

    # After the request, the user should exist in tracker DB
    with app_context.test_request_context():
        client.get("/health")
        user = User.query.filter_by(cwa_user_id=1).first()
        assert user is not None
        assert user.username == "alice"


def test_invalid_cookie_does_not_create_user(app_context, client):
    client.set_cookie("session", "totally-not-a-real-cookie", domain="localhost")
    client.get("/health")

    from app.tracker.models import User

    with app_context.app_context():
        assert User.query.count() == 0


def test_no_cookie_does_not_create_user(app_context, client):
    client.get("/health")

    from app.tracker.models import User

    with app_context.app_context():
        assert User.query.count() == 0


def test_logout_clears_session(app_context, client, alice_cookie_payload):
    cookie = _make_cookie(alice_cookie_payload)
    client.set_cookie("session", cookie, domain="localhost")
    client.get("/health")
    resp = client.get("/auth/logout", follow_redirects=False)
    assert resp.status_code in (301, 302)


# ── COOKIE_PREFIX handling ──────────────────────────────────────────────────


def test_cookie_prefix_respected(app_context, client, alice_cookie_payload):
    """When CWA_COOKIE_PREFIX is non-empty, the prefixed cookie name is used."""
    app_context.config["CWA_COOKIE_PREFIX"] = "cwn_"
    cookie = _make_cookie(alice_cookie_payload)

    # The plain "session" cookie should be ignored
    client.set_cookie("session", cookie, domain="localhost")
    client.get("/health")
    from app.tracker.models import User

    with app_context.app_context():
        assert User.query.count() == 0

    # ...but "cwn_session" should be read
    client.set_cookie("cwn_session", cookie, domain="localhost")
    client.get("/health")
    with app_context.app_context():
        assert User.query.filter_by(cwa_user_id=1).first() is not None


# ── AUTH_MODE=form disables the cookie hook ─────────────────────────────────


def test_auth_mode_form_skips_cookie_hook(app_context, client, alice_cookie_payload):
    app_context.config["AUTH_MODE"] = "form"
    cookie = _make_cookie(alice_cookie_payload)
    client.set_cookie("session", cookie, domain="localhost")
    client.get("/health")
    from app.tracker.models import User

    with app_context.app_context():
        assert User.query.count() == 0


# ── import_cwn_read_status ──────────────────────────────────────────────────


def test_import_cwn_read_status_seeds_reading_log(app_context):
    """Importing Alice's read books creates a ReadingLog row per read=1 book."""
    from app.extensions import db
    from app.tracker.models import ReadingLog, User

    user = User(cwa_user_id=1, username="alice", display_name="alice")
    db.session.add(user)
    db.session.commit()

    count = cwa_bridge.import_cwn_read_status(user, db.session)
    db.session.commit()

    assert count == 3  # Alice's three read=1 rows: books 1, 2, 3
    logs = ReadingLog.query.filter_by(user_id=user.id).all()
    assert {log.calibre_book_id for log in logs} == {1, 2, 3}
    assert all(log.status == "read" for log in logs)


def test_import_cwn_read_status_is_idempotent(app_context):
    """Running the import twice never duplicates rows."""
    from app.extensions import db
    from app.tracker.models import ReadingLog, User

    user = User(cwa_user_id=1, username="alice", display_name="alice")
    db.session.add(user)
    db.session.commit()

    cwa_bridge.import_cwn_read_status(user, db.session)
    db.session.commit()
    second_run = cwa_bridge.import_cwn_read_status(user, db.session)
    db.session.commit()

    assert second_run == 0
    assert ReadingLog.query.filter_by(user_id=user.id).count() == 3


def test_import_cwn_read_status_is_additive(app_context):
    """Books already in reading_log are not overwritten."""
    from app.extensions import db
    from app.tracker.models import ReadingLog, User

    user = User(cwa_user_id=1, username="alice", display_name="alice")
    db.session.add(user)
    db.session.commit()

    pre_existing = ReadingLog(user_id=user.id, calibre_book_id=1, status="reading")
    db.session.add(pre_existing)
    db.session.commit()

    cwa_bridge.import_cwn_read_status(user, db.session)
    db.session.commit()

    # Book 1's pre-existing row should still be status="reading"
    rows = ReadingLog.query.filter_by(user_id=user.id, calibre_book_id=1).all()
    assert len(rows) == 1
    assert rows[0].status == "reading"
    # Books 2 and 3 should still have been imported
    assert ReadingLog.query.filter_by(user_id=user.id).count() == 3
