"""Read-only SQLAlchemy mapping of Calibre's ``metadata.db``.

These models are **not** registered with Flask-Migrate — the tracker never
issues a migration or write against Calibre's database. The connection is
opened in SQLite read-only URI mode (``mode=ro``), so even a buggy write
would be rejected at the SQLite layer.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock

from flask import current_app
from sqlalchemy import Column, Float, ForeignKey, Integer, Table, Text, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

_engine_cache: dict[str, Engine] = {}
_engine_lock = Lock()


class CalibreBase(DeclarativeBase):
    """Declarative base for Calibre tables (separate from the tracker's base)."""


books_authors_link = Table(
    "books_authors_link",
    CalibreBase.metadata,
    Column("id", Integer, primary_key=True),
    Column("book", Integer, ForeignKey("books.id"), nullable=False),
    Column("author", Integer, ForeignKey("authors.id"), nullable=False),
)

books_tags_link = Table(
    "books_tags_link",
    CalibreBase.metadata,
    Column("id", Integer, primary_key=True),
    Column("book", Integer, ForeignKey("books.id"), nullable=False),
    Column("tag", Integer, ForeignKey("tags.id"), nullable=False),
)

books_series_link = Table(
    "books_series_link",
    CalibreBase.metadata,
    Column("id", Integer, primary_key=True),
    Column("book", Integer, ForeignKey("books.id"), nullable=False),
    Column("series", Integer, ForeignKey("series.id"), nullable=False),
)


class CalibreBook(CalibreBase):
    """A row in Calibre's ``books`` table."""

    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    sort: Mapped[str | None] = mapped_column(Text)
    pubdate: Mapped[str | None] = mapped_column(Text)
    series_index: Mapped[float | None] = mapped_column(Float)
    isbn: Mapped[str | None] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    has_cover: Mapped[int] = mapped_column(Integer, default=0)

    authors: Mapped[list[CalibreAuthor]] = relationship(
        secondary=books_authors_link, viewonly=True, order_by="CalibreAuthor.sort"
    )
    tags: Mapped[list[CalibreTag]] = relationship(
        secondary=books_tags_link, viewonly=True, order_by="CalibreTag.name"
    )
    series: Mapped[list[CalibreSeries]] = relationship(secondary=books_series_link, viewonly=True)


class CalibreAuthor(CalibreBase):
    """A row in Calibre's ``authors`` table."""

    __tablename__ = "authors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sort: Mapped[str | None] = mapped_column(Text)


class CalibreTag(CalibreBase):
    """A row in Calibre's ``tags`` table."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)


class CalibreSeries(CalibreBase):
    """A row in Calibre's ``series`` table."""

    __tablename__ = "series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sort: Mapped[str | None] = mapped_column(Text)


def _make_engine(db_path: str) -> Engine:
    """Build a SQLAlchemy engine that can never write to ``db_path``.

    The engine uses a ``creator`` that opens the SQLite file via the URI
    ``file:{path}?mode=ro``. Any attempt to write fails with
    ``sqlite3.OperationalError: attempt to write a readonly database``.
    """

    def _connect() -> sqlite3.Connection:
        return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    return create_engine(
        "sqlite://",
        creator=_connect,
        future=True,
    )


def get_engine() -> Engine:
    """Return (and cache) the read-only engine for the configured Calibre DB."""
    db_path = current_app.config["CALIBRE_DB_PATH"]
    with _engine_lock:
        engine = _engine_cache.get(db_path)
        if engine is None:
            engine = _make_engine(db_path)
            _engine_cache[db_path] = engine
        return engine


def reset_engine_cache() -> None:
    """Drop cached engines — used between tests."""
    with _engine_lock:
        for engine in _engine_cache.values():
            engine.dispose()
        _engine_cache.clear()


@contextmanager
def read_session() -> Iterator[Session]:
    """Yield a SQLAlchemy session bound to the read-only Calibre engine.

    The session never auto-flushes and is never committed. Callers should
    treat the returned ORM objects as read-only DTOs.
    """
    factory = sessionmaker(
        bind=get_engine(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    session = factory()
    try:
        yield session
    finally:
        session.close()
