"""Phase 6 — dashboard, book detail, search, and cover-streaming tests.

Acceptance criteria from ``docs/04-implementation-plan.md``:

* Dashboard shows the logged-in user's books grouped by status with covers.
* On first login, books marked read in CWN appear under "Read" with the
  import flash shown once and never again.
* Book detail lets the user change status, set a rating, add a review —
  and it persists.
* Covers load via /cover/<id>.
* Search finds a book and lets the user log it.
* Everything is per-user.
"""

from __future__ import annotations

import pytest

from app.auth.cwa_bridge import encode_cwa_session
from app.extensions import db
from app.tracker.models import ReadingLog, User
from app.tracker.service import upsert_reading_log
from tests.conftest import CALIBRE_LIBRARY, CWA_TEST_SECRET_KEY

# ── Fixtures ────────────────────────────────────────────────────────────────


def _login(client, *, cwa_user_id: int, session_key: str, random_token: str) -> None:
    cookie = encode_cwa_session(
        {"_user_id": str(cwa_user_id), "_id": session_key, "_random": random_token},
        secret_key=CWA_TEST_SECRET_KEY,
    )
    client.set_cookie("session", cookie, domain="localhost")
    # Trigger before_request once so the user lands in the tracker DB.
    client.get("/health")


@pytest.fixture()
def alice_client(client, app_context):
    _login(client, cwa_user_id=1, session_key="alice-sk-active", random_token="alice-rand")
    return client


# ── Cover route ─────────────────────────────────────────────────────────────


def test_cover_streams_for_known_book(alice_client):
    resp = alice_client.get("/cover/1")
    assert resp.status_code == 200
    assert resp.mimetype == "image/jpeg"
    # PNG_PIXEL fixture is 70 bytes; the byte count should match the file on disk.
    fixture = CALIBRE_LIBRARY / "Becky Chambers/The Long Way to a Small, Angry Planet (1)/cover.jpg"
    assert resp.data == fixture.read_bytes()


def test_cover_404_when_book_unknown(alice_client):
    assert alice_client.get("/cover/99999").status_code == 404


def test_cover_404_when_no_cover_on_disk(alice_client):
    # Book 5 has has_cover=0 in the fixture.
    assert alice_client.get("/cover/5").status_code == 404


def test_cover_requires_login(client):
    resp = client.get("/cover/1", follow_redirects=False)
    assert resp.status_code in (302, 401)


# ── Dashboard ───────────────────────────────────────────────────────────────


def test_dashboard_first_load_runs_cwn_import_and_flashes(alice_client, app_context):
    resp = alice_client.get("/", follow_redirects=False)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "3 books" in body  # fixture Alice has 3 read=1 book_read_link rows
    assert "Calibre Web" in body
    # Verify the rows actually landed in the tracker DB.
    alice = User.query.filter_by(cwa_user_id=1).one()
    assert ReadingLog.query.filter_by(user_id=alice.id).count() == 3
    assert alice.cwn_import_completed is True


def test_dashboard_second_load_does_not_re_flash_import(alice_client):
    alice_client.get("/")  # first load — flash fires
    resp = alice_client.get("/")  # second load — no flash
    body = resp.get_data(as_text=True)
    assert "3 books" not in body
    assert "Calibre Web" not in body or "Calibre Web Automated" not in body


def test_dashboard_groups_by_status(alice_client, app_context):
    alice = (
        User.query.filter_by(cwa_user_id=1).one()
        if User.query.filter_by(cwa_user_id=1).first()
        else None
    )
    if alice is None:
        # before_request created her on the /health call in alice_client
        alice = User.query.filter_by(cwa_user_id=1).one()

    upsert_reading_log(alice, 4, {"status": "reading"})  # The Fifth Season
    upsert_reading_log(alice, 6, {"status": "want_to_read"})  # Project Hail Mary

    resp = alice_client.get("/")
    body = resp.get_data(as_text=True)
    assert "Currently Reading" in body
    assert "Want to Read" in body
    assert "Finished" in body
    # Each book should appear with its title in its section.
    assert "The Fifth Season" in body
    assert "Project Hail Mary" in body


def test_dashboard_uses_cover_route(alice_client, app_context):
    alice = User.query.filter_by(cwa_user_id=1).one()
    upsert_reading_log(alice, 1, {"status": "reading"})
    resp = alice_client.get("/")
    assert "/cover/1" in resp.get_data(as_text=True)


