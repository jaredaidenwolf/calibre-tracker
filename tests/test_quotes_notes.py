"""Phase 7 — Quotes & Notes CRUD + global /quotes view.

Acceptance criteria from ``docs/04-implementation-plan.md``:

* Adding, editing, deleting quotes and notes works and is per-user.
* Spoiler notes are flagged and visually hidden until revealed.
* The global quotes view aggregates correctly and filters work.
* Page/chapter refs accept free-text ("loc. 1443", "Ch. 4").
"""

from __future__ import annotations

import pytest

from app.extensions import db
from app.tracker.models import Note, Quote, User
from app.tracker.service import (
    AnnotationValidationError,
    create_note,
    create_quote,
    delete_note,
    delete_quote,
    toggle_quote_favourite,
    update_note,
    update_quote,
)
from tests.test_dashboard import _login


@pytest.fixture()
def alice_client(client, app_context):
    _login(client, cwa_user_id=1, session_key="alice-sk-active", random_token="alice-rand")
    return client


@pytest.fixture()
def alice(app_context) -> User:
    """Seed Alice directly (the route-level _login fixture also does this
    via the before_request hook, but service-only tests skip the request
    layer)."""
    existing = User.query.filter_by(cwa_user_id=1).first()
    if existing is not None:
        return existing
    user = User(cwa_user_id=1, username="alice", display_name="alice")
    db.session.add(user)
    db.session.commit()
    return user


# ── Service layer ───────────────────────────────────────────────────────────


def test_create_quote_persists_required_and_optional_fields(alice):
    q = create_quote(
        alice,
        3,
        {
            "quote_text": "Quiet, strange, perfect.",
            "page_reference": "p. 142",
            "chapter_reference": "Ch. 4",
            "context_note": "Right after the flood.",
            "is_favourite": True,
        },
    )
    fresh = db.session.get(Quote, q.id)
    assert fresh.quote_text == "Quiet, strange, perfect."
    assert fresh.page_reference == "p. 142"
    assert fresh.chapter_reference == "Ch. 4"
    assert fresh.context_note == "Right after the flood."
    assert fresh.is_favourite is True
    assert fresh.calibre_book_id == 3
    assert fresh.user_id == alice.id


def test_create_quote_requires_text(alice):
    with pytest.raises(AnnotationValidationError):
        create_quote(alice, 3, {"quote_text": "   "})


def test_create_quote_rejects_too_long_page_ref(alice):
    with pytest.raises(AnnotationValidationError):
        create_quote(alice, 3, {"quote_text": "x", "page_reference": "p" * 65})


def test_update_quote_replaces_fields(alice):
    q = create_quote(alice, 3, {"quote_text": "first", "page_reference": "p. 1"})
    update_quote(
        alice,
        q.id,
        {"quote_text": "second", "page_reference": "p. 2", "is_favourite": True},
    )
    fresh = db.session.get(Quote, q.id)
    assert fresh.quote_text == "second"
    assert fresh.page_reference == "p. 2"
    assert fresh.is_favourite is True


def test_update_quote_without_is_favourite_key_preserves_flag(alice):
    """Editing the body must not silently un-favourite a starred quote."""
    q = create_quote(alice, 3, {"quote_text": "x", "is_favourite": True})
    update_quote(alice, q.id, {"quote_text": "y"})  # no is_favourite key
    fresh = db.session.get(Quote, q.id)
    assert fresh.is_favourite is True


def test_update_quote_rejects_other_user(alice):
    other = User(cwa_user_id=999, username="other")
    db.session.add(other)
    db.session.commit()
    q = create_quote(other, 3, {"quote_text": "not yours"})
    with pytest.raises(AnnotationValidationError):
        update_quote(alice, q.id, {"quote_text": "mine now"})


def test_toggle_quote_favourite(alice):
    q = create_quote(alice, 3, {"quote_text": "x"})
    assert q.is_favourite is False
    toggle_quote_favourite(alice, q.id)
    assert db.session.get(Quote, q.id).is_favourite is True
    toggle_quote_favourite(alice, q.id)
    assert db.session.get(Quote, q.id).is_favourite is False


