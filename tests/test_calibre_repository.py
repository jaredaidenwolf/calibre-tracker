"""Phase 1 — read-only Calibre repository tests.

Exercises every public function in :mod:`app.calibre.repository` against
the small fixture database under ``tests/fixtures/calibre-library``.
"""

from __future__ import annotations

import os
import sqlite3 as _sqlite3
from pathlib import Path

import pytest

from app.calibre import models as calibre_models
from app.calibre.repository import (
    BookDTO,
    get_book,
    get_books,
    get_cover_path,
    search_books,
)


def test_get_book_returns_title_and_authors(app_context):
    book = get_book(1)
    assert isinstance(book, BookDTO)
    assert book.id == 1
    assert book.title == "The Long Way to a Small, Angry Planet"
    assert book.authors == ("Becky Chambers",)
    assert book.series == "Wayfarers"
    assert book.series_index == 1.0
    assert "Science Fiction" in book.tags
    assert book.has_cover is True


def test_get_book_handles_multi_author(app_context):
    book = get_book(5)
    assert book is not None
    assert set(book.authors) == {"Terry Pratchett", "Neil Gaiman"}
    assert book.has_cover is False
    assert book.cover_path is None


def test_get_book_returns_none_for_missing(app_context):
    assert get_book(99999) is None


def test_get_books_preserves_input_order(app_context):
    books = get_books([3, 1, 6])
    assert [b.id for b in books] == [3, 1, 6]


def test_get_books_silently_drops_missing_ids(app_context):
    books = get_books([1, 99999, 2])
    assert [b.id for b in books] == [1, 2]


def test_search_books_finds_known_title(app_context):
    results = search_books("piranesi")
    assert any(b.title == "Piranesi" for b in results)


def test_search_books_finds_by_author(app_context):
    results = search_books("Chambers")
    titles = {b.title for b in results}
    assert "The Long Way to a Small, Angry Planet" in titles
    assert "A Closed and Common Orbit" in titles


def test_search_books_empty_query_returns_empty(app_context):
    assert search_books("") == []
    assert search_books("   ") == []


def test_search_books_respects_limit(app_context):
    results = search_books("the", limit=1)
    assert len(results) == 1


def test_get_cover_path_resolves_existing_file(app_context):
    cover = get_cover_path(1)
    assert cover is not None
    assert os.path.isfile(cover)
    assert cover.endswith("cover.jpg")


def test_get_cover_path_returns_none_when_no_cover(app_context):
    # Book 5 ("Good Omens") was inserted with has_cover=0.
    assert get_cover_path(5) is None


def test_get_cover_path_returns_none_when_book_missing(app_context):
    assert get_cover_path(99999) is None


def test_get_cover_path_returns_none_when_file_missing(app_context, tmp_path):
    # Book 6 has has_cover=1, but if the on-disk file is removed
    # the repository must report None rather than a bad path.
    book = get_book(6)
    assert book is not None
    assert book.cover_path is not None
    backup = Path(book.cover_path).with_suffix(".jpg.bak")
    Path(book.cover_path).rename(backup)
    try:
        assert get_cover_path(6) is None
    finally:
        backup.rename(book.cover_path)


def test_writes_against_metadata_db_are_rejected(app_context):
    """The SQLite layer must reject any write attempt."""
    engine = calibre_models.get_engine()
    with engine.connect() as conn, pytest.raises(Exception) as exc_info:
        conn.exec_driver_sql("UPDATE books SET title = 'mutated' WHERE id = 1")

    msg = str(exc_info.value).lower()
    assert "readonly" in msg or "read-only" in msg or "read only" in msg


def test_raw_sqlite_write_attempt_is_rejected():
    """Belt-and-suspenders: even a direct sqlite3 connection in mode=ro fails."""
    db_path = Path(__file__).parent / "fixtures" / "calibre-library" / "metadata.db"
    conn = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        with pytest.raises(_sqlite3.OperationalError) as exc:
            conn.execute("UPDATE books SET title = 'mutated' WHERE id = 1")
        assert "readonly" in str(exc.value).lower() or "read-only" in str(exc.value).lower()
    finally:
        conn.close()