def test_dashboard_renders_tombstone_for_missing_book(alice_client, app_context):
    alice = (
        User.query.filter_by(cwa_user_id=1).one()
        if User.query.filter_by(cwa_user_id=1).first()
        else None
    )
    if alice is None:
        alice = User.query.filter_by(cwa_user_id=1).one()
    # 99999 doesn't exist in the fixture Calibre DB.
    db.session.add(ReadingLog(user_id=alice.id, calibre_book_id=99999, status="reading"))
    db.session.commit()
    resp = alice_client.get("/")
    body = resp.get_data(as_text=True)
    assert "no longer in library" in body


def test_dashboard_requires_login(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 401)


# ── Book detail ─────────────────────────────────────────────────────────────


def test_book_detail_renders(alice_client):
    resp = alice_client.get("/book/3")  # Piranesi
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Piranesi" in body
    assert "Susanna Clarke" in body
    # The detail page is now read-only — the form has moved to /book/<id>/edit.
    # The trash form is the only form on the page (only present when logged).
    assert "/book/3/edit" in body  # pencil-icon link to the edit page


def test_book_detail_404_for_unknown(alice_client):
    assert alice_client.get("/book/99999").status_code == 404


def test_book_edit_renders_form(alice_client):
    resp = alice_client.get("/book/3/edit")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'name="csrf_token"' in body
    assert 'name="status"' in body
    assert 'name="rating"' in body
    assert 'name="review"' in body


def test_book_edit_404_for_unknown(alice_client):
    assert alice_client.get("/book/99999/edit").status_code == 404