def test_delete_quote(alice):
    q = create_quote(alice, 3, {"quote_text": "x"})
    assert delete_quote(alice, q.id) is True
    assert db.session.get(Quote, q.id) is None
    assert delete_quote(alice, q.id) is False  # idempotent


def test_delete_quote_rejects_other_user(alice):
    other = User(cwa_user_id=999, username="other")
    db.session.add(other)
    db.session.commit()
    q = create_quote(other, 3, {"quote_text": "x"})
    assert delete_quote(alice, q.id) is False
    assert db.session.get(Quote, q.id) is not None


def test_create_note_with_type_and_spoiler(alice):
    n = create_note(
        alice,
        3,
        {
            "note_text": "The reveal in chapter 12 was incredible.",
            "note_type": "plot",
            "page_reference": "Ch. 12",
            "is_spoiler": True,
        },
    )
    fresh = db.session.get(Note, n.id)
    assert fresh.note_text == "The reveal in chapter 12 was incredible."
    assert fresh.note_type == "plot"
    assert fresh.is_spoiler is True


def test_create_note_defaults_type_to_general(alice):
    n = create_note(alice, 3, {"note_text": "x"})
    assert db.session.get(Note, n.id).note_type == "general"


def test_create_note_rejects_invalid_type(alice):
    with pytest.raises(AnnotationValidationError):
        create_note(alice, 3, {"note_text": "x", "note_type": "garbage"})


def test_update_note_preserves_spoiler_flag_without_key(alice):
    n = create_note(alice, 3, {"note_text": "x", "is_spoiler": True})
    update_note(alice, n.id, {"note_text": "y", "note_type": "general"})
    assert db.session.get(Note, n.id).is_spoiler is True


def test_delete_note(alice):
    n = create_note(alice, 3, {"note_text": "x"})
    assert delete_note(alice, n.id) is True
    assert db.session.get(Note, n.id) is None


# ── Routes (book detail integration) ────────────────────────────────────────


def test_book_detail_renders_empty_states(alice_client):
    body = alice_client.get("/book/3").get_data(as_text=True)
    assert "No quotes yet." in body
    assert "No notes yet." in body


def test_quote_new_get_renders_form(alice_client):
    body = alice_client.get("/book/3/quote/new").get_data(as_text=True)
    assert 'name="quote_text"' in body
    assert "Add a quote" in body


