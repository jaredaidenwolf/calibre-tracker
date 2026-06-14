"""Phase 4 — reading-log core tests.

Acceptance criteria from ``docs/04-implementation-plan.md``:

* Logging as ``read`` with a rating persists and reads back.
* Status transitions auto-fill dates as specified.
* Invalid rating / status / date ordering is rejected with HTTP 400.
* A reread creates a second row, preserving the first.
* All reading-log operations are scoped to the authenticated user.
* ``maybe_run_cwn_import`` seeds 3 ``reading_log`` rows for Alice
  (status='read'; dates and rating intentionally null).
* Re-running the import (with ``cwn_import_completed=True``) creates 0 new rows.
* The import is additive — books already in ``reading_log`` are not overwritten.
* ``user.cwn_import_completed`` is True after a successful import.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.auth.cwa_bridge import encode_cwa_session
from app.extensions import db
from app.tracker.models import ReadingLog, User
from app.tracker.service import (
    ReadingLogValidationError,
    delete_reading_log,
    maybe_run_cwn_import,
    quick_status_change,
    upsert_reading_log,
)
from tests.conftest import CWA_TEST_SECRET_KEY

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def alice(app_context) -> User:
    user = User(cwa_user_id=1, username="alice", display_name="alice")
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture()
def bob(app_context) -> User:
    user = User(cwa_user_id=2, username="bob", display_name="bob")
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture()
def alice_client(client, app_context):
    """A test client logged in as Alice via her CWN cookie."""
    cookie = encode_cwa_session(
        {"_user_id": "1", "_id": "alice-sk-active", "_random": "alice-rand"},
        secret_key=CWA_TEST_SECRET_KEY,
    )
    client.set_cookie("session", cookie, domain="localhost")
    # First request triggers the before_request hook to create + log in the user.
    client.get("/health")
    return client


# ── Service: validation ─────────────────────────────────────────────────────


def test_rating_out_of_range_rejected(alice):
    with pytest.raises(ReadingLogValidationError):
        upsert_reading_log(alice, 1, {"status": "read", "rating": 11})
    with pytest.raises(ReadingLogValidationError):
        upsert_reading_log(alice, 1, {"status": "read", "rating": 0})
    with pytest.raises(ReadingLogValidationError):
        upsert_reading_log(alice, 1, {"status": "read", "rating": "not-a-number"})


def test_unknown_status_rejected(alice):
    with pytest.raises(ReadingLogValidationError):
        upsert_reading_log(alice, 1, {"status": "completed-and-loved"})


def test_finished_before_started_rejected(alice):
    with pytest.raises(ReadingLogValidationError):
        upsert_reading_log(
            alice,
            1,
            {
                "status": "read",
                "started_at": "2026-06-01",
                "finished_at": "2026-05-01",
            },
        )


def test_garbage_date_rejected(alice):
    with pytest.raises(ReadingLogValidationError):
        upsert_reading_log(alice, 1, {"status": "reading", "started_at": "not-a-date"})


# ── Service: persistence + auto-transitions ─────────────────────────────────


def test_logging_read_with_rating_persists(alice):
    log = upsert_reading_log(
        alice,
        1,
        {"status": "read", "rating": 8, "review": "Excellent."},
    )
    fresh = db.session.get(ReadingLog, log.id)
    assert fresh.status == "read"
    assert fresh.rating == 8
    assert fresh.review == "Excellent."
    assert fresh.finished_at is not None  # auto-filled


def test_status_reading_autofills_started_at(alice):
    log = upsert_reading_log(alice, 1, {"status": "reading"})
    assert log.started_at is not None
    assert log.finished_at is None


def test_status_read_autofills_finished_at(alice):
    log = upsert_reading_log(alice, 1, {"status": "read"})
    assert log.finished_at is not None


def test_provided_dates_are_preserved(alice):
    started = datetime(2026, 6, 1)
    finished = datetime(2026, 6, 5)
    log = upsert_reading_log(
        alice,
        1,
        {
            "status": "read",
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
        },
    )
    assert log.started_at == started
    assert log.finished_at == finished


def test_status_quick_change(alice):
    upsert_reading_log(alice, 1, {"status": "want_to_read"})
    log = quick_status_change(alice, 1, "reading")
    assert log.status == "reading"
    assert log.started_at is not None


def test_quick_change_rejects_bad_status(alice):
    with pytest.raises(ReadingLogValidationError):
        quick_status_change(alice, 1, "bogus")


# ── Rereads ─────────────────────────────────────────────────────────────────


def test_reread_inserts_new_row(alice):
    first = upsert_reading_log(alice, 1, {"status": "read", "rating": 9})
    second = upsert_reading_log(
        alice,
        1,
        {"status": "re_reading", "is_reread": True, "rating": 10},
    )
    assert first.id != second.id
    assert second.is_reread is True
    assert second.reread_count == 1
    # Original first-read row must be intact.
    canonical = (
        db.session.query(ReadingLog)
        .filter_by(user_id=alice.id, calibre_book_id=1, is_reread=False)
        .one()
    )
    assert canonical.id == first.id
    assert canonical.rating == 9


def test_reread_count_increments(alice):
    upsert_reading_log(alice, 1, {"status": "read"})
    r1 = upsert_reading_log(alice, 1, {"status": "re_reading", "is_reread": True})
    r2 = upsert_reading_log(alice, 1, {"status": "re_reading", "is_reread": True})
    assert r1.reread_count == 1
    assert r2.reread_count == 2


# ── Delete ──────────────────────────────────────────────────────────────────


def test_delete_canonical_row(alice):
    upsert_reading_log(alice, 1, {"status": "read"})
    assert delete_reading_log(alice, 1) is True
    assert ReadingLog.current_status_for(alice.id, 1) is None
    assert delete_reading_log(alice, 1) is False


def test_delete_preserves_rereads(alice):
    upsert_reading_log(alice, 1, {"status": "read"})
    upsert_reading_log(alice, 1, {"status": "re_reading", "is_reread": True})
    delete_reading_log(alice, 1)
    rereads = ReadingLog.query.filter_by(user_id=alice.id, is_reread=True).all()
    assert len(rereads) == 1


# ── Cross-user isolation ────────────────────────────────────────────────────


def test_users_cannot_see_each_others_logs(alice, bob):
    upsert_reading_log(alice, 1, {"status": "read", "rating": 9})
    upsert_reading_log(bob, 1, {"status": "want_to_read"})
    assert ReadingLog.current_status_for(alice.id, 1) == "read"
    assert ReadingLog.current_status_for(bob.id, 1) == "want_to_read"
    # Service mutations don't bleed across users either:
    quick_status_change(alice, 1, "dnf")
    assert ReadingLog.current_status_for(bob.id, 1) == "want_to_read"


# ── Routes ──────────────────────────────────────────────────────────────────


def test_post_log_via_route(alice_client):
    resp = alice_client.post("/book/1/log", json={"status": "read", "rating": 8})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "read"
    assert body["rating"] == 8


def test_post_log_rejects_invalid_payload(alice_client):
    resp = alice_client.post("/book/1/log", json={"status": "read", "rating": 99})
    assert resp.status_code == 400
    assert "rating" in resp.get_json()["error"].lower()


def test_get_log_returns_current_and_rereads(alice_client):
    alice_client.post("/book/1/log", json={"status": "read", "rating": 9})
    alice_client.post("/book/1/log", json={"status": "re_reading", "is_reread": True})
    resp = alice_client.get("/book/1/log")
    body = resp.get_json()
    assert body["status"] == "read"
    assert len(body["rereads"]) == 1


def test_post_status_via_route(alice_client):
    resp = alice_client.post("/book/1/status", json={"status": "reading"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "reading"
    assert body["started_at"] is not None


def test_post_status_rejects_unknown(alice_client):
    resp = alice_client.post("/book/1/status", json={"status": "bogus"})
    assert resp.status_code == 400


def test_delete_log_via_route(alice_client):
    alice_client.post("/book/1/log", json={"status": "read"})
    resp = alice_client.delete("/book/1/log")
    assert resp.status_code == 200
    resp2 = alice_client.delete("/book/1/log")
    assert resp2.status_code == 404


def test_routes_require_login(client):
    """No CWN cookie = no auth = redirect to login page."""
    resp = client.post("/book/1/log", json={"status": "read"})
    assert resp.status_code in (302, 401)


# ── Future-proof: status transitions don't undo manual dates ────────────────


def test_status_change_does_not_clobber_user_dates(alice):
    started = datetime(2026, 6, 1)
    upsert_reading_log(
        alice,
        1,
        {"status": "reading", "started_at": started.isoformat()},
    )
    # Now flip to 'read'. The user's started_at must survive.
    log = upsert_reading_log(alice, 1, {"status": "read"})
    assert log.started_at == started
    assert log.finished_at is not None
    assert log.finished_at >= started


def test_future_finished_after_started_accepted(alice):
    started = datetime(2026, 6, 1)
    finished = started + timedelta(days=14)
    log = upsert_reading_log(
        alice,
        1,
        {
            "status": "read",
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
        },
    )
    assert log.started_at == started
    assert log.finished_at == finished


# ── CWN import ──────────────────────────────────────────────────────────────


def test_maybe_run_cwn_import_seeds_three_rows(alice):
    """Fixture Alice has 3 read=1 book_read_link rows → 3 reading_log rows."""
    assert alice.cwn_import_completed is False
    count = maybe_run_cwn_import(alice)
    assert count == 3
    rows = ReadingLog.query.filter_by(user_id=alice.id).all()
    assert {r.calibre_book_id for r in rows} == {1, 2, 3}
    # Acceptance: status='read' with no dates or rating.
    for r in rows:
        assert r.status == "read"
        assert r.started_at is None
        assert r.finished_at is None
        assert r.rating is None
    assert alice.cwn_import_completed is True


def test_maybe_run_cwn_import_is_one_shot(alice):
    first = maybe_run_cwn_import(alice)
    second = maybe_run_cwn_import(alice)
    assert first == 3
    assert second is None  # already completed → no-op


def test_maybe_run_cwn_import_is_additive(alice):
    """Books already in reading_log are not overwritten."""
    existing = ReadingLog(
        user_id=alice.id,
        calibre_book_id=1,
        status="reading",
        rating=7,
        review="In progress.",
    )
    db.session.add(existing)
    db.session.commit()

    maybe_run_cwn_import(alice)

    rows = ReadingLog.query.filter_by(user_id=alice.id, calibre_book_id=1).all()
    assert len(rows) == 1
    assert rows[0].status == "reading"
    assert rows[0].rating == 7
    # Other books still imported.
    assert ReadingLog.query.filter_by(user_id=alice.id).count() == 3