def test_book_edit_form_creates_new_attempt(alice_client, app_context):
    """POST /book/<id>/edit always inserts a fresh read-attempt row."""
    alice = User.query.filter_by(cwa_user_id=1).one()
    resp = alice_client.post(
        "/book/3/edit",
        data={
            "status": "read",
            "rating": "9",
            "review": "Quiet, strange, perfect.",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    logs = ReadingLog.query.filter_by(user_id=alice.id, calibre_book_id=3).all()
    assert len(logs) == 1
    assert logs[0].status == "read"
    assert logs[0].rating == 9
    assert logs[0].review == "Quiet, strange, perfect."

    # A second POST creates a SECOND row — no upsert. Every read attempt
    # is its own row; the "second time through" just picks Currently
    # Reading again (re_reading was retired in favour of one shared
    # in-flight status).
    alice_client.post(
        "/book/3/edit",
        data={"status": "reading"},
        follow_redirects=False,
    )
    logs = ReadingLog.query.filter_by(user_id=alice.id, calibre_book_id=3).all()
    assert len(logs) == 2
    statuses = sorted(log.status for log in logs)
    assert statuses == ["read", "reading"]


def test_book_edit_entry_updates_in_place(alice_client, app_context):
    """POST /book/<id>/edit/<entry_id> amends that row without creating
    a new one."""
    alice = User.query.filter_by(cwa_user_id=1).one()
    alice_client.post("/book/3/edit", data={"status": "read", "rating": "7"})
    log = ReadingLog.query.filter_by(user_id=alice.id, calibre_book_id=3).one()

    resp = alice_client.post(
        f"/book/3/edit/{log.id}",
        data={"status": "read", "rating": "9", "review": "Better on reflection."},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    logs = ReadingLog.query.filter_by(user_id=alice.id, calibre_book_id=3).all()
    assert len(logs) == 1  # still just one row
    assert logs[0].rating == 9
    assert logs[0].review == "Better on reflection."


def test_book_edit_entry_404_for_other_user_row(alice_client, app_context):
    """A user can't edit another user's read-attempt row."""
    # Seed a row owned by some other user_id (bob's row).
    bob_log = ReadingLog(user_id=999, calibre_book_id=3, status="read")
    from app.extensions import db
    db.session.add(bob_log)
    db.session.commit()

    resp = alice_client.post(
        f"/book/3/edit/{bob_log.id}",
        data={"status": "read", "rating": "10"},
        follow_redirects=True,
    )
    # Service raises ReadingLogValidationError -> flash + redirect to /edit
    # (not 404 because the URL is valid; authz happens in the service layer).
    body = resp.get_data(as_text=True)
    assert "not found" in body.lower()


def test_book_edit_form_rejects_bad_rating_via_flash(alice_client, app_context):
    """A bad rating flashes an error and does NOT persist."""
    resp = alice_client.post(
        "/book/3/edit",
        data={"status": "read", "rating": "99"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "rating" in body.lower()  # flash visible
    alice = User.query.filter_by(cwa_user_id=1).one()
    log = ReadingLog.query.filter_by(user_id=alice.id, calibre_book_id=3).first()
    assert log is None or log.rating != 99


def test_book_detail_delete_action_drops_all_rows(alice_client, app_context):
    """The trash icon removes the book from the tracker entirely."""
    alice = User.query.filter_by(cwa_user_id=1).one()
    upsert_reading_log(alice, 3, {"status": "read", "rating": 8})
    upsert_reading_log(alice, 3, {"status": "reading", "is_reread": True})
    assert ReadingLog.query.filter_by(user_id=alice.id, calibre_book_id=3).count() == 2

    resp = alice_client.post(
        "/book/3",
        data={"action": "delete"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert ReadingLog.query.filter_by(user_id=alice.id, calibre_book_id=3).count() == 0
    assert ReadingLog.current_status_for(alice.id, 3) is None


# ── Search ──────────────────────────────────────────────────────────────────


def test_search_finds_book(alice_client):
    resp = alice_client.get("/search?q=Piranesi")
    body = resp.get_data(as_text=True)
    assert "Piranesi" in body
    # The card should link to the book detail page.
    assert "/book/3" in body


def test_search_empty_query_renders_prompt(alice_client):
    resp = alice_client.get("/search")
    body = resp.get_data(as_text=True)
    assert "Type a title" in body


def test_search_no_match(alice_client):
    resp = alice_client.get("/search?q=zzzzzzz")
    body = resp.get_data(as_text=True)
    assert "No books match" in body


# ── Cross-user isolation ────────────────────────────────────────────────────


def test_dashboard_is_per_user(app):
    """Logging in as Alice shows Alice's books. Bob never sees them.

    Alice has 3 read=1 book_read_link rows in the fixture; Bob has zero.
    Uses separate test clients so cookie state from Alice never reaches Bob.

    Intentionally does NOT take ``app_context`` — Flask's ``g`` is bound to
    the app context, and Flask-Login caches the loaded user on
    ``g._login_user`` per-app-context. Holding one outer context across
    both requests would let Alice's cached user bleed into Bob's response.
    Each ``client.get`` already pushes its own short-lived app/request
    context, so DB writes that the request handlers do are visible to
    everyone via the shared SQLite file.
    """
    # Seed a non-expired user_session row for Bob (the fixture row is expired).
    import sqlite3

    with sqlite3.connect(app.config["CWA_DB_PATH"]) as conn:
        conn.execute(
            "INSERT INTO user_session (user_id, session_key, random, expiry) VALUES (?, ?, ?, ?)",
            (2, "bob-sk-live", "bob-rand-live", 0),
        )
        conn.commit()

    alice_client = app.test_client()
    _login(alice_client, cwa_user_id=1, session_key="alice-sk-active", random_token="alice-rand")
    alice_dash = alice_client.get("/").get_data(as_text=True)
    assert "Piranesi" in alice_dash

    bob_client = app.test_client()
    _login(bob_client, cwa_user_id=2, session_key="bob-sk-live", random_token="bob-rand-live")
    bob_dash = bob_client.get("/").get_data(as_text=True)
    assert "Piranesi" not in bob_dash
    # Bob has no books — every status section should render the empty state.
    assert bob_dash.count("Nothing here yet.") >= 3


def test_book_edit_review_only_persists_and_displays(alice_client, app_context):
    """Review-only save: persists, repopulates the textarea on /edit, and
    surfaces under 'My review' on the detail page. Mirrors the user flow."""
    alice = User.query.filter_by(cwa_user_id=1).one()
    resp = alice_client.post(
        "/book/3/edit",
        data={"review": "Sample content from the user."},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)

    log = ReadingLog.query.filter_by(
        user_id=alice.id, calibre_book_id=3
    ).one()
    assert log.review == "Sample content from the user."

    body_edit = alice_client.get("/book/3/edit").get_data(as_text=True)
    assert "Sample content from the user." in body_edit

    body_detail = alice_client.get("/book/3").get_data(as_text=True)
    assert "Sample content from the user." in body_detail
