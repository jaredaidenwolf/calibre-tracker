"""Phase 8 — Shelves CRUD + manual ordering + per-user isolation.

Acceptance criteria from ``docs/04-implementation-plan.md``:

* Creating a shelf, adding books, reordering, and removing books all work.
* Shelves are per-user and isolated.
* A book can belong to multiple shelves.
"""

from __future__ import annotations

import pytest

from app.extensions import db
from app.tracker.models import Shelf, ShelfBook, User
from app.tracker.service import (
    ShelfValidationError,
    add_book_to_shelf,
    create_shelf,
    delete_shelf,
    move_book_in_shelf,
    remove_book_from_shelf,
    update_shelf,
)
from tests.test_dashboard import _login


@pytest.fixture()
def alice_client(client, app_context):
    _login(client, cwa_user_id=1, session_key="alice-sk-active", random_token="alice-rand")
    return client


@pytest.fixture()
def alice(app_context) -> User:
    existing = User.query.filter_by(cwa_user_id=1).first()
    if existing is not None:
        return existing
    user = User(cwa_user_id=1, username="alice", display_name="alice")
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture()
def other_user(app_context) -> User:
    user = User(cwa_user_id=999, username="other")
    db.session.add(user)
    db.session.commit()
    return user


# ── Service: shelves ────────────────────────────────────────────────────────


def test_create_shelf_persists_fields(alice):
    shelf = create_shelf(
        alice,
        {"name": "Hugo Winners", "description": "Speculative best-of", "color_hex": "#c9a96e"},
    )
    fresh = db.session.get(Shelf, shelf.id)
    assert fresh.name == "Hugo Winners"
    assert fresh.description == "Speculative best-of"
    assert fresh.color_hex == "#c9a96e"
    assert fresh.user_id == alice.id


def test_create_shelf_requires_name(alice):
    with pytest.raises(ShelfValidationError):
        create_shelf(alice, {"name": "   "})


def test_create_shelf_rejects_duplicate_name_same_user(alice):
    create_shelf(alice, {"name": "Faves"})
    with pytest.raises(ShelfValidationError):
        create_shelf(alice, {"name": "Faves"})


def test_create_shelf_allows_same_name_for_different_user(alice, other_user):
    create_shelf(alice, {"name": "Faves"})
    create_shelf(other_user, {"name": "Faves"})
    assert Shelf.query.filter_by(name="Faves").count() == 2


def test_create_shelf_rejects_bad_hex(alice):
    with pytest.raises(ShelfValidationError):
        create_shelf(alice, {"name": "x", "color_hex": "not-a-color"})


def test_create_shelf_accepts_uppercase_hex_and_lowercases(alice):
    shelf = create_shelf(alice, {"name": "x", "color_hex": "#C9A96E"})
    assert shelf.color_hex == "#c9a96e"


def test_update_shelf_amends_fields(alice):
    shelf = create_shelf(alice, {"name": "old", "description": "old desc"})
    update_shelf(alice, shelf.id, {"name": "new", "description": "new desc"})
    fresh = db.session.get(Shelf, shelf.id)
    assert fresh.name == "new"
    assert fresh.description == "new desc"


def test_update_shelf_rejects_rename_to_existing(alice):
    create_shelf(alice, {"name": "A"})
    shelf_b = create_shelf(alice, {"name": "B"})
    with pytest.raises(ShelfValidationError):
        update_shelf(alice, shelf_b.id, {"name": "A"})


def test_update_shelf_allows_keeping_same_name(alice):
    shelf = create_shelf(alice, {"name": "Keep"})
    update_shelf(alice, shelf.id, {"name": "Keep", "description": "fresh"})
    assert db.session.get(Shelf, shelf.id).description == "fresh"


def test_update_shelf_rejects_other_user(alice, other_user):
    shelf = create_shelf(other_user, {"name": "not yours"})
    with pytest.raises(ShelfValidationError):
        update_shelf(alice, shelf.id, {"name": "mine now"})


def test_delete_shelf_cascades_membership_rows(alice):
    shelf = create_shelf(alice, {"name": "x"})
    add_book_to_shelf(alice, shelf.id, 3)
    add_book_to_shelf(alice, shelf.id, 4)
    assert ShelfBook.query.filter_by(shelf_id=shelf.id).count() == 2
    assert delete_shelf(alice, shelf.id) is True
    assert db.session.get(Shelf, shelf.id) is None
    assert ShelfBook.query.filter_by(shelf_id=shelf.id).count() == 0


def test_delete_shelf_rejects_other_user(alice, other_user):
    shelf = create_shelf(other_user, {"name": "x"})
    assert delete_shelf(alice, shelf.id) is False
    assert db.session.get(Shelf, shelf.id) is not None


