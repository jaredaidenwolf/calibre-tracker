"""Tracker database models.

Phase 2 lands a minimal :class:`User` model and a stub :class:`ReadingLog`
so the auth bridge has something concrete to talk to. Phase 3 extends
these with the rest of the tracker's tables, indexes, and proper Alembic
migrations.
"""

from __future__ import annotations

from datetime import datetime

from flask_login import UserMixin
from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db


class User(db.Model, UserMixin):
    """Tracker-side user record, paired 1:1 to a CWN ``user.id``.

    Phase 2 carries the fields the auth bridge needs to find/create a
    user. Phase 3 adds ``timezone`` and the relationships to
    ``ReadingLog``, ``Quote``, ``Note``, ``Shelf``, ``ReadingGoal``.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cwa_user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    cwn_import_completed: Mapped[bool] = mapped_column(
        db.Boolean, nullable=False, default=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    last_seen: Mapped[datetime | None] = mapped_column(DateTime)


class ReadingLog(db.Model):
    """Stub — full schema lands in Phase 3.

    Only the columns the Phase 2 import path writes are present, so
    :func:`app.auth.cwa_bridge.import_cwn_read_status` compiles and
    its tests can pass. Phase 3 will add the rest (dates, rating,
    review, reread flags, indexes).
    """

    __tablename__ = "reading_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    calibre_book_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="want_to_read")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