def test_quote_create_post_persists_and_redirects(alice_client, alice):
    resp = alice_client.post(
        "/book/3/quote/new",
        data={
            "quote_text": "Quiet, strange, perfect.",
            "page_reference": "p. 142",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "/book/3" in resp.headers["Location"]
    assert "#quotes" in resp.headers["Location"]

    quotes = Quote.query.filter_by(user_id=alice.id, calibre_book_id=3).all()
    assert len(quotes) == 1
    assert quotes[0].quote_text == "Quiet, strange, perfect."


def test_quote_create_post_rejects_empty(alice_client, alice):
    resp = alice_client.post(
        "/book/3/quote/new", data={"quote_text": "   "}, follow_redirects=True
    )
    body = resp.get_data(as_text=True)
    assert "required" in body.lower()
    assert Quote.query.filter_by(user_id=alice.id).count() == 0


def test_quote_edit_post_updates(alice_client, alice):
    q = create_quote(alice, 3, {"quote_text": "first"})
    alice_client.post(
        f"/quote/{q.id}/edit",
        data={"quote_text": "second", "is_favourite_present": "1", "is_favourite": "1"},
        follow_redirects=False,
    )
    fresh = db.session.get(Quote, q.id)
    assert fresh.quote_text == "second"
    assert fresh.is_favourite is True


def test_quote_favourite_toggles(alice_client, alice):
    q = create_quote(alice, 3, {"quote_text": "x"})
    alice_client.post(f"/quote/{q.id}/favourite")
    assert db.session.get(Quote, q.id).is_favourite is True
    alice_client.post(f"/quote/{q.id}/favourite")
    assert db.session.get(Quote, q.id).is_favourite is False


def test_quote_delete_removes_row(alice_client, alice):
    q = create_quote(alice, 3, {"quote_text": "x"})
    resp = alice_client.post(f"/quote/{q.id}/delete", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert db.session.get(Quote, q.id) is None


def test_quote_edit_404_for_other_user(alice_client, alice):
    other = User(cwa_user_id=999, username="other")
    db.session.add(other)
    db.session.commit()
    q = create_quote(other, 3, {"quote_text": "x"})
    assert alice_client.get(f"/quote/{q.id}/edit").status_code == 404
    assert alice_client.post(f"/quote/{q.id}/delete").status_code == 404


def test_note_create_and_edit(alice_client, alice):
    alice_client.post(
        "/book/3/note/new",
        data={
            "note_text": "Big reveal here.",
            "note_type": "plot",
            "is_spoiler_present": "1",
            "is_spoiler": "1",
        },
    )
    n = Note.query.filter_by(user_id=alice.id, calibre_book_id=3).one()
    assert n.note_type == "plot"
    assert n.is_spoiler is True

    alice_client.post(
        f"/note/{n.id}/edit",
        data={
            "note_text": "Big reveal here, actually.",
            "note_type": "plot",
            "is_spoiler_present": "1",
            # box unchecked this time
        },
    )
    fresh = db.session.get(Note, n.id)
    assert fresh.note_text == "Big reveal here, actually."
    assert fresh.is_spoiler is False


def test_note_delete_removes_row(alice_client, alice):
    n = create_note(alice, 3, {"note_text": "x"})
    alice_client.post(f"/note/{n.id}/delete")
    assert db.session.get(Note, n.id) is None


# ── Global /quotes view ─────────────────────────────────────────────────────


def test_quotes_index_empty(alice_client):
    body = alice_client.get("/quotes").get_data(as_text=True)
    assert "No quotes yet" in body


def test_quotes_index_lists_quotes(alice_client, alice):
    create_quote(alice, 3, {"quote_text": "First quote"})
    create_quote(alice, 4, {"quote_text": "Second quote"})
    body = alice_client.get("/quotes").get_data(as_text=True)
    assert "First quote" in body
    assert "Second quote" in body
    assert "Piranesi" in body         # book 3 — book header strip
    assert "The Fifth Season" in body  # book 4


def test_quotes_index_book_filter(alice_client, alice):
    create_quote(alice, 3, {"quote_text": "Piranesi quote"})
    create_quote(alice, 4, {"quote_text": "Fifth Season quote"})
    body = alice_client.get("/quotes?book=3").get_data(as_text=True)
    assert "Piranesi quote" in body
    assert "Fifth Season quote" not in body


def test_quotes_index_favourites_filter(alice_client, alice):
    create_quote(alice, 3, {"quote_text": "Plain", "is_favourite": False})
    create_quote(alice, 3, {"quote_text": "Loved this", "is_favourite": True})
    body = alice_client.get("/quotes?favourites=1").get_data(as_text=True)
    assert "Loved this" in body
    assert "Plain" not in body


def test_quotes_index_is_per_user(alice_client, alice):
    other = User(cwa_user_id=999, username="other")
    db.session.add(other)
    db.session.commit()
    create_quote(alice, 3, {"quote_text": "Mine"})
    create_quote(other, 3, {"quote_text": "Not mine"})
    body = alice_client.get("/quotes").get_data(as_text=True)
    assert "Mine" in body
    assert "Not mine" not in body


def test_sidebar_links_to_quotes_index(alice_client):
    """The sidebar 'Quotes' link now resolves to /quotes (no placeholder)."""
    body = alice_client.get("/").get_data(as_text=True)
    assert "/quotes" in body
