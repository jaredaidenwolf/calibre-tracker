"""High-level read access to Calibre's metadata.

The repository returns :class:`BookDTO` instances — plain dataclasses —
rather than ORM objects, so callers don't accidentally hold on to a
detached session and so the rest of the codebase doesn't need to know
SQLAlchemy syntax.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from flask import current_app
from sqlalchemy import or_, select
from sqlalchemy.orm import joinedload

from .models import CalibreAuthor, CalibreBook, read_session


@dataclass(frozen=True)
class BookDTO:
    """A read-only snapshot of a Calibre book + its lookups."""

    id: int
    title: str
    sort: str | None
    authors: tuple[str, ...]
    series: str | None
    series_index: float | None
    tags: tuple[str, ...]
    isbn: str | None
    pubdate: str | None
    path: str
    has_cover: bool
    cover_path: str | None
    extra: dict[str, object] = field(default_factory=dict)


def _to_dto(book: CalibreBook, library_root: str) -> BookDTO:
    """Convert a Calibre ORM book row into a :class:`BookDTO`."""
    series_name: str | None = None
    if book.series:
        series_name = book.series[0].name

    cover_path: str | None = None
    if book.has_cover:
        cover_path = str(Path(library_root) / book.path / "cover.jpg")

    return BookDTO(
        id=book.id,
        title=book.title,
        sort=book.sort,
        authors=tuple(a.name for a in book.authors),
        series=series_name,
        series_index=book.series_index if series_name else None,
        tags=tuple(t.name for t in book.tags),
        isbn=book.isbn or None,
        pubdate=book.pubdate,
        path=book.path,
        has_cover=bool(book.has_cover),
        cover_path=cover_path,
    )


def _library_root() -> str:
    """The on-disk root of the Calibre library (parent of metadata.db)."""
    configured = current_app.config.get("CALIBRE_LIBRARY_PATH")
    if configured:
        return configured
    db_path = current_app.config["CALIBRE_DB_PATH"]
    return str(Path(db_path).parent)


def get_book(book_id: int) -> BookDTO | None:
    """Return metadata for a single Calibre book, or ``None`` if missing."""
    with read_session() as session:
        book = session.get(
            CalibreBook,
            book_id,
            options=(
                joinedload(CalibreBook.authors),
                joinedload(CalibreBook.tags),
                joinedload(CalibreBook.series),
            ),
        )
        if book is None:
            return None
        return _to_dto(book, _library_root())


def get_books(book_ids: Iterable[int]) -> list[BookDTO]:
    """Return DTOs for every requested ID that exists, preserving input order.

    Missing IDs are silently dropped — callers should compare lengths if they
    need to know which IDs were unknown.
    """
    ids = list(dict.fromkeys(int(i) for i in book_ids))
    if not ids:
        return []
    with read_session() as session:
        stmt = (
            select(CalibreBook)
            .where(CalibreBook.id.in_(ids))
            .options(
                joinedload(CalibreBook.authors),
                joinedload(CalibreBook.tags),
                joinedload(CalibreBook.series),
            )
        )
        rows = session.execute(stmt).unique().scalars().all()
        library_root = _library_root()
        by_id = {row.id: _to_dto(row, library_root) for row in rows}
    return [by_id[i] for i in ids if i in by_id]


def search_books(query: str, limit: int = 50) -> list[BookDTO]:
    """Case-insensitive title/author search, ordered by title.

    A bare substring search is enough for the tracker's needs: Calibre's
    own full-text search lives elsewhere, and the tracker only needs to
    let a user find a book they want to log.
    """
    needle = (query or "").strip()
    if not needle:
        return []
    pattern = f"%{needle}%"
    with read_session() as session:
        stmt = (
            select(CalibreBook)
            .where(
                or_(
                    CalibreBook.title.ilike(pattern),
                    CalibreBook.sort.ilike(pattern),
                    CalibreBook.isbn.ilike(pattern),
                    CalibreBook.authors.any(CalibreAuthor.name.ilike(pattern)),
                )
            )
            .options(
                joinedload(CalibreBook.authors),
                joinedload(CalibreBook.tags),
                joinedload(CalibreBook.series),
            )
            .order_by(CalibreBook.sort, CalibreBook.title)
            .limit(max(1, int(limit)))
        )
        rows = session.execute(stmt).unique().scalars().all()
        library_root = _library_root()
        return [_to_dto(row, library_root) for row in rows]


def get_cover_path(book_id: int) -> str | None:
    """Return the absolute on-disk cover path for ``book_id``, if any.

    Returns ``None`` when the book is missing, has no cover, or the file
    does not exist on disk.
    """
    with read_session() as session:
        book = session.get(CalibreBook, book_id)
        if book is None or not book.has_cover:
            return None
        path = Path(_library_root()) / book.path / "cover.jpg"
    if not os.path.isfile(path):
        return None
    return str(path)
