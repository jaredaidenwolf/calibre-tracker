"""Tracker database models.

All tables live in ``tracker.db`` (the only writable database). Every
``calibre_book_id`` column is a plain integer — Calibre lives in a
separate SQLite file so no SQL-level FK constraint is possible; the
soft FK is enforced in application code.

The full schema and ERDs live in ``docs/01-data-model.md``.
"""

from __future__ import annotations

from datetime import datetime

from flask_login import UserMixin
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db

# ── Status / type vocabularies ──────────────────────────────────────────────

#: Allowed values for :attr:`ReadingLog.status`.
READING_STATUSES: frozenset[str] = frozenset(
    {"want_to_read", "reading", "read", "dnf", "re_reading"}
)

#: Allowed values for :attr:`Note.note_type`.
NOTE_TYPES: frozenset[str] = frozenset({"general", "character", "plot", "theme", "reaction"})


# ── Users ───────────────────────────────────────────────────────────────────


class User(db.Model, UserMixin):
    """Tracker-side user, paired 1:1 to a CWN ``user.id`` via :attr:`cwa_user_id`."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cwa_user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="UTC", server_default="UTC"
    )
    cwn_import_completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    last_seen: Mapped[datetime | None] = mapped_column(DateTime)

    reading_logs: Mapped[list[ReadingLog]] = relationship(
        "ReadingLog", back_populates="user", cascade="all, delete-orphan"
    )
    quotes: Mapped[list[Quote]] = relationship(
        "Quote", back_populates="user", cascade="all, delete-orphan"
    )
    notes: Mapped[list[Note]] = relationship(
        "Note", back_populates="user", cascade="all, delete-orphan"
    )
    shelves: Mapped[list[Shelf]] = relationship(
        "Shelf", back_populates="user", cascade="all, delete-orphan"
    )
    reading_goals: Mapped[list[ReadingGoal]] = relationship(
        "ReadingGoal", back_populates="user", cascade="all, delete-orphan"
    )


# ── Reading log ─────────────────────────────────────────────────────────────


class ReadingLog(db.Model):
    """One row per user / book / read-attempt.

    Rereads are *new* rows with :attr:`is_reread` = True rather than
    overwrites of the original — see ``docs/01-data-model.md`` for the
    rationale.
    """

    __tablename__ = "reading_log"
    __table_args__ = (
        Index("idx_reading_log_user", "user_id"),
        Index("idx_reading_log_book", "calibre_book_id"),
        Index("idx_reading_log_status", "user_id", "status"),
    )

    #: Mirror of :data:`READING_STATUSES` so route code can do
    #: ``status in ReadingLog.STATUSES``.
    STATUSES = READING_STATUSES

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    calibre_book_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="want_to_read")
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    rating: Mapped[int | None] = mapped_column(Integer)  # 1–10
    review: Mapped[str | None] = mapped_column(Text)
    is_reread: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    reread_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    user: Mapped[User] = relationship("User", back_populates="reading_logs")
    sessions: Mapped[list[ReadingSession]] = relationship(
        "ReadingSession",
        back_populates="reading_log",
        cascade="all, delete-orphan",
    )

    @classmethod
    def current_status_for(cls, user_id: int, calibre_book_id: int) -> str | None:
        """Return the user's current status for a book.

        "Current" = the most-recently-updated non-reread row. Rereads
        are explicitly excluded so they don't mask the original read.
        Returns ``None`` when the user has no log entry for the book.
        """
        row = (
            db.session.query(cls)
            .filter_by(user_id=user_id, calibre_book_id=calibre_book_id, is_reread=False)
            .order_by(cls.updated_at.desc())
            .first()
        )
        return row.status if row else None


# ── Reading sessions (optional per-sitting tracking) ────────────────────────


class ReadingSession(db.Model):
    """One row per reading sitting; optional, attaches to a :class:`ReadingLog`."""

    __tablename__ = "reading_sessions"
    __table_args__ = (Index("idx_sessions_log", "reading_log_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reading_log_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reading_log.id"), nullable=False
    )
    session_date: Mapped[datetime | None] = mapped_column(DateTime)
    pages_start: Mapped[int | None] = mapped_column(Integer)
    pages_end: Mapped[int | None] = mapped_column(Integer)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    reading_log: Mapped[ReadingLog] = relationship("ReadingLog", back_populates="sessions")


# ── Quotes & notes ──────────────────────────────────────────────────────────


class Quote(db.Model):
    """Verbatim text the user saved from a book."""

    __tablename__ = "quotes"
    __table_args__ = (Index("idx_quotes_user_book", "user_id", "calibre_book_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    calibre_book_id: Mapped[int] = mapped_column(Integer, nullable=False)
    quote_text: Mapped[str] = mapped_column(Text, nullable=False)
    page_reference: Mapped[str | None] = mapped_column(String(64))
    chapter_reference: Mapped[str | None] = mapped_column(String(128))
    context_note: Mapped[str | None] = mapped_column(Text)
    is_favourite: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    user: Mapped[User] = relationship("User", back_populates="quotes")


class Note(db.Model):
    """The user's own thoughts on a book (as opposed to :class:`Quote`)."""

    __tablename__ = "notes"
    __table_args__ = (Index("idx_notes_user_book", "user_id", "calibre_book_id"),)

    #: Mirror of :data:`NOTE_TYPES` for in-route validation.
    NOTE_TYPES = NOTE_TYPES

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    calibre_book_id: Mapped[int] = mapped_column(Integer, nullable=False)
    note_text: Mapped[str] = mapped_column(Text, nullable=False)
    note_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="general", server_default="general"
    )
    page_reference: Mapped[str | None] = mapped_column(String(64))
    is_spoiler: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    user: Mapped[User] = relationship("User", back_populates="notes")


# ── Shelves ─────────────────────────────────────────────────────────────────


class Shelf(db.Model):
    """User-defined collection beyond the core ``reading_log.status`` set."""

    __tablename__ = "shelves"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_shelves_user_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    color_hex: Mapped[str | None] = mapped_column(String(7))
    is_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    user: Mapped[User] = relationship("User", back_populates="shelves")
    books: Mapped[list[ShelfBook]] = relationship(
        "ShelfBook",
        back_populates="shelf",
        cascade="all, delete-orphan",
        order_by="ShelfBook.sort_order",
    )


class ShelfBook(db.Model):
    """Membership row joining a :class:`Shelf` to a Calibre book."""

    __tablename__ = "shelf_books"
    __table_args__ = (
        Index("idx_shelf_books", "shelf_id", "calibre_book_id"),
        UniqueConstraint("shelf_id", "calibre_book_id", name="uq_shelf_books_shelf_book"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shelf_id: Mapped[int] = mapped_column(Integer, ForeignKey("shelves.id"), nullable=False)
    calibre_book_id: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    added_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    shelf: Mapped[Shelf] = relationship("Shelf", back_populates="books")


# ── Reading goals ───────────────────────────────────────────────────────────


class ReadingGoal(db.Model):
    """Annual reading targets — one row per user per year."""

    __tablename__ = "reading_goals"
    __table_args__ = (UniqueConstraint("user_id", "year", name="uq_reading_goals_user_year"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    target_books: Mapped[int | None] = mapped_column(Integer)
    target_pages: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    user: Mapped[User] = relationship("User", back_populates="reading_goals")