# ── Service: membership ─────────────────────────────────────────────────────


def test_add_book_to_shelf_is_idempotent(alice):
    shelf = create_shelf(alice, {"name": "x"})
    first = add_book_to_shelf(alice, shelf.id, 3)
    second = add_book_to_shelf(alice, shelf.id, 3)
    assert first.id == second.id
    assert ShelfBook.query.filter_by(shelf_id=shelf.id).count() == 1


def test_add_book_assigns_incrementing_sort_order(alice):
    shelf = create_shelf(alice, {"name": "x"})
    a = add_book_to_shelf(alice, shelf.id, 3)
    b = add_book_to_shelf(alice, shelf.id, 4)
    c = add_book_to_shelf(alice, shelf.id, 5)
    assert (a.sort_order, b.sort_order, c.sort_order) == (1, 2, 3)


def test_book_can_belong_to_multiple_shelves(alice):
    a = create_shelf(alice, {"name": "A"})
    b = create_shelf(alice, {"name": "B"})
    add_book_to_shelf(alice, a.id, 3)
    add_book_to_shelf(alice, b.id, 3)
    assert ShelfBook.query.filter_by(calibre_book_id=3).count() == 2


def test_add_book_rejects_other_user_shelf(alice, other_user):
    shelf = create_shelf(other_user, {"name": "x"})
    with pytest.raises(ShelfValidationError):
        add_book_to_shelf(alice, shelf.id, 3)


def test_remove_book_from_shelf(alice):
    shelf = create_shelf(alice, {"name": "x"})
    add_book_to_shelf(alice, shelf.id, 3)
    assert remove_book_from_shelf(alice, shelf.id, 3) is True
    assert ShelfBook.query.filter_by(shelf_id=shelf.id).count() == 0
    assert remove_book_from_shelf(alice, shelf.id, 3) is False  # idempotent


def test_move_book_swaps_with_neighbour(alice):
    shelf = create_shelf(alice, {"name": "x"})
    add_book_to_shelf(alice, shelf.id, 3)
    add_book_to_shelf(alice, shelf.id, 4)
    add_book_to_shelf(alice, shelf.id, 5)
    # Initial order: 3, 4, 5.  Move book 5 up → 3, 5, 4.
    assert move_book_in_shelf(alice, shelf.id, 5, "up") is True
    rows = (
        ShelfBook.query.filter_by(shelf_id=shelf.id)
        .order_by(ShelfBook.sort_order.asc())
        .all()
    )
    assert [r.calibre_book_id for r in rows] == [3, 5, 4]


def test_move_book_at_top_with_up_is_noop(alice):
    shelf = create_shelf(alice, {"name": "x"})
    add_book_to_shelf(alice, shelf.id, 3)
    add_book_to_shelf(alice, shelf.id, 4)
    assert move_book_in_shelf(alice, shelf.id, 3, "up") is False


def test_move_book_at_bottom_with_down_is_noop(alice):
    shelf = create_shelf(alice, {"name": "x"})
    add_book_to_shelf(alice, shelf.id, 3)
    add_book_to_shelf(alice, shelf.id, 4)
    assert move_book_in_shelf(alice, shelf.id, 4, "down") is False


def test_move_book_rejects_bad_direction(alice):
    shelf = create_shelf(alice, {"name": "x"})
    add_book_to_shelf(alice, shelf.id, 3)
    with pytest.raises(ShelfValidationError):
        move_book_in_shelf(alice, shelf.id, 3, "sideways")


# ── Routes ──────────────────────────────────────────────────────────────────


def test_shelves_index_empty(alice_client):
    body = alice_client.get("/shelves").get_data(as_text=True)
    assert "No shelves yet" in body


def test_shelves_index_lists_shelves_with_counts(alice_client, alice):
    s = create_shelf(alice, {"name": "Beach reads"})
    add_book_to_shelf(alice, s.id, 3)
    add_book_to_shelf(alice, s.id, 4)
    body = alice_client.get("/shelves").get_data(as_text=True)
    assert "Beach reads" in body
    assert "2 books" in body


def test_shelf_new_renders_form(alice_client):
    body = alice_client.get("/shelves/new").get_data(as_text=True)
    assert 'name="name"' in body
    assert "Create a shelf" in body


def test_shelf_create_post(alice_client, alice):
    resp = alice_client.post(
        "/shelves/new",
        data={"name": "Hugo Winners", "description": "best of"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    shelf = Shelf.query.filter_by(user_id=alice.id).one()
    assert shelf.name == "Hugo Winners"
    # Redirects to the shelf detail page.
    assert f"/shelf/{shelf.id}" in resp.headers["Location"]


def test_shelf_create_rejects_duplicate_via_flash(alice_client, alice):
    create_shelf(alice, {"name": "Faves"})
    resp = alice_client.post(
        "/shelves/new", data={"name": "Faves"}, follow_redirects=True
    )
    body = resp.get_data(as_text=True)
    assert "already exists" in body.lower()


def test_shelf_detail_renders_books(alice_client, alice):
    s = create_shelf(alice, {"name": "Beach reads"})
    add_book_to_shelf(alice, s.id, 3)  # Piranesi
    body = alice_client.get(f"/shelf/{s.id}").get_data(as_text=True)
    assert "Beach reads" in body
    assert "Piranesi" in body


def test_shelf_detail_404_for_other_user(alice_client, other_user):
    shelf = create_shelf(other_user, {"name": "x"})
    assert alice_client.get(f"/shelf/{shelf.id}").status_code == 404


def test_shelf_edit_post_updates(alice_client, alice):
    shelf = create_shelf(alice, {"name": "old"})
    alice_client.post(
        f"/shelf/{shelf.id}/edit",
        data={"name": "new", "description": "updated"},
        follow_redirects=False,
    )
    fresh = db.session.get(Shelf, shelf.id)
    assert fresh.name == "new"
    assert fresh.description == "updated"


def test_shelf_delete_removes_row(alice_client, alice):
    shelf = create_shelf(alice, {"name": "x"})
    resp = alice_client.post(f"/shelf/{shelf.id}/delete", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert db.session.get(Shelf, shelf.id) is None


def test_book_add_to_shelf_route(alice_client, alice):
    shelf = create_shelf(alice, {"name": "x"})
    resp = alice_client.post(
        "/book/3/add-to-shelf",
        data={"shelf_id": str(shelf.id)},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert ShelfBook.query.filter_by(shelf_id=shelf.id, calibre_book_id=3).count() == 1


def test_book_add_to_shelf_rejects_other_user_shelf(alice_client, other_user):
    shelf = create_shelf(other_user, {"name": "x"})
    resp = alice_client.post(
        "/book/3/add-to-shelf",
        data={"shelf_id": str(shelf.id)},
        follow_redirects=True,
    )
    body = resp.get_data(as_text=True)
    assert "not found" in body.lower()
    assert ShelfBook.query.filter_by(shelf_id=shelf.id).count() == 0


def test_shelf_remove_book_via_route(alice_client, alice):
    shelf = create_shelf(alice, {"name": "x"})
    add_book_to_shelf(alice, shelf.id, 3)
    resp = alice_client.post(
        f"/shelf/{shelf.id}/remove-book",
        data={"book_id": "3"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert ShelfBook.query.filter_by(shelf_id=shelf.id).count() == 0


def test_shelf_move_book_via_route(alice_client, alice):
    shelf = create_shelf(alice, {"name": "x"})
    add_book_to_shelf(alice, shelf.id, 3)
    add_book_to_shelf(alice, shelf.id, 4)
    alice_client.post(
        f"/shelf/{shelf.id}/move-book",
        data={"book_id": "4", "direction": "up"},
    )
    rows = (
        ShelfBook.query.filter_by(shelf_id=shelf.id)
        .order_by(ShelfBook.sort_order.asc())
        .all()
    )
    assert [r.calibre_book_id for r in rows] == [4, 3]


def test_sidebar_links_to_shelves_index(alice_client):
    body = alice_client.get("/").get_data(as_text=True)
    assert "/shelves" in body


def test_book_detail_renders_add_to_shelf_dropdown(alice_client, alice):
    """The action bar shows the Add-to-shelf dropdown with each shelf."""
    create_shelf(alice, {"name": "Beach reads"})
    body = alice_client.get("/book/3").get_data(as_text=True)
    assert 'id="add-to-shelf"' in body
    assert "Beach reads" in body
    assert "Create a Shelf" in body  # always present in the dropdown


def test_book_detail_shows_current_shelves_in_metadata(alice_client, alice):
    """When a book is on shelves, they appear in the metadata row."""
    shelf = create_shelf(alice, {"name": "Beach reads"})
    add_book_to_shelf(alice, shelf.id, 3)
    body = alice_client.get("/book/3").get_data(as_text=True)
    assert "Shelves:" in body
    assert "Beach reads" in body


def test_sidebar_lists_user_shelves(alice_client, alice):
    """Each shelf gets its own sidebar link, plus a 'Create a Shelf' affordance."""
    s = create_shelf(alice, {"name": "Beach reads"})
    body = alice_client.get("/").get_data(as_text=True)
    assert "Beach reads" in body
    assert "Create a Shelf" in body
    assert f"/shelf/{s.id}" in body
